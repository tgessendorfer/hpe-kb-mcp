# Copyright 2026 Thomas Gessendorfer.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""HTTP client for the HPE Support Center document API.

The public documentation portal at ``support.hpe.com/hpesc/public/docDisplay``
is a JavaScript viewer: fetching that URL returns roughly 9 KB of navigation
chrome and no document text, which is why an LLM's own web-fetch tool comes
back empty from it. The viewer is fed by a plain, unauthenticated JSON/HTML
API underneath, and that is what this module talks to:

======================================================  =========================
``GET /hpesc/public/api/document/{docId}``               whole document, or the
                                                         front matter when the
                                                         document is multi-page
``GET /hpesc/public/api/document/{docId}/toc``           topic list, each with a
                                                         render link
``GET /hpesc/public/api/document/{docId}/render?page=``   one topic, as JSON with
                                                         a ``page_html`` field
``GET /hpesc/public/api/document/{docId}/search?q=``      requires a signed-in
                                                         HPE account
======================================================  =========================

Which shape a document uses is announced in the ``Content-Type`` header:
``htmlzip`` for a single-page document, ``multiPage`` for one split into topics.
"""

from __future__ import annotations

import gzip
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

BASE_URL = "https://support.hpe.com"
API_PATH = "/hpesc/public/api/document"
USER_AGENT = "hpe-kb-mcp/0.1 (+internal documentation retrieval for an MCP server)"

# Documents whose content type is this are split into topics; anything else
# comes back whole.
MULTI_PAGE_CONTENT_TYPE = "multipage"


class HpeKbError(RuntimeError):
    """A request to the HPE documentation API did not succeed."""


class AuthenticationRequired(HpeKbError):
    """The endpoint exists but needs a signed-in HPE account."""


@dataclass
class Topic:
    name: str
    page: str | None
    description: str | None = None

    def as_dict(self) -> dict:
        return {"topic": self.name, "page": self.page, "description": self.description}


@dataclass
class Document:
    doc_id: str
    title: str | None
    text: str
    multi_page: bool
    topics: list[Topic] = field(default_factory=list)
    truncated: bool = False
    source_url: str = ""


def normalise_doc_id(value: str) -> str:
    """Accept a bare docId or any URL that carries one.

    People paste the address bar, not the identifier, and every HPE surface
    spells it differently - ``docDisplay?docId=``, an API path, a sitemap entry.
    """
    if not value or not value.strip():
        raise ValueError("A document id or URL is required")
    candidate = value.strip()

    if "://" in candidate or candidate.startswith("/"):
        parsed = urllib.parse.urlparse(candidate)
        query = urllib.parse.parse_qs(parsed.query)
        if "docId" in query and query["docId"]:
            candidate = query["docId"][0]
        else:
            # .../api/document/<docId>[/toc|/render]
            match = re.search(r"/document/([A-Za-z0-9_]+)", parsed.path)
            if not match:
                raise ValueError(f"No document id found in URL: {value}")
            candidate = match.group(1)

    if not re.fullmatch(r"[A-Za-z0-9_]+", candidate):
        raise ValueError(f"Not a valid document id: {value}")
    return candidate


def html_to_text(html: str) -> str:
    """Flatten the DITA-rendered HTML the API returns into readable text.

    Deliberately dependency-free and deliberately dumb: these documents are
    generated, structurally boring, and only ever consumed as prose by a model.
    """
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    # Keep the block structure that carries meaning in release notes.
    text = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h[1-6])\s*/?>", "\n", text)
    text = re.sub(r"(?i)<li\b[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)<h([1-6])\b[^>]*>", lambda m: "\n\n" + "#" * int(m.group(1)) + " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n[ \n]*\n+", "\n\n", text)
    return text.strip()


class HpeKbClient:
    """Polite, cached client for the public HPE document API.

    ``session_cookie`` is optional. Everything this server needs for public
    product documentation works without it; supply one only to reach the
    search endpoint or documents gated behind an entitlement.
    """

    def __init__(
        self,
        base_url: str = BASE_URL,
        session_cookie: str | None = None,
        cache_ttl_seconds: float = 900.0,
        min_request_interval_seconds: float = 0.5,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session_cookie = session_cookie
        self.cache_ttl_seconds = cache_ttl_seconds
        self.min_request_interval_seconds = min_request_interval_seconds
        self.timeout_seconds = timeout_seconds
        self._cache: dict[str, tuple[float, tuple[bytes, str]]] = {}
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    # -- transport ---------------------------------------------------------

    def _get(self, url: str) -> tuple[bytes, str]:
        """Return (body, content_type), served from cache when still fresh."""
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(url)
            if cached and (now - cached[0]) < self.cache_ttl_seconds:
                return cached[1]
            # One shared throttle: an agent can fan out, HPE's portal should not
            # notice. Held under the lock on purpose so it actually serialises.
            wait = self.min_request_interval_seconds - (now - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.monotonic()

        headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9"}
        if self.session_cookie:
            headers["Cookie"] = self.session_cookie
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code in (401, 403) or "sign in" in detail.lower():
                raise AuthenticationRequired(
                    f"HPE returned {exc.code} for {url}. This document or endpoint needs a "
                    f"signed-in HPE account - set HPE_SESSION_COOKIE. Detail: {detail}"
                ) from exc
            raise HpeKbError(f"HPE returned {exc.code} for {url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise HpeKbError(f"Could not reach {url}: {exc.reason}") from exc

        # The search endpoint answers 200 with a sign-in notice rather than 401.
        if b"sign in to the HPE Support Center" in body[:400]:
            raise AuthenticationRequired(
                "HPE answered with a sign-in notice. This endpoint needs a signed-in "
                "HPE account - set HPE_SESSION_COOKIE."
            )

        result = (body, content_type)
        with self._lock:
            self._cache[url] = (time.monotonic(), result)
        return result

    def _get_json(self, url: str):
        body, _ = self._get(url)
        try:
            return json.loads(body.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise HpeKbError(f"Expected JSON from {url}: {exc}") from exc

    # -- API surface -------------------------------------------------------

    def document_url(self, doc_id: str) -> str:
        return f"{self.base_url}{API_PATH}/{doc_id}"

    def viewer_url(self, doc_id: str, page: str | None = None) -> str:
        """The human-facing URL, for citing back to whoever asked."""
        url = f"{self.base_url}/hpesc/public/docDisplay?docId={doc_id}"
        return f"{url}&page={page}" if page else url

    def get_topics(self, doc_id: str) -> list[Topic]:
        payload = self._get_json(f"{self.document_url(doc_id)}/toc")
        if not isinstance(payload, list):
            return []
        topics: list[Topic] = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            link = entry.get("topicLink") or ""
            page = None
            if "page=" in link:
                page = link.split("page=", 1)[1].split("&", 1)[0]
            topics.append(Topic(
                name=str(entry.get("topicName") or "").strip() or "(untitled)",
                page=page,
                description=entry.get("description"),
            ))
        return topics

    def render_page(self, doc_id: str, page: str) -> str:
        url = f"{self.document_url(doc_id)}/render?page={urllib.parse.quote(page)}"
        payload = self._get_json(url)
        if isinstance(payload, dict):
            return html_to_text(payload.get("page_html") or "")
        raise HpeKbError(f"Unexpected render payload for {doc_id} page {page}")

    def get_document(
        self,
        doc_id: str,
        page: str | None = None,
        max_chars: int = 40000,
        include_all_topics: bool = False,
    ) -> Document:
        """Fetch a document, or one topic of it.

        A multi-page document returns only its front matter from the document
        endpoint - notices, trademarks, and nothing anyone asked for - so the
        topics are fetched and, unless a single page was requested, joined.
        """
        doc_id = normalise_doc_id(doc_id)

        if page:
            text = self.render_page(doc_id, page)
            return Document(
                doc_id=doc_id, title=None, text=_truncate(text, max_chars)[0],
                multi_page=True, truncated=len(text) > max_chars,
                source_url=self.viewer_url(doc_id, page),
            )

        body, content_type = self._get(self.document_url(doc_id))
        raw_html = body.decode("utf-8", "replace")
        multi_page = content_type.lower() == MULTI_PAGE_CONTENT_TYPE
        title = _extract_title(raw_html)

        if not multi_page:
            text, truncated = _truncate(html_to_text(raw_html), max_chars)
            return Document(doc_id=doc_id, title=title, text=text, multi_page=False,
                            truncated=truncated, source_url=self.viewer_url(doc_id))

        topics = self.get_topics(doc_id)
        # The front matter of a multi-page document often has no usable heading;
        # its first topic is the document's real title ("v9.0.2 LTS Release Notes").
        if not title and topics:
            title = topics[0].name
        if not include_all_topics:
            # The front matter is legal boilerplate; the topic list is the useful
            # answer, and the caller picks what to read next.
            return Document(doc_id=doc_id, title=title, text="", multi_page=True,
                            topics=topics, source_url=self.viewer_url(doc_id))

        parts: list[str] = []
        for topic in topics:
            if not topic.page:
                continue
            parts.append(f"## {topic.name}\n\n{self.render_page(doc_id, topic.page)}")
            if sum(len(p) for p in parts) >= max_chars:
                break
        text, truncated = _truncate("\n\n".join(parts), max_chars)
        return Document(doc_id=doc_id, title=title, text=text, multi_page=True,
                        topics=topics, truncated=truncated,
                        source_url=self.viewer_url(doc_id))

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        """Search the knowledgebase. Needs a signed-in HPE account.

        Without a cookie this raises AuthenticationRequired, which is the
        honest answer: HPE gates search behind a login even though it serves
        the documents themselves anonymously.
        """
        url = (f"{self.base_url}{API_PATH}/search"
               f"?q={urllib.parse.quote(query)}&size={int(max_results)}")
        payload = self._get_json(url)
        results = payload.get("results", payload) if isinstance(payload, dict) else payload
        if not isinstance(results, list):
            return []
        out = []
        for entry in results[:max_results]:
            if not isinstance(entry, dict):
                continue
            doc_id = entry.get("docId") or entry.get("documentId")
            out.append({
                "doc_id": doc_id,
                "title": entry.get("title") or entry.get("documentTitle"),
                "url": self.viewer_url(doc_id) if doc_id else entry.get("url"),
            })
        return out


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip() + "\n\n[truncated]", True


def _extract_title(html: str) -> str | None:
    match = re.search(r'(?is)<h1[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</h1>', html)
    if not match:
        match = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", html)
    if not match:
        return None
    return html_to_text(match.group(1)) or None

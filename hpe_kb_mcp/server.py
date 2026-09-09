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

"""MCP server exposing the HPE Support Center knowledgebase.

Registered under *Tools > AI Services > MCP Servers* in HPE Morpheus and
attached to an Agent alongside the built-in Morpheus server, this gives the
agent something neither a model's own web search nor its web fetch can do:
read the actual text of an HPE document.
"""

from __future__ import annotations

import argparse
import os

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__
from .client import AuthenticationRequired, HpeKbClient, HpeKbError, normalise_doc_id
from .releases import latest_release

# Every tool here only reads. Morpheus agents in read-only mode refuse to execute
# an external tool that does not say so - the tool loads, then fails at call time
# with "Write operations are disabled for this agent" - so declare it explicitly.
# `open_world_hint` because these reach support.hpe.com rather than a closed set.
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)

mcp = MCPServer(
    name="hpe-kb",
    version=__version__,
    instructions=(
        "Reads documents from the HPE Support Center knowledgebase.\n\n"
        "Use these tools instead of fetching support.hpe.com URLs directly: the "
        "docDisplay viewer is JavaScript-rendered, so a plain web fetch of one of "
        "those URLs returns an empty page with no document text.\n\n"
        "A good pattern for questions about HPE products: find the document with web "
        "search (HPE documents are indexed publicly and results carry a docId), then "
        "read it here. For what is actually installed on the appliance, use the "
        "Morpheus tools - this server only knows what HPE has published."
    ),
)

_client: HpeKbClient | None = None


def client() -> HpeKbClient:
    global _client
    if _client is None:
        _client = HpeKbClient(
            session_cookie=os.environ.get("HPE_SESSION_COOKIE") or None,
            cache_ttl_seconds=float(os.environ.get("HPE_KB_CACHE_TTL", "900")),
            min_request_interval_seconds=float(os.environ.get("HPE_KB_MIN_INTERVAL", "0.5")),
        )
    return _client


@mcp.tool(annotations=READ_ONLY)
def get_hpe_document(
    document: str,
    page: str | None = None,
    include_all_topics: bool = False,
    max_chars: int = 40000,
) -> dict:
    """Read the full text of an HPE Support Center document.

    Use this for any support.hpe.com document - release notes, advisories,
    customer notices, product documentation. Fetching a
    `support.hpe.com/hpesc/public/docDisplay?docId=...` URL with a normal web
    fetch returns an empty JavaScript shell with no document text; this tool
    reads the API underneath it and returns the real content.

    `document` accepts either a bare document id (`dp00008463en_us`) or any
    URL containing one, so a link pasted from a browser works as-is.

    Large documents are split into topics. Called without `page`, this returns
    the topic list so you can pick one; call it again with that topic's `page`
    value to read it, or pass `include_all_topics=true` to read the whole
    document at once.
    """
    try:
        doc = client().get_document(document, page=page,
                                    include_all_topics=include_all_topics,
                                    max_chars=max_chars)
    except AuthenticationRequired as exc:
        return {"error": "authentication_required", "detail": str(exc)}
    except (HpeKbError, ValueError) as exc:
        return {"error": "unavailable", "detail": str(exc)}

    result = {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "url": doc.source_url,
        "multi_page": doc.multi_page,
        "truncated": doc.truncated,
        "text": doc.text,
    }
    if doc.multi_page and not doc.text:
        result["topics"] = [t.as_dict() for t in doc.topics]
        result["note"] = ("This document is split into topics and only its front matter "
                          "is on the main page. Call this tool again with one of the "
                          "`page` values above, or include_all_topics=true.")
    return result


@mcp.tool(annotations=READ_ONLY)
def list_hpe_document_topics(document: str) -> dict:
    """List the topics (table of contents) of an HPE document.

    Cheaper than reading a whole document when you only need one section -
    "Fixes" or "New features" of a release notes document, for instance.
    Accepts a document id or a URL containing one.
    """
    try:
        doc_id = normalise_doc_id(document)
        topics = client().get_topics(doc_id)
    except AuthenticationRequired as exc:
        return {"error": "authentication_required", "detail": str(exc)}
    except (HpeKbError, ValueError) as exc:
        return {"error": "unavailable", "detail": str(exc)}
    return {
        "doc_id": doc_id,
        "url": client().viewer_url(doc_id),
        "topics": [t.as_dict() for t in topics],
    }


@mcp.tool(annotations=READ_ONLY)
def search_hpe_kb(query: str, max_results: int = 10) -> dict:
    """Search the HPE Support Center knowledgebase by keyword.

    Requires a signed-in HPE account: HPE serves documents anonymously but
    gates search behind a login. If this returns `authentication_required`,
    find the document with your own web search instead - HPE documents are
    indexed publicly, and search results carry the `docId=...` you need - then
    read it properly with `get_hpe_document`.
    """
    try:
        results = client().search(query, max_results=max_results)
    except AuthenticationRequired as exc:
        return {
            "error": "authentication_required",
            "detail": str(exc),
            "suggestion": ("Use your own web search restricted to support.hpe.com to find "
                           "the docId, then call get_hpe_document with it."),
        }
    except (HpeKbError, ValueError) as exc:
        return {"error": "unavailable", "detail": str(exc)}
    return {"query": query, "results": results}


@mcp.tool(annotations=READ_ONLY)
def get_latest_morpheus_release(product: str = "morpheus-enterprise") -> dict:
    """Report the newest HPE Morpheus release this server knows about.

    Compares the release-notes documents in the server's configured list and
    returns the highest version, with its publication month and document link.

    Important: this reflects the documents the server has been told about, not
    a live query of everything HPE has published - HPE has no anonymous search
    API. If you have reason to think a newer release exists, find its release
    notes with your own web search and read it with `get_hpe_document`; the
    appliance's own build version comes from the Morpheus tools, not this one.

    `product` is `morpheus-enterprise` or `morpheus-vm-essentials`.
    """
    newest, known, problems = latest_release(client(), product)
    if not newest:
        return {
            "error": "unavailable",
            "detail": f"No release notes documents could be read for {product}.",
            "problems": problems,
        }
    return {
        "product": product,
        "latest": newest.as_dict(),
        "known_releases": [r.as_dict() for r in known],
        "problems": problems,
        "caveat": ("Reflects this server's configured document list, not a live search "
                   "of the HPE catalog."),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="HPE knowledgebase MCP server")
    parser.add_argument("--transport", choices=["streamable-http", "sse", "stdio"],
                        default=os.environ.get("HPE_KB_TRANSPORT", "streamable-http"),
                        help="stdio for local testing; streamable-http for Morpheus")
    parser.add_argument("--host", default=os.environ.get("HPE_KB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("HPE_KB_PORT", "8081")))
    parser.add_argument("--path", default=os.environ.get("HPE_KB_PATH", "/mcp"),
                        help="HTTP path the MCP endpoint is served on")
    parser.add_argument("--stateless", action="store_true",
                        help="Serve without server-side sessions. Try this if the "
                             "Morpheus MCP client fails to keep a session.")
    parser.add_argument("--allowed-host", action="append", default=[], metavar="HOST",
                        help="Turn on DNS-rebinding protection and allow this Host "
                             "header (repeatable, e.g. kb.example.com:8081). Off by "
                             "default, which accepts any Host.")
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
        return

    security = None
    if args.allowed_host:
        from mcp.server.transport_security import TransportSecuritySettings
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=args.allowed_host)

    mcp.run(transport=args.transport, host=args.host, port=args.port,
            streamable_http_path=args.path, stateless_http=args.stateless,
            transport_security=security)


if __name__ == "__main__":
    main()

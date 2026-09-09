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

"""Tracking which HPE Morpheus release is current.

HPE gates knowledgebase *search* behind a login but serves the documents
themselves anonymously, so there is no credential-free way to ask "what is the
newest release notes document". The answer here is a small list of known
release-notes documents that the server reads and compares. Two ways to extend
it: point ``HPE_KB_DOCUMENTS`` at your own JSON file, or let the agent find a
newer document id with its web search and pass it to ``get_hpe_document``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from .client import HpeKbClient, HpeKbError

# Seeded from the documents that exist today. A new release means a new entry;
# nothing here is guessed from a URL pattern, because HPE's document ids are not
# sequential and the prefix changes between releases (sd... then dp...).
DEFAULT_DOCUMENTS: list[dict] = [
    {"product": "morpheus-enterprise", "doc_id": "dp00008463en_us"},
    {"product": "morpheus-enterprise", "doc_id": "sd00008269en_us"},
    {"product": "morpheus-enterprise", "doc_id": "sd00006660en_us"},
    {"product": "morpheus-vm-essentials", "doc_id": "sd00008079en_us"},
]

VERSION_PATTERN = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
DATE_PATTERN = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{4})", re.IGNORECASE)


@dataclass
class Release:
    product: str
    doc_id: str
    version: str
    version_key: tuple[int, int, int]
    title: str | None
    published: str | None
    url: str

    def as_dict(self) -> dict:
        return {
            "product": self.product,
            "version": self.version,
            "published": self.published,
            "title": self.title,
            "doc_id": self.doc_id,
            "url": self.url,
        }


def load_documents() -> list[dict]:
    path = os.environ.get("HPE_KB_DOCUMENTS")
    if not path:
        return list(DEFAULT_DOCUMENTS)
    with open(path, encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, list):
        raise ValueError(f"{path} must contain a JSON list of documents")
    return loaded


def parse_version(*candidates: str | None) -> tuple[str, tuple[int, int, int]] | None:
    for candidate in candidates:
        if not candidate:
            continue
        match = VERSION_PATTERN.search(candidate)
        if match:
            parts = tuple(int(p) for p in match.groups())
            return ".".join(str(p) for p in parts), parts  # type: ignore[return-value]
    return None


def parse_published(text: str | None) -> str | None:
    if not text:
        return None
    match = DATE_PATTERN.search(text)
    return f"{match.group(1).capitalize()} {match.group(2)}" if match else None


def describe_release(client: HpeKbClient, entry: dict) -> Release | None:
    """Read one release-notes document far enough to date and version it."""
    doc_id = entry["doc_id"]
    document = client.get_document(doc_id)

    # A single-page document carries the version in its <h1>; a multi-page one
    # carries it in the first topic name ("v9.0.2 LTS Release Notes").
    first_topic = document.topics[0] if document.topics else None
    version = parse_version(document.title, first_topic.name if first_topic else None)
    if not version:
        return None

    body = document.text
    if not body and first_topic and first_topic.page:
        body = client.render_page(doc_id, first_topic.page)

    return Release(
        product=entry.get("product", "unknown"),
        doc_id=doc_id,
        version=version[0],
        version_key=version[1],
        title=document.title or (first_topic.name if first_topic else None),
        published=parse_published(body),
        url=client.viewer_url(doc_id),
    )


def latest_release(client: HpeKbClient, product: str) -> tuple[Release | None, list[Release], list[str]]:
    """Return (newest, all known for the product, problems encountered)."""
    releases: list[Release] = []
    problems: list[str] = []
    for entry in load_documents():
        if entry.get("product") != product:
            continue
        try:
            release = describe_release(client, entry)
        except (HpeKbError, ValueError) as exc:
            problems.append(f"{entry.get('doc_id')}: {exc}")
            continue
        if release:
            releases.append(release)
    releases.sort(key=lambda r: r.version_key, reverse=True)
    return (releases[0] if releases else None), releases, problems

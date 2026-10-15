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

"""Title search over a local catalog of HPE documents.

HPE's knowledgebase search needs a signed-in account, so an agent without a
session cookie has no way to go from "the HVM qualification matrix" to a
document id. A catalog closes that gap: a JSON file of document ids, titles,
kinds, products and dates that the operator builds and points
``HPE_KB_CATALOG`` at. It holds metadata, never document text; the text is
read live with ``get_hpe_document``. No catalog ships with this server.

Shape::

    {"snapshot": "2026-10-15",
     "documents": [{"doc_id": "...en_us", "title": "...", "kind": "release-notes",
                    "products": ["enterprise"], "date": "2026-10-15"}]}
"""

from __future__ import annotations

import json
import os
import re

KINDS = ("release-notes", "documentation", "guide", "advisory", "notice",
         "bulletin", "kb-article")
PRODUCTS = ("enterprise", "advanced", "vm-essentials", "all", "cloudops", "other")

_WORD = re.compile(r"[a-z0-9][a-z0-9.\-]*")


def load_catalog(path: str | os.PathLike | None = None) -> dict | None:
    """Load the catalog named by ``path`` or ``HPE_KB_CATALOG``; None if neither is set."""
    source = path or os.environ.get("HPE_KB_CATALOG")
    if not source:
        return None
    with open(source, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
        raise ValueError(f"{source} must contain an object with a 'documents' list")
    return data


def _words(text: str) -> list[str]:
    words = _WORD.findall(text.lower())
    # "v9.1.0" should match a query for "9.1".
    return words + [w[1:] for w in words if len(w) > 1 and w[0] == "v" and w[1].isdigit()]


def find_documents(catalog: dict, query: str = "", product: str | None = None,
                   kind: str | None = None, max_results: int = 15) -> list[dict]:
    """Return catalog entries whose title holds every query word, newest first.

    A word matches as a prefix of a title word, so "qualif" finds
    "Qualification" and "9.1" finds "9.1.0". Documents tagged ``all`` match any
    product filter, because they apply to every edition.
    """
    terms = _words(query)
    hits = []
    for doc in catalog["documents"]:
        if kind and doc.get("kind") != kind:
            continue
        products = doc.get("products") or []
        if product and product not in products and "all" not in products:
            continue
        title_words = _words(doc.get("title", ""))
        if all(any(w.startswith(t) for w in title_words) for t in terms):
            hits.append(doc)
    hits.sort(key=lambda d: (d.get("date") or "", d.get("doc_id") or ""), reverse=True)
    return hits[:max(1, max_results)]

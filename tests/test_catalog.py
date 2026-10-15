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

"""Offline tests for the shipped document catalog and its title search."""

import unittest

from hpe_kb_mcp.catalog import KINDS, PRODUCTS, find_documents, load_catalog

CATALOG = {"documents": [
    {"doc_id": "xx00000001en_us", "title": "Product Release Notes 9.1.0",
     "kind": "release-notes", "products": ["enterprise"], "date": "2026-10-15"},
    {"doc_id": "xx00000002en_us", "title": "Product Release Notes 9.0.2",
     "kind": "release-notes", "products": ["enterprise"], "date": "2026-09-25"},
    {"doc_id": "xx00000003en_us", "title": "Qualification Matrix for Clusters",
     "kind": "guide", "products": ["all"], "date": "2026-10-14"},
    {"doc_id": "xx00000004en_us", "title": "Essentials Release Notes v9.1.0",
     "kind": "release-notes", "products": ["vm-essentials"], "date": "2026-10-15"},
]}


class FindDocumentsTests(unittest.TestCase):

    def ids(self, **kwargs):
        return [d["doc_id"] for d in find_documents(CATALOG, **kwargs)]

    def test_every_word_must_match_a_title_word_prefix(self):
        self.assertEqual(self.ids(query="release 9.1"),
                         ["xx00000004en_us", "xx00000001en_us"])
        self.assertEqual(self.ids(query="qualif"), ["xx00000003en_us"])
        self.assertEqual(self.ids(query="matrix 9.1"), [])

    def test_newest_first_and_capped(self):
        self.assertEqual(self.ids(query="release notes", max_results=1),
                         ["xx00000004en_us"])

    def test_product_filter_keeps_documents_for_all_editions(self):
        self.assertEqual(self.ids(product="enterprise", kind="guide"),
                         ["xx00000003en_us"])
        self.assertNotIn("xx00000004en_us", self.ids(product="enterprise"))


class ShippedCatalogTests(unittest.TestCase):

    def test_shipped_catalog_is_well_formed(self):
        catalog = load_catalog()
        self.assertTrue(catalog.get("snapshot"))
        docs = catalog["documents"]
        self.assertGreater(len(docs), 0)
        self.assertEqual(len({d["doc_id"] for d in docs}), len(docs))
        for doc in docs:
            with self.subTest(doc=doc["doc_id"]):
                self.assertTrue(doc["doc_id"].endswith("en_us"))
                self.assertIn(doc["kind"], KINDS)
                self.assertTrue(set(doc["products"]) <= set(PRODUCTS))
                self.assertTrue(doc["title"].strip())


if __name__ == "__main__":
    unittest.main()

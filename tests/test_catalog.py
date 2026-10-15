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

"""Offline tests for the document catalog and its title search."""

import json
import os
import tempfile
import unittest
from unittest import mock

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


class LoadCatalogTests(unittest.TestCase):

    def test_no_catalog_without_configuration(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(load_catalog())

    def test_loads_the_file_named_by_the_environment(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(dict(CATALOG, snapshot="2026-10-15"), handle)
        try:
            with mock.patch.dict(os.environ, {"HPE_KB_CATALOG": handle.name}):
                catalog = load_catalog()
        finally:
            os.unlink(handle.name)
        self.assertEqual(catalog["snapshot"], "2026-10-15")
        for doc in catalog["documents"]:
            self.assertIn(doc["kind"], KINDS)
            self.assertTrue(set(doc["products"]) <= set(PRODUCTS))

    def test_rejects_a_file_without_documents(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump([], handle)
        try:
            with self.assertRaises(ValueError):
                load_catalog(handle.name)
        finally:
            os.unlink(handle.name)


if __name__ == "__main__":
    unittest.main()

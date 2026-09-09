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

"""Offline tests. Fixtures mimic the shape of the real API payloads, not their
content, so the suite runs without network access and without carrying HPE's
copyrighted documentation around in the repository."""

import json
import unittest

from hpe_kb_mcp.client import (AuthenticationRequired, Document, HpeKbClient,
                               HpeKbError, html_to_text, normalise_doc_id)
from hpe_kb_mcp.releases import Release, latest_release, parse_published, parse_version


class FakeClient(HpeKbClient):
    """HpeKbClient with the transport replaced by a URL -> (body, type) map."""

    def __init__(self, responses, **kwargs):
        super().__init__(min_request_interval_seconds=0.0, **kwargs)
        self.responses = responses
        self.requested = []

    def _get(self, url):
        self.requested.append(url)
        if url not in self.responses:
            raise HpeKbError(f"unexpected request: {url}")
        body, content_type = self.responses[url]
        if isinstance(body, str):
            body = body.encode("utf-8")
        return body, content_type


API = "https://support.hpe.com/hpesc/public/api/document"


class NormaliseDocIdTest(unittest.TestCase):
    def test_accepts_the_forms_a_person_actually_pastes(self):
        expected = "dp00008463en_us"
        for value in [
            "dp00008463en_us",
            "  dp00008463en_us  ",
            "https://support.hpe.com/hpesc/public/docDisplay?docId=dp00008463en_us&page=index.html",
            "https://support.hpe.com/hpesc/public/api/document/dp00008463en_us/toc",
            "/hpesc/public/api/document/dp00008463en_us/render?page=GUID-1.html",
        ]:
            self.assertEqual(normalise_doc_id(value), expected, value)

    def test_rejects_junk(self):
        for value in ["", "   ", "https://support.hpe.com/hpesc/public/somethingelse"]:
            with self.assertRaises(ValueError):
                normalise_doc_id(value)


class HtmlToTextTest(unittest.TestCase):
    def test_keeps_the_structure_that_carries_meaning(self):
        html = ("<h2 class='title'>Fixes</h2><ul><li>Fixed a thing</li>"
                "<li>Fixed another</li></ul><p>Done.</p>")
        text = html_to_text(html)
        self.assertIn("## Fixes", text)
        self.assertIn("- Fixed a thing", text)
        self.assertIn("- Fixed another", text)

    def test_drops_scripts_and_decodes_entities(self):
        html = "<div>A &amp; B<script>var x = '<b>no</b>';</script></div>"
        text = html_to_text(html)
        self.assertEqual(text, "A & B")

    def test_empty_input_is_not_an_error(self):
        self.assertEqual(html_to_text(""), "")


class SinglePageDocumentTest(unittest.TestCase):
    def test_a_whole_document_is_returned_as_text(self):
        client = FakeClient({
            f"{API}/sd00008269en_us": (
                "<main><h1 class='title topictitle1'>v9.0.1 Release Notes</h1>"
                "<p>Released July 2026.</p></main>", "htmlzip"),
        })
        doc = client.get_document("sd00008269en_us")
        self.assertFalse(doc.multi_page)
        self.assertEqual(doc.title, "v9.0.1 Release Notes")
        self.assertIn("Released July 2026.", doc.text)
        self.assertEqual(doc.source_url,
                         "https://support.hpe.com/hpesc/public/docDisplay?docId=sd00008269en_us")

    def test_long_documents_are_truncated_and_say_so(self):
        client = FakeClient({f"{API}/big_en_us": ("<p>" + "x" * 5000 + "</p>", "htmlzip")})
        doc = client.get_document("big_en_us", max_chars=100)
        self.assertTrue(doc.truncated)
        self.assertTrue(doc.text.endswith("[truncated]"))


class MultiPageDocumentTest(unittest.TestCase):
    def setUp(self):
        toc = json.dumps([
            {"topicName": "v9.0.2 LTS Release Notes",
             "topicLink": f"{API}/dp00008463en_us/render?page=GUID-AAA.html",
             "description": None},
            {"topicName": "New features",
             "topicLink": f"{API}/dp00008463en_us/render?page=GUID-BBB.html"},
        ])
        self.responses = {
            # The front matter really is nothing but notices - that is the point.
            f"{API}/dp00008463en_us": ("<h1 class='title'>Release Notes</h1>"
                                       "<p>Notices. Trademarks.</p>", "multiPage"),
            f"{API}/dp00008463en_us/toc": (toc, "application/json"),
            f"{API}/dp00008463en_us/render?page=GUID-AAA.html": (
                json.dumps({"page_html": "<p>Version 9.0.2, August 2026.</p>"}),
                "application/json"),
            f"{API}/dp00008463en_us/render?page=GUID-BBB.html": (
                json.dumps({"page_html": "<p>A new feature.</p>"}), "application/json"),
        }

    def test_topics_are_offered_instead_of_the_boilerplate_front_matter(self):
        client = FakeClient(self.responses)
        doc = client.get_document("dp00008463en_us")
        self.assertTrue(doc.multi_page)
        self.assertEqual(doc.text, "")
        self.assertEqual([t.name for t in doc.topics],
                         ["v9.0.2 LTS Release Notes", "New features"])
        self.assertEqual(doc.topics[0].page, "GUID-AAA.html")

    def test_a_single_topic_can_be_read(self):
        client = FakeClient(self.responses)
        doc = client.get_document("dp00008463en_us", page="GUID-AAA.html")
        self.assertIn("Version 9.0.2, August 2026.", doc.text)

    def test_include_all_topics_joins_them_under_headings(self):
        client = FakeClient(self.responses)
        doc = client.get_document("dp00008463en_us", include_all_topics=True)
        self.assertIn("## v9.0.2 LTS Release Notes", doc.text)
        self.assertIn("## New features", doc.text)
        self.assertIn("A new feature.", doc.text)


class AuthenticationTest(unittest.TestCase):
    def test_a_sign_in_notice_in_a_200_body_is_recognised(self):
        class SignInClient(HpeKbClient):
            def _get(self, url):
                body = (b'{"detail":"You will need to sign in to the HPE Support Center '
                        b'with your HPE Account."}')
                if b"sign in to the HPE Support Center" in body[:400]:
                    raise AuthenticationRequired("sign-in required")
                return body, "application/json"

        with self.assertRaises(AuthenticationRequired):
            SignInClient().search("morpheus")


class CachingAndThrottlingTest(unittest.TestCase):
    def test_a_repeated_fetch_is_served_from_cache(self):
        calls = []

        class CountingClient(HpeKbClient):
            def _fetch(self, url):
                calls.append(url)
                return b"<p>hello</p>", "htmlzip"

        client = CountingClient(min_request_interval_seconds=0.0)
        # Exercise the real _get, with only the network call stubbed out.
        import urllib.request

        class FakeResponse:
            headers = {"Content-Type": "htmlzip;charset=UTF-8"}

            def read(self):
                calls.append("network")
                return b"<p>hello</p>"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        original = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: FakeResponse()
        try:
            client.get_document("x_en_us")
            client.get_document("x_en_us")
        finally:
            urllib.request.urlopen = original
        self.assertEqual(calls.count("network"), 1)


class ReleaseTest(unittest.TestCase):
    def test_version_and_date_parsing(self):
        self.assertEqual(parse_version("v9.0.2 LTS Release Notes"), ("9.0.2", (9, 0, 2)))
        self.assertEqual(parse_version(None, "Release Notes 10.11.12")[1], (10, 11, 12))
        self.assertIsNone(parse_version(None, "no version here"))
        self.assertEqual(parse_published("Release Dates v9.0.2 August 2026"), "August 2026")
        self.assertIsNone(parse_published("no date"))

    def test_the_newest_version_wins_regardless_of_list_order(self):
        responses = {
            f"{API}/old_en_us": ("<h1 class='title'>v9.0.1 Release Notes</h1>"
                                 "<p>July 2026</p>", "htmlzip"),
            f"{API}/new_en_us": ("<h1 class='title'>v9.0.10 Release Notes</h1>"
                                 "<p>August 2026</p>", "htmlzip"),
        }
        client = FakeClient(responses)
        import hpe_kb_mcp.releases as releases_module
        original = releases_module.load_documents
        releases_module.load_documents = lambda: [
            {"product": "morpheus-enterprise", "doc_id": "old_en_us"},
            {"product": "morpheus-enterprise", "doc_id": "new_en_us"},
        ]
        try:
            newest, known, problems = latest_release(client, "morpheus-enterprise")
        finally:
            releases_module.load_documents = original

        # 9.0.10 beats 9.0.1 - string comparison would get this backwards.
        self.assertEqual(newest.version, "9.0.10")
        self.assertEqual(newest.published, "August 2026")
        self.assertEqual(len(known), 2)
        self.assertEqual(problems, [])

    def test_an_unreadable_document_is_reported_not_swallowed(self):
        client = FakeClient({})
        import hpe_kb_mcp.releases as releases_module
        original = releases_module.load_documents
        releases_module.load_documents = lambda: [
            {"product": "morpheus-enterprise", "doc_id": "missing_en_us"}]
        try:
            newest, known, problems = latest_release(client, "morpheus-enterprise")
        finally:
            releases_module.load_documents = original
        self.assertIsNone(newest)
        self.assertEqual(known, [])
        self.assertEqual(len(problems), 1)


if __name__ == "__main__":
    unittest.main()

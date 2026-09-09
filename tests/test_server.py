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

"""Offline tests for what the server advertises over MCP. These guard the two
things a Morpheus appliance reads and that nothing else would catch: the tool
annotations, and the version in the `initialize` response."""

import asyncio
import unittest

from hpe_kb_mcp import __version__
from hpe_kb_mcp.server import mcp

EXPECTED_TOOLS = {"get_hpe_document", "list_hpe_document_topics",
                  "search_hpe_kb", "get_latest_morpheus_release"}


def tools():
    return asyncio.run(mcp.list_tools())


class ServerSurfaceTests(unittest.TestCase):

    def test_advertises_every_tool(self):
        self.assertEqual({t.name for t in tools()}, EXPECTED_TOOLS)

    def test_every_tool_is_annotated_read_only(self):
        """Morpheus refuses to run an external tool that does not declare this,
        and a tool added without it would fail only on the appliance."""
        for tool in tools():
            with self.subTest(tool=tool.name):
                self.assertIsNotNone(tool.annotations,
                                     f"{tool.name} declares no annotations")
                self.assertIs(tool.annotations.read_only_hint, True)
                self.assertIs(tool.annotations.open_world_hint, True)

    def test_every_tool_has_a_description(self):
        """The description is what an agent picks the tool from - Morpheus shows
        it in `search_external_tools` rather than the prompt."""
        for tool in tools():
            with self.subTest(tool=tool.name):
                self.assertTrue((tool.description or "").strip())

    def test_reports_a_version(self):
        self.assertTrue(__version__)
        self.assertEqual(mcp.version, __version__)


if __name__ == "__main__":
    unittest.main()

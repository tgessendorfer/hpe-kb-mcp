# hpe-kb-mcp

An MCP server that reads **HPE Support Center documents** and returns them as text,
so an LLM agent can answer from HPE release notes and KB articles instead of
guessing. Written for HPE Morpheus Enterprise Agents; it is a plain MCP server and
works with any client.

Public repository: <https://github.com/tgessendorfer/hpe-kb-mcp> (Apache 2.0).

## Why it exists — read this before changing anything

An agent with a web search tool can **find** HPE documentation but cannot **read**
it. `support.hpe.com/hpesc/public/docDisplay?docId=…` is a JavaScript viewer:
fetching that URL returns ~9 KB of navigation chrome and zero document text, so a
model's own web-fetch tool comes back empty and the agent ends up reasoning from
search-result snippets.

Underneath the viewer is a plain, **unauthenticated** API. All of the following was
verified against the live service:

| Endpoint | Returns |
|---|---|
| `GET /hpesc/public/api/document/{docId}` | whole document (`Content-Type: htmlzip`), or **only front matter** when the document is split into topics (`Content-Type: multiPage`) |
| `GET /hpesc/public/api/document/{docId}/toc` | JSON topic list, each with a `topicLink` containing `page=GUID-….html` |
| `GET /hpesc/public/api/document/{docId}/render?page=GUID-….html` | one topic, JSON with a `page_html` field |
| `GET /hpesc/public/api/document/{docId}/search?q=…` | **requires a signed-in HPE account** — answers HTTP 200 with a sign-in notice in the body, not a 401 |

**The asymmetry is the whole design.** HPE serves documents anonymously but gates
*search* behind a login. So discovery and retrieval are split: the agent's own web
search finds the `docId` (HPE documents are indexed publicly and results carry it),
and this server reads the document properly. `HPE_SESSION_COOKIE` is optional and
only needed for `search_hpe_kb` or entitlement-gated documents.

Things that are **not** reachable and should not be attempted again:

- `docs.morpheusdata.com` → redirects to `www.hpe.com/support/morpheus-enterprise-documentation-latest`, which times out for a plain client.
- `community.hpe.com` → 403 to a plain fetch.
- `myenterpriselicense.hpe.com` → authentication plus contract entitlement.
- `support.hpe.com/docs/display/public/mp00801en_us/` → real server-rendered HTML, but it is the abandoned 8.0.1 documentation; `mp00900/00901/00902en_us` are all 404.

## Layout

| File | Contains |
|---|---|
| `hpe_kb_mcp/client.py` | HTTP client for the document API: caching, throttling, HTML→text, `normalise_doc_id`, single-page vs multi-page handling |
| `hpe_kb_mcp/releases.py` | Version comparison across a configured list of release-notes documents |
| `hpe_kb_mcp/server.py` | MCP tool definitions and the CLI entry point |
| `tests/test_client.py` | Offline tests |

Four tools: `get_hpe_document`, `list_hpe_document_topics`, `search_hpe_kb`,
`get_latest_morpheus_release`.

## Conventions

- **No AI attribution in git.** Commits, tags and PR bodies carry no Claude
  co-author trailer and no "generated with" line.
- **Tests are offline.** Fixtures mimic the *shape* of HPE's payloads, never their
  content — HPE's documentation is copyrighted and does not belong in this repo.
- **Tools return errors as data** (`{"error": …, "detail": …}`) rather than raising,
  so the model can read what went wrong and adjust.
- **Be a polite client.** Caching and a minimum interval between upstream requests
  are deliberate, as is the honest `User-Agent`. Keep them if you extend it.
- Apache 2.0 header on every source file.
- MCP SDK is **2.x**, where `FastMCP` was renamed `MCPServer`
  (`from mcp.server.mcpserver import MCPServer`). Do not write v1 `FastMCP` code.

## Working on it

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e .
./.venv/bin/python -m unittest discover -s tests -t .        # 15 tests, offline
./.venv/bin/python -m hpe_kb_mcp.server --transport stdio    # local client
./.venv/bin/python -m hpe_kb_mcp.server --transport streamable-http --port 8081
```

Python 3.14 locally; `requires-python = ">=3.10"`.

`transport_security` defaults to `None`, which **disables** DNS-rebinding
protection and accepts any `Host` header. `--allowed-host` turns protection on.

## Current state

Working and verified end to end against the live API: `get_latest_morpheus_release`
correctly reports **9.0.2, August 2026** (`dp00008463en_us`), and the streamable-HTTP
transport was exercised with a real `initialize` handshake.

Known limits, all deliberate and documented in the README:

- `get_latest_morpheus_release` compares a **configured list** of documents, because
  there is no anonymous search API to enumerate them. Document ids are not
  sequential and the prefix changes between releases (`sd00008269` → `dp00008463`),
  so a new release needs an entry via `HPE_KB_DOCUMENTS`. Nothing guesses a URL pattern.
- `search_hpe_kb` returns `authentication_required` without a cookie, by design.
- **Read-only agents cannot call it.** Morpheus 9.0.1 treats every external MCP tool
  as a write, so a read-only agent loads these tools and then fails every call with
  `Write operations are disabled for this agent (read-only mode)`. All four tools
  declare `ToolAnnotations(read_only_hint=True, open_world_hint=True)` and 9.0.1
  ignores it; keep the annotations, since they are spec-correct and a later release
  honouring them would fix this. Uncheck *Read-only mode* on the agent.

Registered and verified on the `morph-ent` appliance (9.0.1) on 2026-09-09: an agent
asked a cold question chose the tool itself and answered 9.0.2 / August 2026 /
`dp00008463en_us`, with the configured-list caveat passed through to the user.

Two things about Morpheus that cost a long debugging detour, both now in the README:
external MCP tools are **not** placed in the model's prompt — they are reached through
`search_external_tools` / `load_external_tools` and renamed `external__<id>__<tool>`,
so asking an agent "do you have `get_hpe_document`?" answers **no** even when
everything works. And the appliance log is `/var/log/morpheus/morpheus-ui/current`
(runit/svlogd, not `morpheus-ui.log`); grep it for `AiChatService` for the real reason
a tool call failed.

Deployed as a systemd service on a Rocky 9 VM (`rocky9`, 192.168.0.31) beside the
appliance, from `/opt/hpe-kb-mcp` on python3.12 — Rocky's stock python3 is 3.9, under
the `>=3.10` floor. To ship a change: `sudo git -C /opt/hpe-kb-mcp pull && sudo
systemctl restart hpe-kb-mcp`. The VM tracks GitHub, so an uncommitted working copy
does not reach it.

CI runs the offline suite on 3.10/3.12/3.13 (`.github/workflows/tests.yml`); it needs
no secrets, because the tests never touch the network.

`__version__` in `hpe_kb_mcp/__init__.py` is the single source of the version — the
server reports it as `serverInfo.version` and `pyproject.toml` reads it from there, so
bump that one line and everything follows.

**Not a distributed package.** This was briefly packaged for PyPI with a release
workflow and a console script; all of it was removed and nothing was ever uploaded.
`pyproject.toml` now carries only what `pip install -e .` needs — no readme, license
expression, keywords, classifiers, project URLs or `[project.scripts]`. The server is
launched as `python -m hpe_kb_mcp.server`, never as a `hpe-kb-mcp` command. Do not
re-add packaging metadata, a release workflow or a PyPI publish step; deployments
track this git repository directly.

## Companion project

<https://github.com/tgessendorfer/morpheus-anthropic-plugin> — an `LlmProvider`
plugin that adds Anthropic Claude to HPE Morpheus, by the same author. It supplies
the model and Anthropic's server-side web search; this server supplies the HPE
documents that web search can find but not read. The two are complementary and
cross-linked from both READMEs. **Work on that plugin happens in its own session —
keep this one to the MCP server.**

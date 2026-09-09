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

Not yet done: registering it in Morpheus under *Tools > AI Services > MCP Servers*
and attaching it to an Agent. No packaging beyond `pip install -e .`, no CI.

## Companion project

<https://github.com/tgessendorfer/morpheus-anthropic-plugin> — an `LlmProvider`
plugin that adds Anthropic Claude to HPE Morpheus, by the same author. It supplies
the model and Anthropic's server-side web search; this server supplies the HPE
documents that web search can find but not read. The two are complementary and
cross-linked from both READMEs. **Work on that plugin happens in its own session —
keep this one to the MCP server.**

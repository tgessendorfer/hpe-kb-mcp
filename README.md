# HPE Knowledgebase MCP server

[![tests](https://github.com/tgessendorfer/hpe-kb-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/tgessendorfer/hpe-kb-mcp/actions/workflows/tests.yml)

An MCP server that reads **HPE Support Center documents** and hands them to an
LLM agent as text. Built for HPE Morpheus Enterprise Agents, but it is a plain
MCP server and works with any client.

> Independent community project. Not an official HPE product, and neither
> endorsed by nor affiliated with Hewlett Packard Enterprise.

## Why this exists

An agent with a web search tool can *find* HPE documentation but cannot *read*
it. `support.hpe.com/hpesc/public/docDisplay?docId=...` is a JavaScript viewer:
fetch that URL and you get about 9 KB of navigation chrome and no document text.
So an agent asked "what changed in the latest release" ends up reasoning from
search-result snippets, and says it could not confirm anything.

Underneath the viewer is a plain, **unauthenticated** API that serves the same
documents as HTML and JSON. This server talks to it.

| Endpoint | Returns |
|---|---|
| `GET /hpesc/public/api/document/{docId}` | the whole document (`Content-Type: htmlzip`), or just front matter when it is split into topics (`multiPage`) |
| `GET /hpesc/public/api/document/{docId}/toc` | topic list, each with a render link |
| `GET /hpesc/public/api/document/{docId}/render?page=GUID-….html` | one topic, as JSON with a `page_html` field |
| `GET /hpesc/public/api/document/{docId}/search?q=…` | **needs a signed-in HPE account** |

The asymmetry is the thing to understand: HPE serves documents anonymously but
gates *search* behind a login. So discovery and retrieval are split — the agent's
own web search finds the `docId`, this server reads the document.

## Tools

| Tool | What it does |
|---|---|
| `get_hpe_document(document, page=None, include_all_topics=False)` | Reads a document as text. Takes a bare `docId` **or any URL containing one**, so a pasted browser link works. Multi-page documents return their topic list first; pass a `page`, or `include_all_topics=true`. |
| `list_hpe_document_topics(document)` | Table of contents only — cheap when you want just "Fixes". |
| `search_hpe_kb(query)` | Keyword search. Requires `HPE_SESSION_COOKIE`; without it returns `authentication_required` and tells the model to use web search instead. |
| `get_latest_morpheus_release(product)` | Newest release this server knows about, with version, month and link. |

### About `get_latest_morpheus_release`

It compares a **configured list** of release-notes documents rather than querying
HPE, because there is no anonymous search API to query. Seeded with the Morpheus
Enterprise and VM Essentials release notes that exist today.

Point `HPE_KB_DOCUMENTS` at your own JSON file to extend it:

```json
[
  {"product": "morpheus-enterprise", "doc_id": "dp00008463en_us"},
  {"product": "morpheus-enterprise", "doc_id": "sd00008269en_us"}
]
```

Document ids are not sequential and the prefix changes between releases
(`sd…` then `dp…`), so a new release means a new entry — nothing here guesses a
URL pattern. The tool says so in its own output, and the agent can always find a
newer document with web search and read it with `get_hpe_document`.

## Install and run

Run it from a checkout:

```bash
git clone https://github.com/tgessendorfer/hpe-kb-mcp
cd hpe-kb-mcp
python3 -m venv .venv
./.venv/bin/pip install -e .

# For Morpheus (network-reachable):
./.venv/bin/python -m hpe_kb_mcp.server --transport streamable-http --host 0.0.0.0 --port 8081
# For local testing with a stdio client:
./.venv/bin/python -m hpe_kb_mcp.server --transport stdio
```

There is no published package — this is not on PyPI and is not meant to be
installed by name.

| Flag / variable | Default | Notes |
|---|---|---|
| `--host` / `HPE_KB_HOST` | `0.0.0.0` | |
| `--port` / `HPE_KB_PORT` | `8081` | |
| `--path` / `HPE_KB_PATH` | `/mcp` | endpoint path |
| `--stateless` | off | try it if the Morpheus client fails to hold a session |
| `--allowed-host HOST` | off | turns on DNS-rebinding protection; repeatable. Off means any `Host` header is accepted — fine on a trusted network, worth setting otherwise |
| `HPE_SESSION_COOKIE` | unset | only needed for `search_hpe_kb` and entitlement-gated documents |
| `HPE_KB_CACHE_TTL` | `900` | seconds |
| `HPE_KB_MIN_INTERVAL` | `0.5` | seconds between upstream requests |

**Nothing here needs credentials** for public product documentation. Set
`HPE_SESSION_COOKIE` (the `Cookie` header value from a signed-in browser
session) only if you need search or a gated document; sessions expire, so treat
that as a convenience, not infrastructure.

### As a service

```ini
# /etc/systemd/system/hpe-kb-mcp.service
[Unit]
Description=HPE knowledgebase MCP server
After=network-online.target

[Service]
User=hpe-kb
WorkingDirectory=/opt/hpe-kb-mcp
ExecStart=/opt/hpe-kb-mcp/.venv/bin/python -m hpe_kb_mcp.server \
  --transport streamable-http --host 0.0.0.0 --port 8081
Restart=on-failure
Environment=HPE_KB_CACHE_TTL=3600

[Install]
WantedBy=multi-user.target
```

## Wiring it into HPE Morpheus

1. **Tools > AI Services > MCP Servers** → add this server's URL
   (`http://<host>:8081/mcp`).
2. **Tools > AI Services > Agents** → edit the agent and add it alongside
   `Morpheus (Built-in)`.
3. Leave **Read-only mode** unchecked on the agent — see below.
4. Start a **new** conversation — an existing one keeps the tool catalog it
   opened with.

> **Read-only agents cannot call external tools.** Verified against Morpheus
> 9.0.1: an agent with *Read-only mode* checked discovers and loads these tools
> happily, then fails every call with
> `Write operations are disabled for this agent (read-only mode)`. Morpheus
> classifies every external MCP tool as a write. Declaring the tools read-only
> does not help — they all carry `readOnlyHint`, and 9.0.1 ignores it. The
> setting only ever guarded Morpheus's *own* write tools, so unchecking it
> exposes nothing about this server, whose four tools are GETs against
> `support.hpe.com`. To keep that guard, run two agents: a read-only one
> without this server, and a documentation one with it.

Do not verify a registration by asking the agent whether it has
`get_hpe_document` — it will say no even when everything works. Morpheus does
not put external tools in the model's prompt; it exposes them through
`search_external_tools` and `load_external_tools`, and renames them
`external__<id>__<tool>`. Ask it to run `search_external_tools` instead. Nothing
reaches this server between registration and the first real call, so a quiet
access log is expected.

Worth adding to the agent's system prompt, since it steers the split cleanly:

> For questions about HPE product releases, documentation or known issues, use
> the HPE knowledgebase tools. Use the Morpheus tools for what is actually
> deployed on this appliance. Never state a release version from memory.
> When citing a document, print its full URL as plain text on its own line —
> not markdown link syntax, and not the bare document id.

That last line is a workaround for the chat UI, which renders neither markdown
links nor bare URLs as anchors (Morpheus 9.0.1). Nothing here can produce a
clickable link; a full URL is at least selectable in one go, where a bare
`dp00008463en_us` leaves the reader to assemble it. Drop the line if a later
release renders markdown — the tools already return a `url` field, so proper
links come back for free.

The appliance must be able to reach this server. Runs happily next to the
appliance or on it.

## Behaviour worth knowing

- **Caching and throttling.** Responses are cached (15 minutes by default) and
  upstream requests are serialised with a minimum interval, so an agent that
  fans out does not turn into a burst of traffic against HPE's portal.
- **Errors are returned, not raised.** Tools answer with
  `{"error": "...", "detail": "..."}` so the model can read what went wrong and
  adjust, rather than seeing a transport failure.
- **Truncation is explicit.** Long documents come back with `truncated: true` and
  a `[truncated]` marker instead of silently losing the tail.
- **This reads a vendor portal.** It is polite by construction — cached,
  throttled, honestly identified in its `User-Agent` — and reads only documents
  HPE serves publicly. Keep it that way if you extend it.

## Tests

```bash
./.venv/bin/python -m unittest discover -s tests -t .
```

Offline: the fixtures mimic the *shape* of HPE's payloads, not their content, so
the suite needs no network and carries none of HPE's documentation. That is also
why CI needs no secrets — GitHub Actions runs the same command on 3.10, 3.12 and
3.13 on every push and pull request.

`tests/test_server.py` covers what the appliance actually reads: that every tool
is annotated read-only, that each carries a description, and that the server
reports a version. A tool added without those would pass every other test and
fail only once it reached Morpheus.

## Versioning

`__version__` in `hpe_kb_mcp/__init__.py` is the single source — `pyproject.toml`
reads it, and the server reports it as `serverInfo.version`. Bump that one line
and everything follows. Deployments track this repository directly; there is no
build or publish step.

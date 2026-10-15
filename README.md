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
gates *search* behind a login. So discovery and retrieval are split — a local
document catalog (`find_hpe_document`, if you configure one) or the agent's own
web search finds the `docId`, and this server reads the document.

## Tools

| Tool | What it does |
|---|---|
| `get_hpe_document(document, page=None, include_all_topics=False)` | Reads a document as text. Takes a bare `docId` **or any URL containing one**, so a pasted browser link works. Multi-page documents return their topic list first; pass a `page`, or `include_all_topics=true`. |
| `list_hpe_document_topics(document)` | Table of contents only — cheap when you want just "Fixes". |
| `search_hpe_kb(query)` | Keyword search. Requires `HPE_SESSION_COOKIE`; without it returns `authentication_required` and tells the model to find the `docId` with one narrow web search on support.hpe.com, without web-fetching HPE pages. |
| `find_hpe_document(query, product=None, kind=None)` | Finds HPE documents by title in a **local catalog** you point `HPE_KB_CATALOG` at, without a login or web search. Returns `doc_id`, title, kind, products, date and link, newest first. Without a catalog it answers `no_catalog`. |
| `get_latest_morpheus_release(product)` | Newest release this server knows about, with version, month and link. `product`: `morpheus-enterprise`, `morpheus-advanced`, `morpheus-vm-essentials`. |

### About `find_hpe_document`

No catalog ships with this server. Build a JSON file of the documents you care
about and set `HPE_KB_CATALOG` to its path:

```json
{
  "snapshot": "2026-10-15",
  "documents": [
    {"doc_id": "sd00008803en_us", "title": "HPE Morpheus Enterprise Software v9.1.0 Release Notes",
     "kind": "release-notes", "products": ["enterprise"], "date": "2026-10-15"}
  ]
}
```

It holds ids, titles, kinds, products and dates only, never document text;
`get_hpe_document` reads the text live. One way to fill it is the document list
of an HPE Support Center product page, which a browser can export; keep only
public documents, since restricted ones need a signed-in account to read.

Every query word must match the start of a title word (`9.1` finds `v9.1.0`).
`product` is one of `enterprise`, `advanced`, `vm-essentials`, `all`,
`cloudops`, `other`; `kind` is one of `release-notes`, `documentation`, `guide`,
`advisory`, `notice`, `bulletin`, `kb-article`. Documents tagged `all` match any
product filter. The file is read on every call, so replacing it needs no restart.

### About `get_latest_morpheus_release`

It compares a **configured list** of release-notes documents rather than querying
HPE, because there is no anonymous search API to query. Seeded with the Morpheus
Enterprise and VM Essentials release notes that exist today.

Point `HPE_KB_DOCUMENTS` at your own JSON file to extend it:

```json
[
  {"product": "morpheus-enterprise", "doc_id": "sd00008803en_us"},
  {"product": "morpheus-enterprise", "doc_id": "dp00008463en_us"}
]
```

Document ids are not sequential and the prefix changes between releases
(`sd…`, then `dp…` for 9.0.2, then `sd…` again for 9.1.0), so a new release
means a new entry — nothing here guesses a URL pattern. The tool says so in its own output, and the agent can always find a
newer document id with one narrow web search and read it with `get_hpe_document`.

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

**HPE Morpheus 9.1.0 refuses MCP servers on private addresses.** If this server
has an RFC1918, IPv6 ULA or CGNAT address, every tool call fails on 9.1.0 with
`Blocked MCP connection to unsafe destination … Destination resolves to a private
address` in `/var/log/morpheus/morpheus-ui/current`, while the server still shows
OK. The switch is the undocumented, appliance-wide setting
`morpheus.mcp.allowPrivateDestinations`; setting it as a JVM system property
(`-Dmorpheus.mcp.allowPrivateDestinations=true` in
`/opt/morpheus/lib/tomcat/bin/setenv.sh`, then restart `morpheus-ui`) worked.
It opens private destinations for every MCP server on the appliance, so weigh
that before using it. Loopback stays blocked, so running this server on the
appliance itself does not work on 9.1.0.

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

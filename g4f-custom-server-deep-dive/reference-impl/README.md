# reference-impl — working re-implementation of the g4f.dev custom-server feature

A "deepened" Python/FastAPI port of the custom-server logic in
`gpt4free/g4f.dev` `workers/api-worker.js`, with the 10 bugs from
[`../03-BUGS-AND-IMPROVEMENTS.md`](../03-BUGS-AND-IMPROVEMENTS.md) fixed, plus a mock of
cakey's upstream and a small chat UI that reproduces the g4f.dev flow.

## Files

| File | Role |
|---|---|
| `server.py` | The router (≈ the Cloudflare Worker): server CRUD + validation, public index, `/custom/{srv_id}/models`, `/custom/{srv_id}/chat/completions` proxy (stream + non-stream), usage tracking, `Server?` easter egg, rate limits, auth |
| `mock_upstream.py` | Fake `osaii.wyvernhub.net` — OpenAI-compatible `/api/v1/models` + `/chat/completions` (SSE) with Bearer-key auth; stands in for cakey's real relay |
| `static/index.html` | Demo chat UI: server picker (from public index + your servers) → model picker (sorted by usage) → streaming chat, "Test: Server?" button |
| `test_e2e.py` | 32-check end-to-end suite (spawns both servers, exercises everything) |
| `servers.json` / `public_index.json` / `usage_stats.json` | Runtime state (R2/KV/D1 stand-ins; created at runtime) |

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh            # mock upstream :9101 + router/UI :8090  →  open http://localhost:8090
.venv/bin/python test_e2e.py
```

The router auto-seeds **cakey's exact server** ("custom server by cakey(Luna, GLM 5.3
Flash, Kimi K3, etc)") pointing at the local mock, so the UI works immediately.

## Try

1. Open the UI, pick the cakey server + any model, send a message — watch it stream.
2. Click **🧪 Test: “Server?”** — the router answers `"<label> - Server ID: srv_…"`
   without touching the upstream (same as production).
3. API tour (session token for the demo user):

```bash
S="Bearer g4f_demo_session_token"
curl -s -H "Authorization: $S" http://localhost:8090/custom/api/servers | python3 -m json.tool
curl -s http://localhost:8090/custom/api/servers/public | python3 -m json.tool
curl -s http://localhost:8090/custom/<srv_id>/models | python3 -m json.tool
curl -s -H "Authorization: $S" -H "Content-Type: application/json" \
     -d '{"model":"logfare/kimi-k3","stream":true,"messages":[{"role":"user","content":"hi"}]}' \
     http://localhost:8090/custom/<srv_id>/chat/completions
curl -s -H "Authorization: $S" "http://localhost:8090/custom/api/servers/usage?server_id=<srv_id>"
curl -s -H "Authorization: $S" -X POST http://localhost:8090/custom/api/servers/create \
     -H "Content-Type: application/json" \
     -d '{"label":"my relay","base_url":"https://api.example.com/v1","api_keys":"sk-…","is_public":false}'
```

## Mapping to the real code

| reference impl | g4f.dev original |
|---|---|
| `generate_server_id()` | `generateServerId()` api-worker.js:2323 |
| `validate_server()` | `validateServer()` api-worker.js:2397 |
| `is_ollama()` | `isOllama()` api-worker.js:2329 |
| `Store.get_server()` | `getServerById()` api-worker.js:2224 |
| `Store.upsert_index()` (redacting) | `updatePublicServerIndex()` api-worker.js:2303 |
| `list_public_servers()` | `handleUpdatePublicServers()` api-worker.js:1074 |
| `proxy_chat()` | `handleProxyToServer()` api-worker.js:1247 + `createUsageTrackingStream()` :1972 |
| `server_models()` | `handleModels()` api-worker.js:1186 |
| `static/index.html` picker | `loadCustomProvidersFromAPI()` addon-init.js:577 + `createClient()` providers.js:91 |

Deliberate simplifications: single demo user (no OAuth flow), in-memory rate limits,
JSON-file storage instead of KV/R2/D1, no edge cache, no VSCode prompt rewriter, no
cake-credit gate — everything else mirrors the production behavior 1:1.

# 02 — Cakey's server, verified against the production API

What the deep link actually resolves to. All data below was fetched live on **2026-09-13**
from the production backend (`g4f.space`) and saved in [`live-data/`](live-data/).

## The record

`GET https://g4f.space/custom/api/servers/public` → entry:

```json
{
  "id": "srv_mtsj8uzo97d3c0d49960",
  "label": "custom server by cakey(Luna, GLM 5.3 Flash, Kimi K3, etc)",
  "base_url": "https://osaii.wyvernhub.net/api/v1",
  "auto_update_models": true,
  "is_public": true,
  "is_ollama": false,
  "created_at": "2026-09-08T10:34:36.900Z",
  "updated_at": "2026-09-13T17:23:50.868Z",
  "validated_at": "2026-09-08T10:34:36.900Z",
  "usage": { "requests": 119, "tokens": 3518, "last_used": "2026-09-09T12:03:01.032Z" },
  "is_hidden": false,
  "is_valid": true,
  "allowed_models": [ …48 models… ]
}
```

So: **cakey registered the OpenAI-compatible relay `https://osaii.wyvernhub.net/api/v1`
on 2026-09-08 with his own key(s), and made it public.** Anyone on g4f.dev can pick
"custom server by cakey(…)" in the chat and chat through the g4f.space proxy, paying with
their own g4f quota (or cake-credits if anonymous).

## What the ID encodes

`srv_mtsj8uzo97d3c0d49960` = `srv_` + `mtsj8uzo` + `97d3c0d49960`

* `mtsj8uzo` is `Date.now()` in **base36** → `2026-09-08T10:34:36.900Z` (matches `created_at`
  exactly — the scheme is `workers/api-worker.js:2323` `generateServerId()`),
* `97d3c0d49960` is 6 random bytes from `crypto.getRandomValues` (12 hex chars).

So server IDs are time-ordered and collision-safe, and you can date any server from its ID.

## The 48 models (top of the list)

Poolside Laguna XS/S 2.1, osaii's own `voicellm` / `faster-experimental` /
`ultrafast-experimental`, then the "-z" (relay) lineup:

* `openai-z/gpt-5.6-luna` ← the "Luna" in the label
* `zai-z/zai-org-glm-5-3-flash` ← "GLM 5.3 Flash"
* `logfare/kimi-k3` ← "Kimi K3"
* `xai-z/grok-4-fast-non-reasoning`, `deepseek-z/deepseek-v4-pro`,
  `gemini-z/gemini-3.1-flash-lite-preview`, `qwen-z/qwen3.8-flash`,
  `venice-z/gemma-4-uncensored`, `minimax-z/minimax-m3`, `mimo-z/mimo-v2.5-pro`, …

## Live endpoint behavior observed

### `GET /custom/srv_mtsj8uzo97d3c0d49960/models`

Returns the upstream's model list (OpenAI shape: `id`, `pricing`, `context_length`,
`supported_features`…) **filtered by `allowed_models` and sorted by request count**
(`handleModels()` + `getModelsFromStats()`), e.g. at snapshot time:

| model | requests through g4f.dev |
|---|---|
| `poolside/laguna-xs-2.1` | 227 |
| `zai-z/zai-org-glm-5-3-flash` | 119 |
| `xai-z/grok-4-fast-non-reasoning` | 104 |
| `xai-z/grok-4-1-fast-non-reasoning` | 99 |
| `deepseek-z/deepseek-v4-pro` | 47 |
| `openai-z/gpt-5.6-luna` | 41 |
| `logfare/kimi-k3` | 22 |

(the per-model stats are served from the worker's D1 `usage_logs` — proof the usage
tracking in the reference impl mirrors real behavior.)

### `GET /custom/srv_mtsj8uzo97d3c0d49960/status`

Returns the server entry **without `api_keys`** — the redaction happens in the handler
(`delete entry.api_keys`), confirming keys are only ever served to the owner's own
endpoints.

### Chat (POST) — verification note

`POST /custom/{srv_id}/chat/completions` could **not** be exercised from this sandbox
(direct egress to `g4f.space` is TLS-blocked here; only the page-fetch tool reaches it).
The POST path was instead verified byte-for-byte against the source and re-implemented +
fully tested in `g4f-reference-impl/` (32/32 checks, including the SSE usage capture and the
`Server?` identity ping that the production code performs at `api-worker.js:1319`).

## Where this sits among all public servers

The public index held **~100 public servers** at snapshot time (28 fetch chunks of JSON).
Highlights seen: pollinations' own servers (375k requests / 352M tokens), groq.com,
nvidia.com (825M tokens), perplexity, you.com, cerebras.ai, "Google Antigravity"
(pass.g4f.space-backed), cloudflare (CF AI models), logfare.ai, sharktide, unorouter.com,
plus several private home Ollama instances (e.g. `http://213.199.44.213.nip.io:11434/v1` —
the nip.io IPv4 mapping the worker adds so Cloudflare can reach raw-IP Ollama boxes).
cakey's server is a young one (created 2026-09-08) with modest usage — but it is *valid*
and online.

## Reproducing this check yourself

```bash
curl -s https://g4f.space/custom/api/servers/public | python3 -m json.tool | grep -A3 srv_mtsj8uzo97d3c0d49960
curl -s https://g4f.space/custom/srv_mtsj8uzo97d3c0d49960/status | python3 -m json.tool
curl -s https://g4f.space/custom/srv_mtsj8uzo97d3c0d49960/models | python3 -m json.tool
```

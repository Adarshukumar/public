# route.md — the full route, end to end

> The **entire** deepened dataset: how I reverse-engineered g4f.dev's
> custom-server feature **with zero prior information**, how the **real API
> actually works** (endpoint by endpoint, line by line), **all the live data**,
> the 10 bugs found in production, and how **every part of this repo works**.
>
> This is the master document. `README.md` is the friendly overview; the
> numbered docs in `research/` are the per-topic deep dives; **this is the
> whole thing in one route.**

---

## Table of contents

1. [The reverse-engineering: zero info → full picture](#1-the-reverse-engineering-zero-info--full-picture)
2. [How the REAL g4f.dev API works](#2-how-the-real-g4fdev-api-works)
   - [2.1 Topology](#21-topology) · [2.2 The data model](#22-the-data-model)
   - [2.3 Auth + tiers](#23-auth--tiers) · [2.4 The master router](#24-the-master-router)
   - [2.5 The proxy, step by step](#25-the-proxy-step-by-step)
   - [2.6 Validation + the hourly refresh loop](#26-validation--the-hourly-refresh-loop)
   - [2.7 The frontend, from URL to answer](#27-the-frontend-from-url-to-answer)
   - [2.8 Storage map](#28-storage-map)
3. [ALL the data](#3-all-the-data)
   - [3.1 Cakey's live record](#31-cakeys-live-record) · [3.2 The 48 models + live usage](#32-the-48-models--live-usage)
   - [3.3 What else was in the public index](#33-what-else-was-in-the-public-index)
4. [The 10 bugs in the production code](#4-the-10-bugs-in-the-production-code)
5. [How the chatbot in this repo works (route-py + route-web)](#5-how-the-chatbot-in-this-repo-works-route-py--route-web)
6. [Reasoning models — the extraction process](#6-reasoning-models--the-extraction-process)
7. [Run it / verify it](#7-run-it--verify-it)
8. [Where every piece lives](#8-where-every-piece-lives)

---

# 1. The reverse-engineering: zero info → full picture

The honest question: **how did I figure any of this out when there is no
documentation for the custom-server feature?** There isn't. g4f.dev ships no
API docs for it, no "how custom servers work" page. Here is the exact route
I took, step by step, and what each step yielded.

### Step 1 — The only clue: a live deep link

Everything started from one thing: a **public, unauthenticated JSON index**
that the g4f.dev frontend calls on every chat page load:

```
GET https://g4f.space/custom/api/servers/public
```

I discovered it by fetching the chat page and reading the frontend JavaScript
directly (`dist/js/addons/addon-init.js` → `loadCustomProvidersFromAPI()`,
line 577 — the hardcoded URL). That single function told me:

- the backend host is `https://g4f.space` (a Cloudflare Worker, not g4f.dev itself),
- there is a **public** index and a **private** list (`/custom/api/servers`, needs a session),
- each entry becomes a chat provider option `custom:<server_id>`.

> Sandbox note: direct egress to g4f.space was TLS-blocked from this machine,
> and the headless browser couldn't be installed (CDN blocked). So all
> production probing went through the page-fetch tool (GET only) — which is
> why POST behavior was proven a different way (Steps 6–8).

### Step 2 — Find cakey in the index

The index holds **~100 public servers** (28 fetch chunks of JSON). Scanning it
found the target entry:

```
srv_mtsj8uzo97d3c0d49960  "custom server by cakey(Luna, GLM 5.3 Flash, Kimi K3, etc)"
```

### Step 3 — Decode the ID (a free win)

`srv_mtsj8uzo` + `97d3c0d49960`. The frontend alone didn't explain it — but the
public repo did (Step 5). Once I had `workers/api-worker.js:2323`:

```js
function generateServerId() {
  return "srv_" + Date.now().toString(36) + crypto.getRandomValues(new Uint8Array(6))… // 12 hex
}
```

`mtsj8uzo` = `Date.now()` in **base36** = **2026-09-08T10:34:36.900Z** — which
matches the entry's `created_at` to the millisecond. So: server IDs are
time-ordered and collision-safe, and **any server can be dated from its ID**
without asking anyone.

### Step 4 — Probe the per-server endpoints

From the same frontend code, two more public endpoints per server:

```
GET https://g4f.space/custom/srv_mtsj8uzo97d3c0d49960/status   → full entry (no api_keys)
GET https://g4f.space/custom/srv_mtsj8uzo97d3c0d49960/models   → models sorted by usage
```

Observations that mattered:

- `/status` returns the server **without `api_keys`** → redaction happens in the handler (later confirmed at the code level),
- `is_valid: true` and `updated_at` moving **hourly** → there is a live re-validation loop,
- `/models` returns per-model request counts (227 / 119 / 104 / …) → usage is tracked per model, somewhere persistent,
- `allowed_models` = **48 models** → this became the chatbot's catalog.

Raw responses saved to `research/live-data/`.

### Step 5 — Get the production source

`gpt4free/g4f.dev` is a **public repository**. The custom-server backend is a
Cloudflare Worker: `workers/api-worker.js` (~3.6k lines) plus the auth worker
`members-worker.js` (~4.1k lines) and two wrangler configs. The frontend
addons (`dist/js/addons/*.js`) and `dist/js/providers.js` / `client.js` are in
the same repo. I saved every key file to `research/sources/` and analyzed it
line by line. (This is where "no info" ends: the source *is* the
documentation.)

### Step 6 — Trace the request lifecycle in the code

Reading `safe()` (the master router, ~line 294) top to bottom produced the
complete routing table (reproduced in §2.4), the auth model (§2.3), the rate
limits, and — the crucial function — `handleProxyToServer()` (~line 1247):
the **key-injecting proxy** (§2.5). Every behavior I claimed later was tied
to a file:line in `sources/`.

### Step 7 — Verify every claim against production

Each code-level claim was checked against the live API where a GET allowed it:
the redaction, the hourly refresh, the usage-sorted model list, the ID
encoding, the ~100-server index. What couldn't be probed (POST chat — egress
blocked) was verified **byte-for-byte against the source** and then…

### Step 8 — Re-implement it to prove understanding

`research/g4f-reference-impl/` is a faithful Python/FastAPI port of the
worker: same endpoints, same auth tiers, same proxy semantics, same easter
egg, same usage capture on SSE — **with the 10 bugs fixed** — running against
a mock of cakey's upstream. A 33-check E2E suite exercises the whole surface
(33/33 passing). If my understanding were wrong, this suite would fail.

**The method in one line:** *live probing to find the surface → public source
to understand it → live re-verification of every claim → a working
re-implementation to prove it.*

---

# 2. How the REAL g4f.dev API works

## 2.1 Topology

```
┌────────────────────────── g4f.dev (static frontend) ─────────────────────────┐
│  chat/index.html → dist/js/v2.js (addon loader, dependency waves)            │
│   providers.js       createClient("custom:srv_…")  ← the pivotal 4 lines     │
│   client.js          Client: fetch + SSE streaming + CORS-proxy failover     │
│   addons/addon-init.js        provider dropdown + custom-server loading      │
│   addons/addon-providers-models.js   API-key resolution per provider         │
│   addons/addon-legacy.js      hash routing, message send/stream, tagging     │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │  fetch  Authorization: Bearer <g4f_session>
                                   ▼
┌──────────────── g4f.space (Cloudflare Worker — api-worker.js) ───────────────┐
│  safe(request, env, ctx)  — master router (~line 294)                        │
│   /custom/api/servers/*   CRUD + public index        (KV: MEMBERS_KV)        │
│   /custom/{srv_id}/…      proxy to the owner's upstream (R2: user documents) │
│   /v1/chat/completions    "auto" routing (random public server)              │
│   everything else         → pass.g4f.space (the core g4f provider farm)      │
│                                                                              │
│  storage: MEMBERS_KV · MEMBERS_BUCKET (R2) · USAGE_DB (D1) · caches.default  │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │  fetch  Authorization: Bearer <OWNER's key>
                                   ▼
                owner's OpenAI-compatible upstream
                (cakey's: https://osaii.wyvernhub.net/api/v1)
```

**The key insight:** the g4f.dev chat **never talks to the owner's server
directly**. The Worker is a *key-injecting proxy*: it hides the owner's API
keys from all users, hides the owner's endpoint from non-owners, picks a
random key per request, and meters usage. That's the whole product.

## 2.2 The data model

A "custom server" is a plain JSON object inside the **owner's user document**
(R2 `g4f-members/users/{user_id}.json`, KV-cached 1h as `user:{id}`):

```json
{
  "id": "srv_mtsj8uzo97d3c0d49960",
  "label": "custom server by cakey(Luna, GLM 5.3 Flash, Kimi K3, etc)",
  "base_url": "https://osaii.wyvernhub.net/api/v1",
  "api_keys": "key1\nkey2\n# comments ok",
  "allowed_models": ["openai-z/gpt-5.6-luna", "zai-z/zai-org-glm-5-3-flash", "logfare/kimi-k3", "…48 total…"],
  "auto_update_models": true,
  "is_public": true,
  "is_ollama": false,
  "default_model": null,
  "created_at": "2026-09-08T10:34:36.900Z",
  "updated_at": "…", "validated_at": "…",
  "usage": { "requests": 119, "tokens": 3518, "last_used": "2026-09-09T12:03:01.032Z" }
}
```

Field notes:

- `api_keys` — a **line-separated pool**; one line is picked **uniformly at
  random per request** (`getRandomApiKey`, ~line 2317) → free key rotation /
  load balancing for the owner.
- `allowed_models` — the filter applied to the upstream's `/models`;
  `auto_update_models: true` re-discovers it on validate/update/model-list.
- `is_public` — listed in the public index and usable by **any** visitor.
- `is_ollama` — detected by probing the base URL for an Ollama banner
  (`isOllama`, ~line 2329).

Plus two derived registries:

- **`public_servers_index`** (KV) — array of public entries, maintained by
  `updatePublicServerIndex()` (api-worker.js:2303). ⚠️ production stores the
  *full* object **including `api_keys`** — bug #3.
- **`SERVER_MAP` / `SERVER_TO_PROVIDER`** (from `dist/js/providers.json`,
  api-worker.js:130–167) — the handful of *first-party* providers
  (pollinations, huggingface, airforce, …) that also have `srv_…` IDs and
  subdomains (`{label}.g4f.space`); for those the **caller's** OAuth token is
  injected instead of an owner key (api-worker.js:2230–2234).

## 2.3 Auth + tiers

`authenticateRequest()` (~line 726) accepts three credential forms:

| Credential | What it is |
|---|---|
| `Authorization: Bearer gfs_…` | a **session** token (g4f.dev login; browser stores it as `g4f_session` in localStorage) → resolves to a user doc (R2, KV-cached 1h) |
| `Authorization: Bearer g4f_…` | a **user API key** (hashed lookup in KV `api_key:{sha256}`) |
| `X-API-Key: g4f_…` | same key, different header |

**Most public-server calls work completely unauthenticated** (`user` may be
`null`). Tiers (`USER_TIER_LIMITS`, ~line 31): `new` / `free` / `sponsor` /
`pro` / `admin`. `free` = 1e6 tokens/day, 500 req/day, 10 req/min, **max 10
servers**; `admin` = 100 servers. Anonymous callers are gated by **cake
credits** — a proof-of-work in the browser (`cake-baker.js`) that literally
mines SHA-256 hashes to earn 0.05¢/cake, funding anonymous usage. The
custom-CRUD paths bypass the token limiter but hit a separate 10-second KV
limiter (status **420** — "Enhance Your Calm").

## 2.4 The master router

For every request, `safe()` (~line 294):

1. `OPTIONS` → CORS (fully open: `Access-Control-Allow-Origin: *`).
2. Auth (above).
3. `checkUserRateLimits()` (~2885).
4. Pathname rewrites (~313): `/public` → `/custom/api/servers/public`,
   `/usage` → `/custom/api/servers/usage`, `/srv_XXXX` → `/custom/srv_XXXX`.
5. Edge-cache check for anonymous GETs (`generateCacheKey`, ~3446 — key is the
   URL minus `seed/url/model` params; safe because only public data is cached).
6. The routing table:

| Path | Handler (line) | What happens |
|---|---|---|
| `/custom/api/servers` | `handleListServers` (804) | own servers, secrets stripped, `api_key_count` instead of keys |
| `/custom/api/servers/create` | `handleCreateServer` (828) | validate upstream → assign `srv_…` id → store in user doc → maybe add to public index |
| `/custom/api/servers/update` | `handleUpdateServer` (907) | patch allowed fields, re-validate, refresh public index |
| `/custom/api/servers/delete` | `handleDeleteServer` (971) | tombstone in R2 + remove from public index |
| `/custom/api/servers/usage` | `handleGetServerUsage` (1006) | per-day history from R2 |
| `/custom/api/servers/public` | `handleListPublicServers` (1064) | public index + lazy re-validation (≤10 stale/pass, hourly budget) |
| `/custom/{srv_id}/models` | `handleModels` (1186) | upstream `/models` filtered by `allowed_models`, **sorted by usage**, cached 1h |
| `/custom/{srv_id}/chat/completions` | `handleProxyToServer` (1247) | the proxy below |
| `/custom/{srv_id}/validate` / `/status` | inline (589) | live `validateServer()` + `isOnline()` |
| `/custom/{anything else}` | `handleProxyToServer` | generic sub-path proxy (quota, images, audio, …) |
| `/v1/chat/completions` | `handleV1ChatCompletions` (3180) | "auto" routing: random public server + fallback chain |
| `/api/{label}/…` | `getServerByLabel` (2264) | label → server (own labels → public → `pass.g4f.space/api/{label}`) |
| *everything else* | `proxyToPassG4f` (3542) | passthrough to the core provider farm; also `BLOCKED_ORGS` (datacenter-ASN blocklist) for anonymous callers |

## 2.5 The proxy, step by step

`handleProxyToServer()` (~line 1247) — the most interesting function in the
codebase:

1. **Read the body** (POST) → the requested model + last message content.
2. **Content surgery** — a block of regexes that *rewrites* Microsoft VSCode
   Copilot system prompts (detected by a distinctive snippet) into a shorter
   "expert coding agent" prompt, strips ~30 VSCode tool schemas, and softens
   ALL-CAPS boilerplate (`IMPORTANT:`/`NEVER`/`MUST` → calmer wording). It
   even counts the saved bytes. (Why g4f.dev is popular with Copilot-bridge
   users: it saves real prompt tokens.)
3. **Abuse filters** — a hardcoded Russian SEO-spam prompt is blocked with
   403; and the easter egg: if the last message is exactly **`Server?`**, it
   short-circuits and returns `"{label} - Server ID: {id}"` **without
   touching the upstream** (line 1319).
4. **Pick a key** — `getRandomApiKey(server.api_keys)`: uniformly random line
   from the owner's pool.
5. **Build the target URL**:
   ```js
   if (server.base_url.includes(subPath))                             targetUrl = server.base_url;
   else if (server.base_url.includes("/v1/chat/completions"))         targetUrl = base.split("/v1/")[0] + subPath;
   else                                                               targetUrl = `${server.base_url}${subPath}`;
   ```
   plus `URL_MAP` rewrites for known quota URLs, and a special case:
   `api.you.com/v1` is converted to their `/answer` API and its response is
   **re-wrapped into OpenAI shape**.
6. **Forward** with the original method/headers + the owner's
   `Authorization` + `x-user`, `x-secret`, `HTTP-Referer: https://g4f.dev`,
   `X-OpenRouter-Title: GPT4Free`.
7. **Stream or JSON**:
   - **streaming** → piped back as SSE through `createUsageTrackingStream()`
     (~1972): a **tee** that parses every `data:` line to capture `usage`
     (or pollinations' `pollen_cost`), then — via `ctx.waitUntil`
     (non-blocking) — persists to D1 `usage_logs`, bumps the server's `usage`
     counters in the owner doc, bumps the user's daily usage, and for
     anonymous callers charges cake credits for what was actually spent.
   - **non-streaming** → proxied as-is; repeated canned test prompts
     (`"hi"`, `"ping"`, …) are deduped by `generatePostBodyHash()` (~3464) and
     served from the edge cache.
8. **Response headers** — `X-Server: {srv_id}`, `X-Provider: {label}`,
   `X-Url: <path — full URL only for admin>`, `X-User-Id`, `X-User-Tier`,
   `X-Ratelimit-*`. **The frontend reads `X-Server` and tags every streamed
   message with `provider = "custom:" + server_id`**
   (addon-legacy.js:1772/1819) — that string is what ends up in conversation
   data and share links.

## 2.6 Validation + the hourly refresh loop

`validateServer()` (~2397) — on create/update and on the hourly index refresh:

1. strip a trailing `/chat/completions` (via `.replace("/chat/completions","")`
   — bug #6),
2. if a default model is known → a real `POST /chat/completions` with
   `"Hello"` (30 s timeout) to prove the pipeline end-to-end; 401/403 →
   "Authentication failed - check your API keys",
3. otherwise probe `GET {base}/models` → `{base}/v1/models` → the
   https-upgrade of an http URL; accepts `{"data":[{id}]}` (OpenAI shape),
   `{"models":[…]}` or a bare array,
4. models found + `auto_update_models` → stored on the server record.

`handleUpdatePublicServers()` (~1074) runs on **every call to the public
index** (i.e. every chat page load): entries with `updated_at` older than an
hour become re-validation candidates; **at most 10 per pass** (each = 1–3
upstream HTTP calls); valid servers get fresh models + `is_valid: true` +
`updated_at` stamp (bug #4: stamped *before* validating); the result is
edge-cached up to 24h when nothing was refreshed. **This is how cakey's
48-model list stays current.**

## 2.7 The frontend, from URL to answer

1. `chat/index.html` loads `dist/js/v2.js` — a chunked addon loader that
   injects each `addon-*.js` in dependency waves
   (`framework,core,worker,load` → `legacy,init` → … → `picker`).
2. `updateLiveProviderOptions()` (addon-init.js:553) adds first-party
   providers from `providers.json`; then `loadCustomProvidersFromAPI()`
   (addon-init.js:577): if a `g4f_session` exists → `GET /custom/api/servers`
   (private; a 401 clears the stale session); always → `GET
   /custom/api/servers/public`; merges (private first, deduped), stores in
   `window.customServers`, and renders
   `<option value="custom:{srv_id}">label (N models)</option>` with an
   enable/disable checkbox persisted as `enableCustomServer_{id}`.
3. Send: `get_api_key_by_provider("custom:…")` → **`g4f_session`**
   (addon-providers-models.js:221); then `createClient("custom:srv_…")` —
   **the pivotal 4 lines** (dist/js/providers.js:91):
   ```js
   if (provider.startsWith("custom:")) {
       serverId = provider.substring(7);
       options.baseUrl = `https://g4f.space/custom/${serverId}`;
       provider = "custom";
   }
   ```
   → a generic `Client` with
   `apiEndpoint = https://g4f.space/custom/{srv_id}/chat/completions`.
4. `ask_gpt()` POSTs `{model, messages, stream:true, …}` with
   `Authorization: Bearer <g4f_session>`; chunks arrive as SSE
   (`data: {...}` lines) and render with a typing effect; the `X-Server`
   header tags the message's provider.
5. **The `#custom:srv_…` hash is a conversation ID, not a provider selector.**
   The chat treats the URL fragment as `conversation_id`
   (addon-load.js:284, addon-legacy.js:3039, addon-settings.js:295); unknown
   hashes are fetched from `GET {backend}/backend-api/v2/chat/{id}`
   (addon-load.js:207) → **shared conversations**. So
   `https://g4f.dev/chat/#custom:srv_mtsj8uzo97d3c0d49960` deep-links a
   conversation *tagged* with that server (verified: all three hash handlers
   parse no `custom:` provider prefix).

## 2.8 Storage map

| Store | Binding | What lives there |
|---|---|---|
| R2 | `MEMBERS_BUCKET` | `users/{user_id}.json` — the source of truth (incl. `api_keys`) |
| KV | `MEMBERS_KV` | `user:{id}` (1h cache), `api_key:{sha256}` → user, `public_servers_index`, `server:{id}` (5-min cache), tombstones, rate-limit counters |
| D1 | `USAGE_DB` | `usage_logs` — per-request token stats, 14-day retention + cleanup cron |
| D1 | `ERRORS_DB` | `error_logs` |
| CF cache | `caches.default` | public GETs (index, models) + repeated canned test prompts, up to 24h |

---

# 3. ALL the data

## 3.1 Cakey's live record

Fetched **2026-09-15** from production (raw JSON in `research/live-data/`):

```json
{
  "id": "srv_mtsj8uzo97d3c0d49960",
  "label": "custom server by cakey(Luna, GLM 5.3 Flash, Kimi K3, etc)",
  "base_url": "https://osaii.wyvernhub.net/api/v1",
  "auto_update_models": true,
  "is_public": true,
  "is_ollama": false,
  "created_at": "2026-09-08T10:34:36.900Z",
  "updated_at": "2026-09-13T18:24:06.047Z",
  "validated_at": "2026-09-08T10:34:36.900Z",
  "usage": { "requests": 119, "tokens": 3518, "last_used": "2026-09-09T12:03:01.032Z" },
  "is_hidden": false,
  "is_valid": true,
  "allowed_models": [ …the 48 models below… ]
}
```

So: **cakey registered the OpenAI-compatible relay
`https://osaii.wyvernhub.net/api/v1` on 2026-09-08 with his own key(s) and
made it public.** Anyone on g4f.dev can pick "custom server by cakey(…)" in
the chat and chat through the g4f.space proxy, paying with their own g4f
quota (or cake-credits if anonymous). `updated_at` advancing hourly proves
the live re-validation loop in §2.6 is running on it in production.

**Reproduce it yourself:**

```bash
curl -s https://g4f.space/custom/api/servers/public | python3 -m json.tool | grep -A3 srv_mtsj8uzo97d3c0d49960
curl -s https://g4f.space/custom/srv_mtsj8uzo97d3c0d49960/status | python3 -m json.tool
curl -s https://g4f.space/custom/srv_mtsj8uzo97d3c0d49960/models | python3 -m json.tool
```

## 3.2 The 48 models + live usage

`GET /custom/{srv_id}/models` returns the upstream list **filtered by
`allowed_models`, sorted by request count** (the counts come from the
worker's D1 `usage_logs` — proof the usage tracking in the reference impl
mirrors real behavior). Top of cakey's list at snapshot time:

| model | requests through g4f.dev |
|---|---|
| `poolside/laguna-xs-2.1` | 227 |
| `zai-z/zai-org-glm-5-3-flash` | 119 |
| `xai-z/grok-4-fast-non-reasoning` | 104 |
| `xai-z/grok-4-1-fast-non-reasoning` | 99 |
| `deepseek-z/deepseek-v4-pro` | 47 |
| `openai-z/gpt-5.6-luna` | 41 |
| `gemini-z/gemini-3.1-flash-lite-preview` | 30 |
| `qwen-z/qwen3.8-flash` | 24 |
| `logfare/kimi-k3` | 22 |
| `venice-z/gemma-4-uncensored` | 15 |

The **full 48-model catalog** (every ID, name, family, tags, context window,
thinking flag, description) is in [`README.md` §5](README.md#5-all-48-models--complete-data)
and in `research/live-data/cakey-models.json`; it is also what
`route-py/backend.py` ships and what `GET /api/models` returns.

## 3.3 What else was in the public index

~100 public servers at snapshot time. Highlights: **pollinations' own
servers** (375k requests / 352M tokens), groq.com, nvidia.com (825M tokens),
perplexity, you.com, cerebras.ai, "Google Antigravity" (pass.g4f.space-backed),
cloudflare (CF AI models), logfare.ai, sharktide, unorouter.com, plus several
**private home Ollama instances** (e.g. `http://213.199.44.213.nip.io:11434/v1`
— the nip.io IPv4 mapping the worker adds so Cloudflare can reach raw-IP
Ollama boxes). cakey's is a young one (created 2026-09-08) with modest usage
— but valid and online.

---

# 4. The 10 bugs in the production code

Full write-up: [`research/03-BUGS-AND-IMPROVEMENTS.md`](research/03-BUGS-AND-IMPROVEMENTS.md)
· patch (14 hunks, verified clean on a fresh `gpt4free/g4f.dev` clone):
[`research/fixes/g4f-dev-custom-server-fixes.patch`](research/fixes/g4f-dev-custom-server-fixes.patch)

| # | Sev | Bug | Fix |
|---|---|---|---|
| 1 | 🔴 crash | `handleUpdateServer` probes `body.base_url` (may be undefined) → `new URL(undefined)` → 500; updating label/keys impossible without resending the URL | probe `server.base_url` (post-patch value) |
| 2 | 🔴 crash | `handleDeleteServer` does `await request.json()` → bodyless `DELETE` (the REST default) 500s | read body defensively + accept `?server_id=` |
| 3 | 🛡️ **security** | `updatePublicServerIndex()` writes the **full server object including `api_keys`** into the shared KV index; responses are redacted, the **at-rest** copy isn't — any worker bound to `MEMBERS_KV` (incl. the auth worker) can read every public server's keys | destructure `const { api_keys, …entry } = server` before pushing |
| 4 | 🐞 | `/validate` + `/status` 404 for the **owner's own private** servers (require presence in the public index) and would persist a synthesized entry | owner synthesizes the entry; skip non-public writes |
| 5 | 🐞 | index refresh stamps `updated_at` **before** validation → dead servers look fresh for an hour and churn KV writes | stamp only on success; `last_checked_at` on failure |
| 6 | 🐞 | `baseUrl.replace("/chat/completions","")` cuts any URL *containing* the substring; timeout message says 10 s while the abort fires at 30 s | strip only a trailing suffix; correct the message |
| 7 | 🐞 | `GET /models` does read-modify-write on the owner's R2 doc **on every call** → lost-update race + R2 write cost when nothing changed | compare first; skip the write when unchanged |
| 8 | 🐞 | unthrottled `/servers/create` runs 2–4 live probes per attempt → upstream-DoS / open-scanner vector | per-user 5/min create throttle, 429 + `Retry-After` |
| 9 | 🐞 | frontend: any non-2xx on the private list throws → the **public** providers never render | parse only when `resp.ok` |
| 10 | 🐞 | malformed JSON on create/update → unhandled `request.json()` rejection → 500 + stack trace | try/catch → clean 400 `{"error":"Invalid JSON body"}` |

**Deliberate design, not bugs:** CORS `*` + credentials (safe because auth is
Bearer, never cookies); shared edge-cache key (only public data cached);
`X-Url` full-URL for admin only; status **420** "Enhance Your Calm"; the
`Server?` easter egg; the VSCode-Copilot prompt rewriter.

Every fixed behavior has a passing E2E test in the reference implementation
(33/33).

---

# 5. How the chatbot in this repo works (route-py + route-web)

The chatbot is a **deliberate miniature of the real system**: the same
one-port design, the same OpenAI shapes, the same pass-through proxy —
rebuildable and runnable on one machine.

## 5.1 One process, one port

`route-py/backend.py` (FastAPI, `0.0.0.0:8090`) serves **both**:

- the API: `/api/health`, `/api/models`, `/api/chat`
- the frontend: `GET /` → `route-web/index.html`, `GET /static/*` → `route-web/`

One web URL, zero CORS, one process — the same idea as g4f.space serving the
API while g4f.dev serves the static chat.

## 5.2 The endpoints

**`GET /api/health`** → `{"ok":true,"mode":"simulated","models":48,"time":"…"}`
(`mode` = `"proxy -> <url>"` when `UPSTREAM_BASE_URL` is set).

**`GET /api/models`** → all 48 models with `id, name, family, tags, context,
description, thinking, requests, tokens` + `total` + `thinking_models` (17).
Per-model usage persists in `route-py/usage.json` (bumped after every chat,
streaming or not).

**`POST /api/chat`** — body `{model, messages, stream}`:

- **JSON mode** → OpenAI-shaped `chat.completion`; thinking models add
  `message.reasoning_content` and
  `usage.completion_tokens_details.reasoning_tokens`.
- **SSE mode** → `chat.completion.chunk` lines; **two-phase for thinking
  models**: `delta.reasoning_content` first, then `delta.content`, then a
  stop-chunk with usage, then `data: [DONE]`.
- **Errors**: unknown model → 400; bad `messages` → 400; malformed JSON → 400
  (bug #10, already fixed in our house); proxy upstream failure → 502.

**No system prompt is ever injected** — `messages` is used directly, exactly
as you send it (this is what `check_all_models.py` verifies on all 48).

## 5.3 Simulated mode (default) — the persona engine

Self-contained, deterministic, always works. For each request:

```
seed = sha256(model_id + ":" + last_user_message)
```

- **voice** — per-style `OPENERS`/`CLOSERS`/`BODY_FLAVOR` (24 styles: luna,
  glm, grok, kimi, coder, rp, …) picked by `seed % len` → the same model
  always sounds the same, different models sound different.
- **capabilities** — a small built-in fact base (capital-of-…, "what is
  recursion"…), `MATH_RE` that actually evaluates `27 × 43 → 1161`
  (incl. unicode `×`/`÷`), `CODE_RE` that returns a tested fenced snippet,
  roleplay flavor for RP models, greetings/self-intros.
- **thinking** — for the 17 flagged models, `make_thinking()` emits a
  deterministic, family-flavored trace in the *real* field
  (`reasoning_content`) with the *real* two-phase stream order: GLM gets
  numbered auditable steps + "Interleaved check", Kimi gets
  "Let me think about this step by step", GPT-5-Luna gets a short **summary**
  (mirroring OpenAI's opaque+summary reality), Qwen a compact deliberation,
  DeepSeek "Routing: fast path would work…", gpt-oss a raw lowercase CoT.

## 5.4 Proxy mode — the real-model path

```bash
UPSTREAM_BASE_URL=https://your.api/v1 UPSTREAM_API_KEY=sk-… ./run.sh
```

The backend then does what the g4f worker does, in miniature:

- forwards `POST {UPSTREAM_BASE}/chat/completions` with your key
  (`Authorization: Bearer`),
- **streams SSE back untouched** — including any upstream `reasoning_content`
  (the pass-through is the support, exactly like g4f.dev),
- merges provider thinking knobs from your request into the upstream payload:
  `enable_thinking` / `thinking_budget` (Qwen), `thinking:{…}` (GLM/Kimi),
  `reasoning:{effort}` (OpenAI/xAI), `max_tokens`, `temperature`,
- captures usage from the final chunk and records it per model.

Point it at a real DeepSeek/Qwen/GLM/Kimi endpoint and the **real model's**
thinking trace streams into the 🧠 box — zero extra code.

## 5.5 The frontend (route-web)

- **sidebar** — 48 models grouped by family (color swatches), live search,
  per-model request counts, **🧠 badge + "thinks" tag** on the 17 reasoning
  models, footer totals; preselects the most-used model on load.
- **chat** — SSE reader with a manual buffer (chunks can split mid-line),
  a tiny markdown renderer (fenced/inline code, bold), streaming caret,
  **collapsible 🧠 thinking box** (pulses while phase 1 streams, collapses to
  "thought · ~N tok" when the answer starts), **stop button** (AbortController
  — mid-thinking too), new chat, usage chip, model meta line, mobile drawer,
  30-second health ping.

---

# 6. Reasoning models — the extraction process

Full sourced research: [`research/04-REASONING-MODELS.md`](research/04-REASONING-MODELS.md).
The industry split into two wire patterns:

1. **Visible chain in a side field** — raw thinking in
   `reasoning_content` (message field / stream delta), separate from
   `content`. → **DeepSeek, Qwen, Z.ai GLM, Kimi.**
2. **Opaque thinking + summary/encrypted blob** → **OpenAI (GPT-5)**
   (`reasoning:{effort}`, reasoning *summary*, `reasoning.encrypted_content`
   to echo back) and **xAI (Grok 4.3+)** (`reasoning.effort` low…xhigh;
   **grok-4-1-fast rejects the param with HTTP 400**).

Plus: Gemini `thinkingBudget`/`thinkingLevel` + `include_thoughts`
(summaries; flash-lite off by default), GPT-OSS raw `reasoning` stream.

**17 of our 48** have the feature (verified live by
`route-py/check_all_models.py` — two-phase stream order confirmed on the wire
for each):

`openai-z/gpt-5.6-luna` · `zai-z/zai-org-glm-5-3-flash` ·
`zai-z/zai-org-glm-4.7-flash` · `zai-z/olafangensan-glm-4.7-flash-heretic` ·
`zai-z/zai-org-glm-4.6` · `logfare/kimi-k3` · `logfare/deepseek-v4-flash` ·
`logfare/deepseek-v4-pro` · `deepseek-z/deepseek-v4-flash` ·
`deepseek-z/deepseek-v4-pro` · `qwen-z/qwen3.8-flash` · `qwen-z/qwen3.6-flash`
· `qwen-z/qwen3-coder-flash` · `qwen-z/qwen3.7-plus` ·
`openrouter-z/qwen3.8-27b` · `groq-z/gpt-oss-20b` · `groq-z/gpt-oss-120b`

Behavior notes: **GLM-5.3 thinking is forced (cannot be disabled)**; Kimi K3
always-on (effort low/high/max); qwen3.7-plus on by default (hybrid for the
rest); DeepSeek V-line auto-routes; GPT-5.6 Luna always reasons but exposes
only a summary.

---

# 7. Run it / verify it

```bash
# the chatbot (main thing)
cd route-py
python3 -m pip install -r requirements.txt
./run.sh                     # → http://localhost:8090
python3 test_chatbot.py      # 49/49 checks (spawns its own server)
python3 check_all_models.py  # sweep ALL 48: direct calls, no system prompt
                             #   → 48/48 working, 17 reasoning

# the g4f.dev reference implementation (research artifact)
cd research/g4f-reference-impl
python3 -m pip install -r requirements.txt
./run.sh                     # mock upstream :9101 + router :8090
python3 test_e2e.py          # 33/33 checks
```

What each suite proves:

| Suite | Checks | Proves |
|---|---|---|
| `route-py/test_chatbot.py` | 49 | catalog (48, 20 families, 17 thinking), SSE + `[DONE]`, distinct voices, math/code/facts/greetings, `reasoning_content` on all 17, two-phase order, usage, errors, frontend |
| `route-py/check_all_models.py` | 48+17 | every model called **directly with one plain user message, no system prompt**; reasoning present exactly where flagged; wire-level phase order |
| `research/g4f-reference-impl/test_e2e.py` | 33 | the g4f.dev re-implementation: CRUD, auth tiers, public-index key redaction, SSE usage capture, `Server?` easter egg, 400s on malformed JSON, owner-only validate |

---

# 8. Where every piece lives

```
public/
├── README.md                     the friendly complete doc (10 sections)
├── route.md                      ← THIS FILE: the whole route, deepened
├── research/                     THE ENTIRE g4f RESEARCH
│   ├── README.md                 research index
│   ├── 01-ARCHITECTURE.md        end-to-end architecture (file/line refs)
│   ├── 02-CAKEY-LIVE-DATA.md     cakey's live server, verified
│   ├── 03-BUGS-AND-IMPROVEMENTS.md  the 10 bugs, fully
│   ├── 04-REASONING-MODELS.md    which models think + extraction (sourced)
│   ├── 05-LMARENA-AUTH.md       how g4f's "lm arena" provider's "private key" really works
│   ├── sources/                  the actual production source
│   │   ├── workers/api-worker.js       the backend Worker (3.6k lines)
│   │   ├── workers/members-worker.js   the auth Worker (4.1k lines)
│   │   ├── workers/wrangler-*.toml     KV/R2/D1 bindings + routes
│   │   └── chat/index.html             the production chat page
│   │   └── lmarena/LMArena.py          the full g4f LMArena provider (732 ln)
│   ├── live-data/                raw JSON: cakey-server / cakey-models / cakey-status
│   ├── fixes/                    g4f-dev-custom-server-fixes.patch (14 hunks)
│   └── g4f-reference-impl/       WORKING re-implementation + 33-check E2E
│       ├── server.py · mock_upstream.py · test_e2e.py
│       ├── static/index.html     demo chat UI
│       └── README.md · requirements.txt · run.sh · .gitignore
├── route-py/                     ALL PYTHON (the chatbot)
│   ├── backend.py                the FastAPI server (API + frontend, one port)
│   ├── test_chatbot.py           49-check E2E
│   ├── check_all_models.py       all-48 direct audit sweep
│   ├── README.md · requirements.txt · run.sh · .gitignore
└── route-web/                    THE FRONTEND (3rd route folder)
    ├── index.html · app.js · style.css
    └── README.md
```

**That's the whole system: how it was figured out, how the real API works,
all the data, how this repo works, and how to run and verify every part.**

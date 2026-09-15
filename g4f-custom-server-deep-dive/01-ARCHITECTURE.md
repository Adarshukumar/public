# 01 — Architecture: how the g4f.dev custom-server feature actually works

Everything below is verified against (a) the repo `gpt4free/g4f.dev` @ `9d075d7` (copies in
[`sources/`](sources/)) and (b) the live production API at `g4f.space` (snapshots in
[`live-data/`](live-data/)).

## 1. The big picture

```
┌─────────────────────────────  g4f.dev (static frontend)  ─────────────────────────────┐
│                                                                                        │
│  chat/index.html  ── loads (chunked)  ──>  dist/js/v2.js  (addon loader)               │
│     dist/js/providers.js        createClient("custom:srv_…")                           │
│     dist/js/client.js           Client class (fetch/SSE streaming)                     │
│     dist/js/addons/addon-init.js      provider dropdown + custom-server loading        │
│     dist/js/addons/addon-providers-models.js  API-key resolution per provider          │
│     dist/js/addons/addon-picker.js    new provider-pick modal (also lists custom srvs) │
│     dist/js/addons/addon-legacy.js    hash routing ("#custom:…" = conversation id)     │
└────────────────────────────────────────────────────────────────────────────────────────┘
                                   │  fetch (Bearer <g4f_session>)
                                   ▼
┌────────────────────  g4f.space (Cloudflare Worker, workers/api-worker.js)  ───────────┐
│                                                                                        │
│  safe(request, env, ctx)      — master router (line ~294)                              │
│     ├─ /custom/api/servers/*   — server CRUD + public index (KV: MEMBERS_KV)           │
│     ├─ /custom/{srv_id}/…      — proxy to the owner's upstream (R2: user documents)    │
│     ├─ /v1/chat/completions    — "auto" routing (random public server)                 │
│     └─ everything else         — pass.g4f.space passthrough (core g4f providers)       │
│                                                                                        │
│  storage:                                                                              │
│     MEMBERS_KV     — sessions, users (1h cache), public_servers_index, server:{id}     │
│     MEMBERS_BUCKET (R2) — users/{id}.json (source of truth, incl. api_keys)            │
│     USAGE_DB (D1)  — usage_logs (per-request token stats, 14-day retention)            │
│     ERRORS_DB (D1) — error_logs                                                        │
│     caches.default — edge HTTP cache (public GETs + repeated test prompts)             │
└────────────────────────────────────────────────────────────────────────────────────────┘
                                   │  fetch (Bearer <OWNER's key>)
                                   ▼
                    owner's OpenAI-compatible upstream
                    (for cakey: https://osaii.wyvernhub.net/api/v1)
```

**Key insight: the g4f.dev chat never talks to the owner's server directly.** The
Cloudflare Worker is a *key-injecting proxy*: it hides the owner's API keys from all users
and hides the owner's endpoint from everyone except the owner themselves.

## 2. The data model

A "custom server" is a plain JSON object stored inside the owner's user document
(R2 `g4f-members/users/{user_id}.json`, KV-cached 1h as `user:{id}`):

```json
{
  "id": "srv_mtsj8uzo97d3c0d49960",          // generateServerId(): srv_ + base36(Date.now()) + 12 hex (workers/api-worker.js:2323)
  "label": "custom server by cakey(Luna, GLM 5.3 Flash, Kimi K3, etc)",
  "base_url": "https://osaii.wyvernhub.net/api/v1",   // owner's OpenAI-compatible endpoint
  "api_keys": "key1\nkey2\n# comments ok",            // line-separated pool, one picked at random per request
  "allowed_models": ["openai-z/gpt-5.6-luna", "zai-z/zai-org-glm-5-3-flash", "logfare/kimi-k3", …],
  "auto_update_models": true,                 // re-discover models on validate/update/GET /models
  "is_public": true,                          // if true: listed in the public index, usable by ANY visitor
  "is_ollama": false,                         // detected by probing "/" for an "Ollama" banner (workers/api-worker.js:2329)
  "default_model": null,
  "created_at": "2026-09-08T10:34:36.900Z",
  "updated_at": "…", "validated_at": "…",
  "usage": { "requests": 119, "tokens": 3518, "last_used": "2026-09-09T12:03:01.032Z" }
}
```

Plus two derived registries:

* **`public_servers_index`** (KV) — array of public server entries, maintained by
  `updatePublicServerIndex()` (api-worker.js:2303). ⚠️ In the production code it stores the
  *full* server object **including `api_keys`** — see the security note in
  [`03-BUGS-AND-IMPROVEMENTS.md`](03-BUGS-AND-IMPROVEMENTS.md).
* **`SERVER_MAP`** / **`SERVER_TO_PROVIDER`** (built from `dist/js/providers.json`,
  api-worker.js:130–167) — the handful of "first-party" providers (pollinations, huggingface,
  airforce, …) that also have `srv_…` IDs and can be addressed by subdomain
  (`{label}.g4f.space`). For those, `getServerById()` additionally injects the *caller's*
  OAuth token for that provider (api-worker.js:2230–2234).

## 3. Backend: request lifecycle (`workers/api-worker.js`)

### 3.1 Master router — `safe()` (line ~294)

Order of operations for every request:

1. `OPTIONS` → CORS headers. CORS is fully open (`Access-Control-Allow-Origin: *`) so the
   g4f.dev frontend (and the public site in general) can call it.
2. Auth — `authenticateRequest()` (line ~726), three accepted credential forms:
   * `Authorization: Bearer gfs_…` **session** token (from g4f.dev login, stored in the
     browser as `g4f_session` in localStorage),
   * `Authorization: Bearer g4f_…` **API key** (hashed lookup in KV `api_key:{sha256}`),
   * `X-API-Key: g4f_…` header.
   Sessions resolve to a user document from R2 (KV-cached 1h). **Most public-server calls
   work completely unauthenticated** — `user` may be `null`.
3. Rate limiting — `checkUserRateLimits()` (line ~2885) enforces per-tier token/request
   windows from `USER_TIER_LIMITS` (line ~31): e.g. `free` = 1e6 tokens/day, 500 req/day,
   10 req/min, max 10 servers; `admin` = 100 servers. Anonymous callers are gated later by
   the "baked cake" proof-of-work credits (`addon-baked-credits` / `cake-baker.js` — the
   browser literally mines SHA-256 hashes to earn 0.05¢ credits that fund anonymous usage).
   Custom-API CRUD paths (`/custom/api/*`) bypass the token limiter but hit a separate
   10-second KV limiter (`RATE_LIMIT`, status `420` 😄).
4. Pathname rewrites (line ~313):
   * `/public` → `/custom/api/servers/public`
   * `/usage` → `/custom/api/servers/usage`
   * `/srv_XXXX` → `/custom/srv_XXXX`
5. Cache check for anonymous GETs (`generateCacheKey()`, line ~3446 — note: the key is the
   URL **minus** `seed/url/model` params, i.e. shared across users; only safe because the
   cached resources are public data).
6. The routing table (the heart of it):

| Path | Handler | What happens |
|---|---|---|
| `/custom/api/servers` | `handleListServers` (804) | own servers, secrets stripped, `api_key_count` instead |
| `/custom/api/servers/create` | `handleCreateServer` (828) | validate upstream → assign `srv_…` id → store in user doc → maybe add to public index |
| `/custom/api/servers/update` | `handleUpdateServer` (907) | patch allowed fields, re-validate, refresh public index |
| `/custom/api/servers/delete` | `handleDeleteServer` (971) | tombstone in R2 + remove from public index |
| `/custom/api/servers/usage` | `handleGetServerUsage` (1006) | per-day history from R2 |
| `/custom/api/servers/public` | `handleListPublicServers` (1064) | public index + lazy re-validation (max 10 stale servers/pass, hourly budget) |
| `/custom/{srv_id}/models` | `handleModels` (1186) | upstream `/models` filtered by `allowed_models`, sorted by usage, cached 1h |
| `/custom/{srv_id}/chat/completions` | `handleProxyToServer` (1247) | the actual proxy (below) |
| `/custom/{srv_id}/validate` / `/status` | inline (589) | live `validateServer()` + `isOnline()` check |
| `/custom/{anything else}` | `handleProxyToServer` | generic sub-path proxy (quota, images, audio, …) |
| `/v1/chat/completions` | `handleV1ChatCompletions` (3180) | "auto" routing: pick a random public server, fallback chain |
| `/api/{label}/…` | `getServerByLabel` (2264) | label → server (own labels first, then public, then `pass.g4f.space/api/{label}`) |
| *everything else* | `proxyToPassG4f` (3542) | passthrough to `pass.g4f.space` (the core g4f provider farm) with the owner-portal secret; also enforces `BLOCKED_ORGS` (datacenter ASN blocklist) for anonymous callers |

### 3.2 The proxy — `handleProxyToServer()` (line ~1247)

This is the most interesting function. In order:

1. **Read the body** (POST) → `requestModel`, last message content.
2. **Content surgery** — a big block of regexes that *replaces* Microsoft VSCode Copilot
   system prompts (detected via a distinctive snippet) with a shorter "expert coding agent"
   prompt, strips ~30 VSCode-specific tool schemas, and softens ALL-CAPS boilerplate
   (`IMPORTANT:`/`NEVER`/`MUST` → calmer wording). It counts the saved bytes. (This is why
   g4f.dev is popular with VSCode-Copilot-bridge users: it saves prompt tokens.)
3. **Abuse filters**: a hardcoded Russian SEO-spam prompt is blocked with 403; and the
   easter egg — if the last message is exactly `Server?`, it short-circuits and returns
   `"{label} - Server ID: {id}"` without touching the upstream.
4. **Pick a key**: `getRandomApiKey(server.api_keys)` (line ~2317) — uniformly random line
   from the owner's key pool (so the owner can rotate/load-balance across keys). For
   `SERVER_TO_PROVIDER` servers (pollinations etc.) the *caller's* OAuth token is used
   instead.
5. **Build the target URL**:
   ```js
   if (server.base_url.includes(subPath))        targetUrl = server.base_url;
   else if (server.base_url.includes("/v1/chat/completions")) targetUrl = base.split("/v1/")[0] + subPath;
   else                                            targetUrl = `${server.base_url}${subPath}`;
   ```
   Then `URL_MAP` rewrites a few known quota URLs. Special case: `api.you.com/v1` gets
   converted to their `/answer` API and its response is re-wrapped into OpenAI shape.
6. **Forward** with the original method/headers (plus the owner's `Authorization`,
   `x-user`, `x-secret`, `HTTP-Referer: https://g4f.dev`, `X-OpenRouter-Title: GPT4Free`).
   `pass.g4f.space` targets additionally get the internal `g4f-api-key` secret.
7. **Stream or JSON**:
   * streaming → the response is piped back as SSE, but through
     `createUsageTrackingStream()` (line ~1972): a tee that parses every `data:` line to
     capture `usage` (prompt/completion/total tokens, or pollinations' `pollen_cost`),
     then — via `ctx.waitUntil` (non-blocking) — persists to `USAGE_DB` (D1
     `usage_logs`), bumps the server's `usage` counters in the owner doc, bumps the user's
     daily usage, and for anonymous callers charges *cake credits* for what the user
     actually spent.
   * non-streaming → proxied as-is; repeated canned test prompts (`"hi"`, `"ping"`, …) are
     deduped by `generatePostBodyHash()` (line ~3464) and served from the edge cache.
8. **Response headers**: `X-Server: {srv_id}`, `X-Provider: {label}`, `X-Url: <path or
   full URL — full URL only for admin tier>`, `X-User-Id`, `X-User-Tier`,
   `X-Ratelimit-*` remainders. **The frontend uses `X-Server` to tag every streamed message
   with `provider = "custom:" + server`** (addon-legacy.js:1772/1819) — that string is what
   ends up in conversation data and in the share links.

### 3.3 Server validation — `validateServer()` (line ~2397) + `isOnline()` (line ~2340)

On create/update (and on the hourly public-index refresh):

1. Strip a trailing `/chat/completions` from the base URL (the code uses
   `.replace("/chat/completions","")` — see bug #6 in the report).
2. If a default model is known → fire a real `POST /chat/completions` with `"Hello"`
   (30 s timeout) to prove the pipeline works end-to-end. 401/403 →
   "Authentication failed - check your API keys".
3. Otherwise probe `GET {base}/models`, then `{base}/v1/models`, then the https-upgrade of
   an http URL — accept `{"data":[{id}]}` (OpenAI shape), `{"models":[…]}` or a bare array;
   401/403 → auth error; anything else → "Cannot connect to server".
4. Models found → `auto_update_models` stores them in the server record.
5. `isOnline()` (used by `/validate` and the public-index refresh) actually sends
   `{"messages":[{"role":"user","content":"hi"}]}` and checks for `choices[0].message.content`.

### 3.4 The public-index refresh — `handleUpdatePublicServers()` (line ~1074)

Every call to `/custom/api/servers/public` (and it's called on **every chat page load**):

* entries with `updated_at` older than 1 hour become re-validation candidates,
* at most 10 are checked per pass (each = 1–3 upstream HTTP calls),
* valid servers get fresh `allowed_models`, `base_url`, `is_valid: true`, and their
  `updated_at` stamped (see bug #4: the production code stamps *before* validating),
* the result is cached in `caches.default` for up to 24h when nothing was refreshed.

That's how cakey's 48-model list stays current: `auto_update_models: true` + hourly refresh
+ `GET /models` refreshes on every model-list request.

## 4. Frontend: from URL to answer

### 4.1 Page load (`chat/index.html` → `dist/js/v2.js`)

`v2.js` is a chunked addon loader: it injects each `dist/js/addons/addon-*.js` as a module
in dependency waves — `framework,core,worker,load` → `legacy,init` → … → `picker`. Each
addon exports its globals onto `window`. The framework object carries `backendUrl`
(`https://g4f.space` in production) and helpers.

### 4.2 Provider dropdown population

* `updateLiveProviderOptions()` (addon-init.js:553) adds the first-party providers from
  `dist/js/providers.json` (pollinations, huggingface, airforce, gemini, ollama, nvidia,
  …). Each carries `data-server-id`.
* `loadCustomProvidersFromAPI()` (addon-init.js:577) — the custom-server loader:
  1. if the user has a `g4f_session`: `GET https://g4f.space/custom/api/servers`
     (private servers) — 401 clears the stale session;
  2. always: `GET https://g4f.space/custom/api/servers/public` (public index);
  3. merges (private first, deduped by id), drops servers already present as first-party
     providers, stores them in `window.customServers`, and for each creates
     `<option value="custom:{srv_id}">` with label + `(N models)` and an enable/disable
     checkbox persisted as `enableCustomServer_{id}` in localStorage.
  The newer `addon-picker.js` (the modal provider picker) does the same with retries and
  builds `baseUrl: https://g4f.space/custom/{id}` per server.

### 4.3 Sending a message

1. User picks provider `custom:srv_mtsj8uzo97d3c0d49960` + model → `initClient()`
   (addon-init.js:1309) → `get_api_key_by_provider("custom:…")` →
   `g4f_session` (addon-providers-models.js:221).
2. `createClient("custom:srv_…")` (**dist/js/providers.js:91**) — the pivotal 4 lines:
   ```js
   if (provider.startsWith("custom:")) {
       serverId = provider.substring(7);
       options.baseUrl = `https://g4f.space/custom/${serverId}`;
       provider = "custom";
   }
   ```
   → a generic `Client` (dist/js/client.js) with `apiEndpoint =
   https://g4f.space/custom/{srv_id}/chat/completions`. (The `Client` also keeps a
   `CorsProxyManager` — a failover list of public CORS proxies — for the cases where it
   talks to a user-supplied custom base URL directly.)
3. `ask_gpt()` (addon-legacy.js) POSTs `{model, messages, stream:true, conversation…}`
   with `Authorization: Bearer <g4f_session>`; chunks are parsed as SSE
   (`data: {...}` lines) and rendered with a typing effect. The first chunk's
   `X-Server`/`X-Provider` headers are read via `fetch`'s `response.headers` and the
   message is tagged `provider = "custom:" + server`.
4. The Web Worker route: `options.fetchFn = window.fetchFn` funnels fetches through a
   service-worker-ish wrapper so streaming survives tab backgrounding.

### 4.4 The `#custom:srv_…` hash

The chat treats the URL fragment as a **conversation ID** (addon-load.js:284 `on_load()`,
addon-legacy.js:3039 hashchange listener; addon-settings.js:295 is the third copy):

* `login` / `menu` / `settings` / `new` / `private` / `session=…` have special meaning;
* anything else → `window.conversation_id = <hash>`; if not in local storage the app
  fetches it from the backend: `GET {backend}/backend-api/v2/chat/{conversation_id}`
  (addon-load.js:207) — i.e. **shared conversations**.

So `https://g4f.dev/chat/#custom:srv_mtsj8uzo97d3c0d49960` is a deep link to a conversation
whose ID is literally `custom:srv_mtsj8uzo97d3c0d49960` (conversations run *with* a custom
server get tagged with that string on every message, and shared conversations keep it).
It is *not* a "select this provider" hash — provider selection happens in the dropdown
(filled from the public index as described above) or in the picker modal. (I verified all
three hash handlers in the repo; none parse a `custom:` provider prefix from the hash.)

## 5. Security & design notes (what they got right, what they didn't)

**Good:**
* Owner keys never leave the worker (responses strip `api_keys`; only `api_key_count` is
  exposed); upstream host is hidden from non-owners (`X-Url` = pathname only).
* Random key selection per request = free load balancing / rotation.
* D1 audit trail (usage + errors) with retention, per-tier rate limits, ASN blocklist for
  anonymous datacenter traffic, cake-credit gate for anonymous users, edge caching of
  public data and repeated test prompts, 14-day usage log cleanup cron.

**Weak (fixed in this project — see 03-BUGS-AND-IMPROVEMENTS.md):**
* **API keys at rest in the shared public index** (KV) — any worker bound to `MEMBERS_KV`
  (e.g. the members/auth worker) can read every public server's keys.
* Two outright crash bugs (update without `base_url` → 500; bodyless `DELETE` → 500).
* `/validate`+`/status` 404 for private servers even for their owner.
* Public-index refresh spends its budget stamping dead servers.
* GET `/models` rewrites owner data on every call (lost-update race).
* Unthrottled `/servers/create` = upstream DoS vector (every create triggers live probes).
* CORS fully open + `Access-Control-Allow-Credentials: true` (works because auth is in a
  Bearer header, not cookies — but it's a footgun).

## 6. Where each file lives (copies in `sources/`)

| Concern | File(s) |
|---|---|
| Backend router (the custom-server brain) | `sources/workers/api-worker.js` (3.6k lines) |
| Auth / members / OAuth / API keys | `sources/workers/members-worker.js` (4.1k lines) |
| Deployment config (KV/R2/D1 bindings, routes) | `sources/workers/wrangler-custom.toml`, `wrangler-members.toml` |
| Provider registry (first-party `srv_…` map) | `sources/dist/js/providers.json` |
| Client factory (`custom:` → proxy base URL) | `sources/dist/js/providers.js` |
| Chat client (SSE streaming, CORS proxy failover) | `sources/dist/js/client.js` |
| Custom-server dropdown + public index loading | `sources/dist/js/addons/addon-init.js` |
| Per-provider API key resolution | `sources/dist/js/addons/addon-providers-models.js` |
| New provider picker modal | `sources/dist/js/addons/addon-picker.js` |
| Hash routing, message send/stream, provider tagging | `sources/dist/js/addons/addon-legacy.js` |
| Shared-conversation fetch on load | `sources/dist/js/addons/addon-load.js` |
| Addon loader (chunk waves) | `sources/dist/js/v2.js` |
| Proof-of-work "cake baker" (anonymous credits) | `sources/dist/js/cake-baker.js` |
| Chat page | `sources/chat/index.html` |

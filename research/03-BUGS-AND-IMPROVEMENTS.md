# 03 — Bugs found in the production custom-server code (+ fixes)

All bugs are in `gpt4free/g4f.dev` as of commit `9d075d7`. Fixes are applied in
[`fixes/g4f-dev-custom-server-fixes.patch`](fixes/g4f-dev-custom-server-fixes.patch)
(14 hunks, syntax-checked with `node --check`, verified to apply cleanly to a fresh
upstream clone) and are all mirrored in the reference implementation, where each fixed
behavior has a passing E2E test.

Legend: 🔴 crash / denial-of-service · 🛡️ security · 🐞 functional bug

---

### 🔴 #1 — `handleUpdateServer` crashes when the payload omits `base_url`

`workers/api-worker.js` (~line 967):

```js
server.is_ollama = await isOllama(body.base_url);   // body.base_url may be undefined!
```

`isOllama()` starts with `url = new URL(url)` → `TypeError: Failed to parse URL` → the
whole update 500s. Updating just the label, `is_public` flag or `api_keys` (all perfectly
valid operations — the UI's enable/disable toggle and key rotation do exactly this) is
impossible without also resending `base_url`.

**Fix:** probe `server.base_url` (the value after the patch is applied).

```js
server.is_ollama = await isOllama(server.base_url);
```

*Reference-impl test:* `[8] label-only update 200 (was 500 in original)` ✅

---

### 🔴 #2 — `handleDeleteServer` 500s on bodyless `DELETE`

```js
if (request.method !== "POST" && request.method !== "DELETE") { … }
const body = await request.json();        // ← throws when the client sends no body
```

REST clients (and the browser's `fetch(url, {method:"DELETE"})` default) send no body →
`request.json()` rejects → unhandled → 500. The delete endpoint is only usable as `POST`
with JSON today.

**Fix:** read the body defensively, and also accept `?server_id=…`:

```js
let body = {};
try { const text = await request.text(); if (text) body = JSON.parse(text); } catch (e) {}
const serverId = body.server_id || new URL(request.url).searchParams.get("server_id");
```

*Reference-impl test:* `[10] bodyless DELETE 200 (was 500 in original)` ✅

---

### 🛡️ #3 — `api_keys` of every public server are stored in plaintext in the shared KV

`updatePublicServerIndex()` pushes the **full server object** (including the
`api_keys` field) into `MEMBERS_KV["public_servers_index"]`. Responses are redacted
(`delete s.api_keys` in `handleListPublicServers`), but the *at-rest* copy is not.
Consequences:

* every worker bound to `MEMBERS_KV` — including the members/auth worker
  (`wrangler-members.toml` binds the same namespace id) — can enumerate all public
  servers and read their owners' API keys;
* KV snapshots/backups and any future binding grant the same exposure;
* it also bloats the index and its 24h edge-cached responses.

The keys are never actually needed from the index: `getServerById()` re-reads the full
server (with keys) from the **owner's user document** for public servers (line ~2241).

**Fix:** redact before persisting:

```js
const { api_keys, ...indexEntry } = server;
servers.push(indexEntry);
```

*Reference-impl test:* `[2] public index file has NO api keys (fix #5)` ✅

---

### 🐞 #4 — `/custom/{id}/validate` and `/status` 404 for the owner's private servers

Both routes resolve the server with `getServerById()` (which **does** find private
servers for the owner), but then require the server to also be present in the **public**
index:

```js
const entry = publicServers.find(s => s.id == server.id);
if (!entry) return jsonResponse({ error: "Server not found" }, 404);
```

So an owner can never health-check a private server — and the subsequent
`MEMBERS_KV.put("public_servers_index", …)` would have persisted an owner-synthesized
entry into the public index.

**Fix:** if the caller is the owner, synthesize the entry from the owned server and skip
the public-index write for entries that aren't actually public.

*Reference-impl test:* `[9] owner validate 200` ✅

---

### 🐞 #5 — public-index refresh stamps `updated_at` before it knows validation succeeded

`handleUpdatePublicServers()` (line ~1107) sets `s.updated_at = now` **at the top** of
the re-validation loop, before probing the server. Effects:

* a dead server consumes one of the hourly 10-check budget *and* looks fresh, so its
  `is_valid: false` / `test_result` error state is masked for up to an hour even though
  the entry was just re-checked and still broken;
* offline servers churn the index write (`ct > 0` → KV put) on every pass.

**Fix:** stamp `updated_at` only on success; record `last_checked_at` on failure so the
budget is still consumed (no hot-looping) but success isn't faked.

*Reference-impl:* implemented in `list_public_servers()` (only `s["updated_at"] = now`
inside the `valid` branch).

---

### 🐞 #6 — `validateServer()` mangles URLs and lies about the timeout

Two small defects in `validateServer()` (line ~2397):

```js
baseUrl = baseUrl.replace("/chat/completions", "")      // first occurrence ANYWHERE
```

A URL that merely *contains* the substring (e.g. `…/chat/completions/v1`) gets cut in
half. And the timeout error says *"did not respond within 10 seconds"* while the abort
controller fires at **30 000 ms** — users debugging slow (but working) relays get misled.

**Fix:** strip only a trailing suffix; correct the message to 30 seconds.

---

### 🐞 #7 — `GET /models` rewrites owner data on every model-list fetch

`handleGetServerModels()` (line ~1142) has a write side effect on a GET: when
`auto_update_models` is on it reads the owner's user document, mutates
`allowed_models`, saves it back, and refreshes the public index — on **every** model
list request. Two problems:

* concurrent GETs (chat page load + picker + models refresh all fire) do a
  read-modify-write on the same R2 document → classic lost-update race;
* R2 writes (cost + latency) even when the upstream model list is unchanged.

**Fix:** compare first — skip the write entirely when the fresh list equals the stored
one. (The worker's `handleGetServerModels` is only reachable for the owner's own server
lookup anyway; the public path goes through `handleModels`, which filters without
writing — the dedupe removes the remaining race.)

*Reference-impl:* `server_models()` only persists when `fresh != stored`.

---

### 🐞 #8 — unthrottled `/custom/api/servers/create` = upstream DoS vector

Every create attempt runs `validateServer()` (1–3 live HTTP probes against an arbitrary
third-party URL) and `isOllama()` (another probe). The CRUD paths skip the tier token
limiter, and the only generic throttle is a 10-second one. A script can hammer
`create` to DoS the owner's chosen upstream (or use g4f.space as an open scanner).

**Fix:** per-user create throttle (5/minute) on the rate-limit KV, 429 + `Retry-After`
on excess; fails open if the binding is missing.

*Reference-impl:* `create_limiter = Limiter(5)` ✅ (also returns 429 correctly).

---

### 🐞 #9 — frontend: one failed private-server fetch kills ALL custom providers

`loadCustomProvidersFromAPI()` (addon-init.js:588):

```js
const resp = await fetch("https://g4f.space/custom/api/servers", …);
if (resp.status === 401) { appStorage.removeItem("g4f_session"); }
privateData = await resp.json();     // ← throws on 4xx/5xx bodies
```

Any non-2xx (rate limit 420/429, 500) throws inside the `try`, jumping to the `catch`
that just `console.debug`s — so the **public** servers (the part that would have worked)
never get added to the dropdown.

**Fix:** parse only when `resp.ok`, warn otherwise.

---

### 🐞 #10 — malformed JSON body → 500 on `/servers/create` and `/servers/update`

```js
const body = await request.json();   // rejects on invalid JSON -> unhandled -> 500
```

A truncated/malformed request body (which is normal client misbehavior, not an
out-of-band error) produced a 500 with a stack trace instead of a clean 400.
Found while load-testing the reference implementation on 2026-09-13 (the E2E
battery's first run actually tripped this in production-parity code).

**Fix:** wrap `request.json()` in try/catch and return
`{ error: "Invalid JSON body" }, 400` in both `handleCreateServer` and
`handleUpdateServer`.

*Reference-impl test:* smoke check `malformed JSON (want 400, was 500)` ✅

---

## Non-bugs that are worth knowing (deliberate design)

* **CORS `*` + `Allow-Credentials: true`** — safe in practice because all auth travels in
  a `Bearer` header (never cookies), but it's a footgun for future cookie-based features.
* **Shared edge cache key without user identity** — fine because everything it caches is
  public data (model lists, public endpoints, repeated test prompts); `userProvidedKey`
  requests explicitly bypass it.
* **`X-Url` disclosure** — full upstream URL only for `admin` tier; everyone else gets the
  pathname only. Reference impl keeps the stricter variant (owner-only).
* **`420` as the rate-limit status** — it's "Enhance Your Calm", a real (rare) HTTP code;
  the frontend handles it.
* **`SERVER?`/`Server?` easter egg** and the VSCode-Copilot prompt rewriter are features,
  not bugs (they save real prompt tokens on Copilot-bridge traffic).

## Verification of the fixed code

```
$ node --check workers/api-worker.js        # OK
$ node --check dist/js/addons/addon-init.js # OK
$ python3 g4f-reference-impl/test_e2e.py
…
ALL 32 CHECKS PASSED ✅
```

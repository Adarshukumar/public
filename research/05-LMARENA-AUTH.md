# 05 — How g4f's "LMArena" provider authenticates (the "private key" question)

**Question answered:** *How does g4f's LMArena (LLM Arena) create its "private key"?
Does it use any API?*

**Short answer:** There is **no private-key API and no keypair at all**. The
"key" g4f uses is a **Supabase session cookie** (`arena-auth-prod-v1`) that
**arena.ai's own frontend creates inside a real Chrome browser** — g4f simply
drives the browser, waits for the site to mint the session, then **scrapes the
cookie + a reCAPTCHA v3 token** and replays them over plain HTTP. The real
APIs in the chain are: **Supabase GoTrue auth** (session create/refresh),
**arena.ai's Next.js streaming API** (the actual chat), **Cloudflare**
(anti-bot), and **reCAPTCHA Enterprise v3** (per-request gate).

---

## 1. What "G4F LLMARENA" actually is

- In the **g4f.dev/chat UI** the provider shows up as **"lm arena"**
  (see gpt4free issue #3238: *"Interface: g4f.dev/chat → select lm arena in
  provider selector → select gpt-5-chat in model selector"*).
- It maps to one file in the xtekky/gpt4free Python library:
  **`g4f/Provider/needs_auth/LMArena.py`** (saved here:
  [`sources/lmarena/LMArena.py`](sources/lmarena/LMArena.py), 732 lines,
  byte-exact copy from `main`).
- Note the folder: **`needs_auth/`** — g4f classifies this provider as
  *auth-required*, because the credential cannot be derived or requested with
  a normal API call. It must be **captured from a live browser session**.
- The backend it talks to is **`https://arena.ai`** — LMArena (formerly
  `lmarena.ai`, the LMSYS Chatbot Arena), not g4f infrastructure. g4f.dev is
  just an API client in front of it.

```
 you (g4f.dev/chat or g4f python)
        │  messages, model
        ▼
 g4f backend  ── g4f/Provider/needs_auth/LMArena.py
        │
        │  ① first time: launch REAL Chrome (nodriver/CDP) → arena.ai
        │     → the SITE creates the session cookie itself (Supabase)
        │     → g4f scrapes: cookies + reCAPTCHA v3 token + action hashes
        │  ② every request: plain HTTPS POST with the stolen cookies
        ▼
 https://arena.ai/nextjs-api/stream/create-evaluation   (Next.js server action)
        │
        ▼
  Supabase (auth + Postgres)  ·  model providers (OpenAI/Anthropic/Google/…)
```

---

## 2. The "private key" — what it really is

The credential is the **`arena-auth-prod-v1` cookie**. Its value format
(decoded by three independent third-party reimplementations — ExtremeRouter,
LMArenaBridge, arena2api):

```
arena-auth-prod-v1 = "base64-" + base64url( JSON )

JSON = {
  "access_token":  "<JWT>",          // Supabase GoTrue JWT
  "refresh_token": "<opaque>",       // GoTrue refresh token
  "expires_at":    1757…,            // unix seconds
  …
}
```

- The JWT's `iss` claim is **`https://<ref>.supabase.co/auth/v1`** — i.e. the
  cookie *is* a **Supabase web session** (the same shape `@supabase/ssr`
  writes). Because the JSON is > 4 KB, `@supabase/ssr` **splits it across
  `arena-auth-prod-v1.0`, `.1`, …** and leaves the base cookie empty; clients
  must rejoin the chunks in order.
- A signed-in (Google/email) user and an anonymous visitor end up with the
  **same cookie shape** — only the `user` inside differs
  (`is_anonymous: true` for anonymous).
- **There is no Ed25519/RSA keypair, no "generate key" endpoint, and no
  key the user types anywhere.** If you've seen the word "private key" in
  arena-adjacent tools, it's either (a) loose wording for this session token,
  or (b) Supabase's *recoverable anonymous users* beta (server stores a public
  JWK in `user_metadata`, client stores the matching private key locally) —
  which g4f never touches: it only ever uses the session cookie.

### Who creates it, and with which API

**arena.ai's own frontend JavaScript**, running in a real browser:

1. Browser hits `https://arena.ai/` → **Cloudflare** challenge
   (`cf_clearance`, `__cf_bm`, `_cfuvid` cookies).
2. Page JS inits the Supabase client and signs the visitor in —
   **anonymous** (`signInAnonymously()` → `POST {supabase}/auth/v1/signup`
   with an empty data payload, user gets `is_anonymous=true`) or via
   **OAuth** (Google/email). arena.ai also exposes its own wrapper
   **`POST https://arena.ai/nextjs-api/sign-up`** which returns the session in
   the response body / `Set-Cookie`.
3. Supabase returns `{access_token, refresh_token, expires_in, user}`.
4. The page encodes it as `base64-…` and sets the cookie
   (chunked by `@supabase/ssr`). A companion **`provisional_user_id`**
   cookie holds LMArena's own provisional identity.

**g4f does none of this directly** — it cannot: the signup endpoint is
behind Cloudflare + reCAPTCHA Enterprise. Instead it *makes the browser do it*:

```python
# LMArena.py — get_args_from_nodriver() (abridged)
args = await get_args_from_nodriver(cls.url, proxy=proxy, callback=callback)
#   callback(page: CDPTab):
#     • click "Accept Cookies" if present
#     • wait until:  document.cookie.indexOf("arena-auth-prod-v1") >= 0
#     • wait until:  window.grecaptcha && window.grecaptcha.enterprise
#     • captcha = await page.evaluate(
#         "window.grecaptcha.enterprise.execute('6LeTGMcsAAAAALuIlkVwIxaAuZA8VledA6d3Nnb0',
#          { action: 'chat_submit' });", await_promise=True)
#     • html = await page.get_content()
#     • cls.__load_actions(html)          # models + Next.js action hashes
```

`get_args_from_nodriver` is the shared g4f helper that spawns a **real Chrome
via CDP (nodriver)**, navigates, runs the callback, and returns
**`{cookies: …, headers: …}`** — the whole session in one dict. g4f then
serializes it to a cache file and reuses it.

So the "key creation" pipeline g4f triggers is:

```
nodriver Chrome → arena.ai
  → Cloudflare JS challenge passes          → cf_clearance
  → Supabase auth API mints anonymous session → arena-auth-prod-v1(.N)
  → grecaptcha.enterprise.execute()          → v3 token (in-page, no user)
  → page HTML                                → initialModels[] + server-action hashes
  → g4f returns to Python, saves everything to file
```

---

## 3. How a chat request is made (after the key exists)

From `create_async_generator()` in the saved source:

- **URL**
  - new conversation: `POST https://arena.ai/nextjs-api/stream/create-evaluation`
  - continuation: `POST https://arena.ai/nextjs-api/stream/post-to-evaluation/{evaluationSessionId}`
- **IDs** — all client-side **UUIDv7** (timestamp-first, `uuid7()` at the top
  of the file): `evaluationSessionId`, `userMessageId`, `modelAMessageId`,
  `modelBMessageId`.
- **mode** — `battle` (two random anonymous models), `direct-battle`
  (one named model vs a random one), `side-by-side` (two named models).
- **Body** (a Next.js server-action payload, `content-type: text/plain;charset=UTF-8`):

```json
{
  "id": "<uuid7>", "mode": "side-by-side",
  "userMessageId": "<uuid7>", "modelAMessageId": "<uuid7>",
  "modelAId": "<internal model id>", "modelBId": "<internal model id>",
  "modelBMessageId": "<uuid7>",
  "userMessage": {"content": "your prompt",
                  "experimental_attachments": [ …uploaded images… ],
                  "metadata": {}},
  "modality": "chat",
  "recaptchaV3Token": "<fresh v3 token>"
}
```

- **Headers**: the scraped cookies (incl. `cf_clearance` +
  `arena-auth-prod-v1`), a browser-like UA, `Referer: https://arena.ai`.
- **Auth refresh per request**: if the in-memory reCAPTCHA pool is empty,
  g4f re-drives the browser (`get_grecaptcha`) — v3 tokens are single-use and
  expire in ~2 minutes.
- **Images** (vision): two more server actions — `generateUploadUrl`
  (get presigned S3 PUT) → `PUT bytes` → `getSignedUrl` (get public URL) —
  with MD5-based image caching on the Python side.

### The SSE response format (custom, not OpenAI)

| prefix | meaning |
|--------|---------|
| `a0:` / `b0:` | text delta (A/B model) — JSON string |
| `ag:` | **reasoning/thinking delta** (this is how g4f surfaces arena "thinking") |
| `a2:` / `b2:` | images + `{"type":"heartbeat"}` keep-alives |
| `ad:` / `bd:` | finish: `finishReason` + `usage` |
| `a3:` | model error → raised as exception |
| `ae:` | platform error (seen in third-party ports) |

### Failure handling (the `for _ in range(2)` retry loop)

- `CloudflareError` / `MissingAuthError` → drop cookies, re-drive browser.
- `RateLimitError` (unless "prompt failed") → clear cookies
  (`_need_clear_cookies`) and retry once with a fresh session.
- Success → **save `args` (cookies+headers) to the cache file** for next time.

---

## 4. "DO THEY USE ANY API?" — the full API inventory

| API | Who calls it | What it does |
|-----|-------------|--------------|
| `https://arena.ai/` (HTML) | g4f's CDP Chrome | passes Cloudflare, loads page JS |
| `https://<ref>.supabase.co/auth/v1/signup` (GoTrue) | arena.ai page JS (in that Chrome) | **creates the session** — anonymous or OAuth; returns JWT + refresh token → `arena-auth-prod-v1` cookie |
| `https://arena.ai/nextjs-api/sign-up` | arena.ai page JS | same thing via arena's own Next.js wrapper (session in body/Set-Cookie) |
| `https://<ref>.supabase.co/auth/v1/token?grant_type=refresh_token` | (a) arena page JS on refresh, (b) third-party bridges directly with the **public** Supabase `anon` key + the cookie's `refresh_token` | rotates the session when expired — this is the only "key API" that exists |
| reCAPTCHA Enterprise v3 (`grecaptcha.enterprise.execute`, site key `6LeTGMcs…`) | g4f, executed *in the page* | one-shot token per chat request |
| `https://arena.ai/nextjs-api/stream/create-evaluation` / `post-to-evaluation/{id}` | **g4f directly** (plain httpx POST) | the actual chat; streams the `a0:/ag:/ad:` SSE |
| S3 presigned upload URLs (via `generateUploadUrl`/`getSignedUrl` actions) | g4f | image inputs |

**So:** yes, APIs are used — but the *credential-creating* API (Supabase
signup) is only ever called **by the site's own JavaScript inside the real
browser** that g4f drives. g4f itself only ever calls arena's streaming
endpoint, replaying the harvested session. There is no public
"create private key" endpoint to hit with curl.

---

## 5. Why it's built this way (design read)

1. **Arena has no real API** — it's a Next.js web app; the "API" is the
   server-action endpoints the UI uses, plus Cloudflare + reCAPTCHA
   Enterprise on every mutation.
2. **Supabase gives LMArena free user management**: anonymous visitors get a
   persistent `is_anonymous` user row (conversations, votes, Elo), OAuth users
   get real accounts — no bespoke auth code.
3. **g4f's strategy for `needs_auth` providers is uniform**: real browser →
   let the site authenticate → harvest `{cookies, headers}` → replay. The
   provider just supplies the *callback* (what to wait for / click / execute).
4. **Cost**: tokens last minutes, cookies last hours; the cache file + retry
   loop means a healthy session = zero browser usage per chat.

## 6. Sources

- [`sources/lmarena/LMArena.py`](sources/lmarena/LMArena.py) — xtekky/gpt4free
  `main`, `g4f/Provider/needs_auth/LMArena.py` (byte-exact copy, 732 ln)
- gpt4free issue [#3238](https://github.com/xtekky/gpt4free/issues/3238) —
  "LMArena Functionality loss" (confirms g4f.dev/chat → lm arena provider)
- rsalmn/ExtremeRouter `open-sse/executors/lmarena.js` — `reconstructLMArenaCookie`
  (Supabase SSR chunked-cookie format, `base64-` prefix)
- CloudWaddie/LMArenaBridge `src/auth.py` — session decode
  (`access_token`/`refresh_token`/`expires_at`), Supabase refresh endpoint
  (`/auth/v1/token?grant_type=refresh_token`), anon-key extraction,
  `provisional_user_id` cookie, server-side cookie rotation via page re-request
- flay-o/arena2api `README.md` — Chrome-extension variant: same cookie +
  v3-token-per-request model, token pool of 10 refreshed every 80 s
- Supabase docs — [Anonymous Sign-Ins](https://supabase.com/docs/guides/auth/auth-anonymous),
  `signInAnonymously()`
- OmniRoute issue #9306 — "copy the full Cookie header … include
  `arena-auth-prod-v1.0` and `.1` … preferably with `cf_clearance`"

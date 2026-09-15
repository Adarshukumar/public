# G4F Custom Server — Deep Dive & Working Reference Implementation

> Research + fixes + a runnable re-implementation of the **"custom server"** feature of
> [`gpt4free/g4f.dev`](https://github.com/gpt4free/g4f.dev) — the thing behind
> `https://g4f.dev/chat/#custom:srv_mtsj8uzo97d3c0d49960`
> ("custom server by cakey(Luna, GLM 5.3 Flash, Kimi K3, etc)").

Researched 2026-09-13 from the live site + the repo at commit `9d075d7`.

## What's in this folder

| Path | What it is |
|---|---|
| [`01-ARCHITECTURE.md`](01-ARCHITECTURE.md) | **The full explanation** — how the custom-server feature works end-to-end (browser → Cloudflare Worker → owner's upstream), with file/line references |
| [`02-CAKEY-LIVE-DATA.md`](02-CAKEY-LIVE-DATA.md) | Live verification of cakey's server against the production API (what the `srv_…` ID actually resolves to) |
| [`03-BUGS-AND-IMPROVEMENTS.md`](03-BUGS-AND-IMPROVEMENTS.md) | 10 real bugs found in the production code (incl. 2 crashes + 1 security issue) and the fixes |
| [`fixes/g4f-dev-custom-server-fixes.patch`](fixes/g4f-dev-custom-server-fixes.patch) | Ready-to-apply git patch for `gpt4free/g4f.dev` with all fixes (syntax-checked with `node --check`) |
| [`sources/`](sources/) | Copies of every key source file: the backend worker, auth worker, frontend addons, provider registry, config |
| [`live-data/`](live-data/) | Raw production snapshots (cakey's server entry, models, status) |
| [`g4f-reference-impl/`](g4f-reference-impl/) | **Working "deepened" re-implementation** in Python/FastAPI — run it, test it (32-check E2E suite), play with the chat UI |

## The 60-second version

1. A member (e.g. cakey) creates a **custom server** on g4f.dev: they submit the base URL of
   any OpenAI-compatible API they control (`https://osaii.wyvernhub.net/api/v1`) plus their
   private API key(s). The g4f.dev backend (`workers/api-worker.js`, a Cloudflare Worker on
   `g4f.space`) validates it, discovers its model list, and assigns it an ID like
   `srv_mtsj8uzo97d3c0d49960`.
2. If the server is marked **public**, it enters a shared public index
   (`MEMBERS_KV: public_servers_index`) that *every* g4f.dev visitor can list.
3. In the chat UI, every public (and your own private) server appears as a provider option
   `custom:{srv_id}`. The browser then talks to
   `https://g4f.space/custom/{srv_id}/chat/completions` — **never** to the owner's upstream.
4. The worker resolves the server, picks a random key from the *owner's* key pool, proxies the
   request to the owner's upstream, wraps the SSE stream to count tokens, updates usage
   counters, and returns the answer with `X-Server` / `X-Provider` headers (which the UI uses
   to label each message, and which is how `#custom:srv_…` conversation links get their name).
5. The URL hash `#custom:srv_mtsj8uzo97d3c0d49960` is a **shared-conversation link**: the chat
   treats the hash as a conversation ID and fetches it from
   `backend-api/v2/chat/{id}`.

## Try it yourself

```bash
cd g4f-reference-impl
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh                     # mock upstream :9101 + router/UI :8090
.venv/bin/python test_e2e.py # 32 end-to-end checks
```

Open `http://localhost:8090` — a small chat UI that reproduces the g4f.dev flow
(server picker → model picker → streaming chat), pre-seeded with cakey's exact server
label and a local mock of his `osaii.wyvernhub.net` upstream.

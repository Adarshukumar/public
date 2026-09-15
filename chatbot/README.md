# G4F Model Router — 48-model chatbot (backend + frontend)

A web chatbot where **every one of the 48 models from cakey's public g4f.dev
server** (the `srv_mtsj8uzo97d3c0d49960` relay at `osaii.wyvernhub.net`) is a
usable chat partner — each with its own voice. One Python backend serves both
the API **and** the frontend over a single web URL.

```
┌────────────── FRONTEND (static, served by backend) ──────────────┐
│  frontend/index.html + app.js + style.css                         │
│  · sidebar: all 48 models, grouped by family, searchable          │
│  · live usage counters, model tags, per-model colors              │
│  · streaming chat (SSE), stop button, new chat, code rendering    │
└──────────────────────────────┬───────────────────────────────────┘
                               │  fetch (same origin, no CORS issues)
┌──────────────────────────────▼───────────────────────────────────┐
│  BACKEND (Python / FastAPI, one port: 0.0.0.0:8090)              │
│  backend.py                                                       │
│  GET  /api/health   server status + mode                          │
│  GET  /api/models   all 48 models + usage stats                   │
│  POST /api/chat     SSE streaming chat (OpenAI-shaped chunks)     │
│  mode: SIMULATED (default, self-contained) or PROXY (real API)    │
└───────────────────────────────────────────────────────────────────┘
```

## Run

```bash
python3 -m pip install -r requirements.txt   # fastapi uvicorn httpx
./run.sh                                     # -> http://localhost:8090
python3 test_chatbot.py                      # 35 end-to-end checks
```

Open `http://localhost:8090` — pick a model on the left, chat on the right.

## 🧠 Thinking (reasoning) models

**17 of the 48 models are real reasoning models** (GLM 5.3 Flash & 4.7 —
forced thinking, Kimi K3 — always-on, DeepSeek V4 line — unified
auto-routing, Qwen3.7+ / Qwen3.x flash — hybrid, GPT-5.6 Luna — opaque
thinking + summary, GPT-OSS — visible reasoning stream). They're flagged
with a 🧠 badge in the sidebar. When you chat with one, the model's
thinking trace streams first into a collapsible **🧠 thinking** box, then
the answer — exactly the wire shape the real vendors use
(`reasoning_content` deltas, then `content` deltas). Full research on
which models think and *how* each vendor extracts the thinking:
[`../g4f-custom-server-deep-dive/04-REASONING-MODELS.md`](../g4f-custom-server-deep-dive/04-REASONING-MODELS.md).

In PROXY mode the same pipeline carries the *real* model's trace: provider
knobs (`enable_thinking`, `thinking_budget`, `thinking: {…}`,
`reasoning: {effort}`) can be sent in the request body and are forwarded to
the upstream untouched.

## Try different models

- **openai-z/gpt-5.6-luna** — the "Luna", balanced frontier voice
- **zai-z/zai-org-glm-5-3-flash** — step-by-step reasoner
- **xai-z/grok-4-fast-non-reasoning** — witty and fast
- **logfare/kimi-k3** — warm, structured, long-context
- **poolside/laguna-xs-2.1** — the agentic coder (ask it for code)
- **venice-z/venice-uncensored-role-play** — character/roleplay
- **osaii/ultrafast-experimental**, **stealth/lion-alpha** — the weird ones
- ask any of them **"what's 27 × 43?"** (math) or **"write python code…"** (code)

## Point it at a REAL model API (proxy mode)

The default mode is fully self-contained (simulated personas — no network,
always works). To make it a real router to any OpenAI-compatible upstream
(exactly like g4f.dev does), set env vars and the same UI chats with the real
model through this backend:

```bash
UPSTREAM_BASE_URL=https://your-openai-compatible.api/v1 \
UPSTREAM_API_KEY=sk-... \
./run.sh
```

`/api/health` reports the current mode (`simulated` vs `proxy -> …`).

## API quick tour

```bash
curl -s localhost:8090/api/health | python3 -m json.tool
curl -s localhost:8090/api/models | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['models']]"
curl -s -N -H 'Content-Type: application/json' \
  -d '{"model":"logfare/kimi-k3","stream":true,"messages":[{"role":"user","content":"hi"}]}' \
  localhost:8090/api/chat
```

## Files

| File | Role |
|---|---|
| `backend.py` | FastAPI server: model catalog (48), chat endpoint (SSE + JSON), usage tracking, simulated model engine, optional real-upstream proxy, serves the frontend |
| `frontend/index.html` | Page shell: model sidebar + chat pane + composer |
| `frontend/app.js` | All UI logic: model list, search, streaming renderer, stop/new-chat |
| `frontend/style.css` | Dark theme (g4f.dev inspired) |
| `test_chatbot.py` | 35-check E2E suite |
| `usage.json` | Per-model request/token stats (created at runtime) |

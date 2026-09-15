# G4F Model Router — Complete Documentation

**Everything in this repo, explained.** A complete deep-dive into the
**g4f.dev custom-server** feature (with its live production data, 10 real bugs
found in the source, a ready-to-apply patch, and a working re-implementation),
plus a **48-model web chatbot** built on top of the research — one Python
backend, one frontend, one web URL — where **17 of the 48 models are real
reasoning models** that stream their thinking.

---

## Table of contents

1. [The three-folder pattern](#1-the-three-folder-pattern)
2. [Quick start — run everything](#2-quick-start--run-everything)
3. [The chatbot backend — how it works](#3-the-chatbot-backend--how-it-works)
4. [The API — every endpoint, how to fetch](#4-the-api--every-endpoint-how-to-fetch)
5. [ALL 48 models — complete data](#5-all-48-models--complete-data)
6. [Reasoning models — how thinking is extracted](#6-reasoning-models--how-thinking-is-extracted)
7. [How model data is fetched from g4f.dev (the research)](#7-how-model-data-is-fetched-from-g4fdev-the-research)
8. [The g4f.dev research — what, how, findings](#8-the-g4fdev-research--what-how-findings)
9. [Testing — what is verified and how](#9-testing--what-is-verified-and-how)
10. [Complete file map — every file in the repo](#10-complete-file-map--every-file-in-the-repo)

---

## 1. The three-folder pattern

```
public/
├── README.md            ← YOU ARE HERE: the entire doc
├── research/            ← THE ENTIRE g4f.dev RESEARCH
│   ├── 01-ARCHITECTURE.md          how g4f.dev's custom-server works end-to-end
│   ├── 02-CAKEY-LIVE-DATA.md       cakey's live server, verified against production
│   ├── 03-BUGS-AND-IMPROVEMENTS.md 10 real bugs found in the production code
│   ├── 04-REASONING-MODELS.md      which models think + how thinking is extracted
│   ├── README.md                   research index
│   ├── sources/                    copies of the actual g4f.dev source files
│   ├── live-data/                  raw JSON snapshots of cakey's live server
│   ├── fixes/                      ready-to-apply patch for gpt4free/g4f.dev
│   └── g4f-reference-impl/         WORKING Python re-implementation + 33-check E2E
├── route-py/            ← ALL PYTHON CODE (the chatbot backend + tests)
│   ├── backend.py            the FastAPI server (API + serves the frontend)
│   ├── test_chatbot.py       49-check E2E suite
│   ├── check_all_models.py   live sweep: all 48 models, direct, no system prompt
│   ├── requirements.txt · run.sh · .gitignore
└── route-web/           ← THE FRONTEND (3rd route folder)
    ├── index.html · app.js · style.css
```

The **backend** (`route-py/backend.py`) serves **both** the API **and** the
frontend (`route-web/`) on one port — one web URL, zero CORS, one process.

---

## 2. Quick start — run everything

### The 48-model chatbot (the main thing)

```bash
cd route-py
python3 -m pip install -r requirements.txt   # fastapi, uvicorn, httpx
./run.sh                                     # → http://localhost:8090
```

Open `http://localhost:8090` — sidebar with all 48 models (grouped by family,
searchable, 🧠 badge on the 17 thinking ones), chat pane with live streaming,
a stop button, per-model usage counters.

```bash
python3 test_chatbot.py       # 49 end-to-end checks
python3 check_all_models.py   # sweeps all 48 models (server must be running)
```

### Point the chatbot at REAL models (proxy mode)

```bash
UPSTREAM_BASE_URL=https://your-openai-compatible.api/v1 \
UPSTREAM_API_KEY=sk-... \
./run.sh
```

Same UI, same code — the backend becomes a real router (exactly what
g4f.dev's custom-server does). `/api/health` reports the active mode.

### The g4f.dev reference implementation (research artifact)

```bash
cd research/g4f-reference-impl
python3 -m pip install -r requirements.txt
./run.sh                # mock upstream on :9101 + g4f-style router on :8090
python3 test_e2e.py     # 33 end-to-end checks
```

A faithful port of the production Cloudflare Worker logic (with the 10 bugs
fixed), plus a mock of cakey's upstream relay, so the whole g4f.dev flow can
be run, tested and inspected locally.

---

## 3. The chatbot backend — how it works

`route-py/backend.py` (≈700 lines, FastAPI, single port `0.0.0.0:8090`):

```
┌────────────────────── FRONTEND (route-web/, served by the backend) ──────────┐
│  index.html + app.js + style.css — dark g4f-style UI                         │
│  · sidebar: 48 models, family groups, search, 🧠 thinking badges, counts     │
│  · chat: SSE streaming with caret, collapsible 🧠 thinking box, stop/new     │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │  fetch() — same origin
┌──────────────────────────────▼───────────────────────────────────────────────┐
│  BACKEND (route-py/backend.py)                                               │
│  GET  /api/health    status + mode + model count                             │
│  GET  /api/models    all 48 models + per-model usage + thinking flags        │
│  POST /api/chat      SSE stream or JSON; two-phase for thinking models       │
│                                                                      │       │
│  ┌─ SIMULATED mode (default) ────────────────────────────────────────────┐   │
│  │ deterministic persona engine: one distinct voice per model,           │   │
│  │ built-in fact base, math eval, code snippets, RP flavor;              │   │
│  │ thinking models emit a family-flavored CoT trace                      │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│  ┌─ PROXY mode (UPSTREAM_BASE_URL set) ─────────────────────────────────┐    │
│  │ forwards the request to any OpenAI-compatible API with your key,     │    │
│  │ streams SSE back untouched (incl. real reasoning_content),           │    │
│  │ merges provider thinking knobs into the upstream payload             │    │
│  └──────────────────────────────────────────────────────────────────────┘   │
│  usage.json — per-model request/token stats, persisted                        │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Key design decisions**

| Decision | Why |
|---|---|
| One port serves API + frontend | One web URL, no CORS, trivially deployable — mirrors how g4f.dev serves its frontend + API |
| Simulated mode by default | The app always works, offline, deterministic — every model gets a distinct, stable persona |
| Proxy mode via env vars | Flip two env vars and the *same UI* talks to *real* models — exactly g4f.dev's custom-server flow |
| OpenAI-shaped responses | Any OpenAI-compatible client (or the g4f.dev flow) can drive this backend |
| `reasoning_content` field | The exact field DeepSeek/Qwen/GLM/Kimi use — so the thinking UI is real-world-shaped in both modes |

---

## 4. The API — every endpoint, how to fetch

Base URL: `http://localhost:8090` (or your deploy host).

### 4.1 `GET /api/health` — server status

```bash
curl -s http://localhost:8090/api/health
# {"ok":true,"mode":"simulated","models":48,"time":"2026-09-15T..."}
```

`mode` is `"simulated"` or `"proxy -> <upstream-url>"`.

### 4.2 `GET /api/models` — the full model data

```bash
curl -s http://localhost:8090/api/models | python3 -m json.tool
```

Response shape:

```json
{
  "models": [
    {
      "id": "openai-z/gpt-5.6-luna",
      "name": "GPT-5.6 Luna",
      "family": "openai-z",
      "tags": ["frontier", "reasoning"],
      "context": 262144,
      "description": "Flagship reasoning model (the 'Luna' in cakey's label).",
      "thinking": true,
      "requests": 41,
      "tokens": 1893
    }
    // … 48 entries
  ],
  "total": 48,
  "thinking_models": 17
}
```

Fetch just the IDs:

```bash
curl -s localhost:8090/api/models |
  python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['models']]"
```

### 4.3 `POST /api/chat` — chat (JSON mode)

```bash
curl -s -H 'Content-Type: application/json' \
  -d '{"model":"logfare/kimi-k3","stream":false,
       "messages":[{"role":"user","content":"what is the capital of France?"}]}' \
  http://localhost:8090/api/chat
```

Response (OpenAI-compatible; thinking models add `reasoning_content`):

```json
{
  "id": "chatcmpl-…", "object": "chat.completion", "created": 1789…,
  "model": "logfare/kimi-k3",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "reasoning_content": "Let me think about this step by step. …",
      "content": "Here's the fact you're after: **Paris**."
    },
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 9, "completion_tokens": 42, "total_tokens": 51,
            "completion_tokens_details": {"reasoning_tokens": 30}}
}
```

**No system prompt is ever injected** — whatever `messages` you send is used
directly (the audit sweep in `check_all_models.py` verifies every model this
way, one plain user message each).

### 4.4 `POST /api/chat` — chat (SSE streaming mode)

```bash
curl -s -N -H 'Content-Type: application/json' \
  -d '{"model":"zai-z/zai-org-glm-5-3-flash","stream":true,
       "messages":[{"role":"user","content":"prove there are infinitely many primes"}]}' \
  http://localhost:8090/api/chat
```

The stream is **two-phase** for thinking models:

```
data: {"choices":[{"delta":{"role":"assistant","reasoning_content":"Step 1 — …"}}]}   ← PHASE 1: thinking
data: {"choices":[{"delta":{"reasoning_content":"Step 2 — …"}}]}
data: {"choices":[{"delta":{"content":"Let me reason through this: …"}}]}             ← PHASE 2: answer
data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{…,"completion_tokens_details":{"reasoning_tokens":96}}}
data: [DONE]
```

Non-thinking models skip phase 1. Parse it in Python:

```python
import json, httpx

with httpx.stream("POST", "http://localhost:8090/api/chat", timeout=60, json={
    "model": "zai-z/zai-org-glm-5-3-flash", "stream": True,
    "messages": [{"role": "user", "content": "prove there are infinitely many primes"}]
}) as r:
    thinking, answer = "", ""
    for line in r.iter_lines():
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            break
        delta = json.loads(payload).get("choices", [{}])[0].get("delta", {})
        if delta.get("reasoning_content"):      # phase 1
            thinking += delta["reasoning_content"]
        elif delta.get("content"):              # phase 2
            answer += delta["content"]
print("THOUGHT:", thinking)
print("ANSWER :", answer)
```

### 4.5 Error handling

| Condition | Status | Body |
|---|---|---|
| Unknown model | 400 | `{"error":"Unknown model '<id>'. Use GET /api/models."}` |
| Missing/non-string `messages[-1].content` | 400 | `{"error":"messages[-1].content (string) is required"}` |
| Malformed JSON body | 400 | `{"error":"Invalid JSON body"}` |
| Proxy mode: upstream failure | 502 | `{"error":"Upstream <code>", "detail":"…"}` |

### 4.6 Proxy mode request extras (real models)

In proxy mode these keys in the request body are forwarded to the upstream
provider untouched — the provider-specific "make it think" knobs:

```json
{
  "model": "…", "stream": true, "messages": [ … ],
  "enable_thinking": true,          // Qwen
  "thinking_budget": 4096,          // Qwen / Gemini-style token caps
  "thinking": {"type": "enabled"},  // GLM / Kimi
  "reasoning": {"effort": "high"},  // OpenAI / xAI
  "max_tokens": 8192, "temperature": 0.7
}
```

---

## 5. ALL 48 models — complete data

The catalog mirrors **all 48 `allowed_models` from cakey's live public g4f
server** (`srv_mtsj8uzo97d3c0d49960` at `https://osaii.wyvernhub.net/api/v1`,
snapshot in `research/live-data/`). 20 families, 17 reasoning models.

| # | Model ID | Name | Tags | Context | Thinks | Description |
|---|---|---|---|---|---|---|
| 1 | `openai-z/gpt-5.6-luna` | GPT-5.6 Luna | frontier, reasoning | 262,144 | 🧠 yes | Flagship reasoning model (the 'Luna' in cakey's label). |
| 2 | `openai-z/gpt-5.4-nano` | GPT-5.4 Nano | fast, cheap | 131,072 | — | Small and quick GPT-5.4. |
| 3 | `openai-z/gpt-4o-mini` | GPT-4o Mini | fast, general | 128,000 | — | Lightweight GPT-4o. |
| 4 | `openai-z/gpt-4.1-nano` | GPT-4.1 Nano | fast, cheap | 128,000 | — | Tiny, fast, cheap. |
| 5 | `openai-z/gpt-4.1-mini` | GPT-4.1 Mini | fast, general | 128,000 | — | Small GPT-4.1. |
| 6 | `zai-z/zai-org-glm-5-3-flash` | GLM 5.3 Flash | reasoning, fast | 131,072 | 🧠 yes | Z.ai GLM 5.3 flash — strong reasoning, quick. |
| 7 | `zai-z/zai-org-glm-4.7-flash` | GLM 4.7 Flash | reasoning, fast | 131,072 | 🧠 yes | Previous-gen GLM flash. |
| 8 | `zai-z/olafangensan-glm-4.7-flash-heretic` | GLM 4.7 Heretic | uncensored, roleplay | 131,072 | 🧠 yes | Uncensored fine-tune of GLM 4.7 flash. |
| 9 | `zai-z/zai-org-glm-4.6` | GLM 4.6 | general | 131,072 | 🧠 yes | Solid mid-gen GLM. |
| 10 | `xai-z/grok-4-fast-non-reasoning` | Grok 4 Fast | fast, witty | 131,072 | — | xAI Grok 4 fast tier — witty, no reasoning overhead. |
| 11 | `xai-z/grok-4-1-fast-non-reasoning` | Grok 4.1 Fast | fast, witty | 131,072 | — | Grok 4.1 fast tier. |
| 12 | `logfare/kimi-k3` | Kimi K3 | long-context, helpful | 262,144 | 🧠 yes | Moonshot Kimi K3 — long context, helpful. |
| 13 | `logfare/minimax-m3` | MiniMax M3 | general, creative | 131,072 | — | MiniMax M3 via Logfare. |
| 14 | `logfare/deepseek-v4-flash` | DeepSeek V4 Flash | reasoning, fast | 131,072 | 🧠 yes | DeepSeek V4 flash via Logfare. |
| 15 | `logfare/deepseek-v4-pro` | DeepSeek V4 Pro | reasoning, strong | 131,072 | 🧠 yes | DeepSeek V4 pro via Logfare. |
| 16 | `poolside/laguna-xs-2.1` | Laguna XS 2.1 | coding, agentic, fast | 262,144 | — | Lightest agentic coding model (227 reqs — most used!). |
| 17 | `poolside/laguna-s-2.1` | Laguna S 2.1 | coding, agentic, frontier | 262,144 | — | Poolside's most capable agentic coder. |
| 18 | `osaii/voicellm` | VoiceLLM | audio, experimental | 32,768 | — | osaii's voice-focused experimental model. |
| 19 | `osaii/faster-experimental` | Faster Experimental | fast, experimental | 32,768 | — | Speed-tuned experimental build. |
| 20 | `osaii/ultrafast-experimental` | Ultrafast Experimental | ultrafast, experimental | 32,768 | — | The fastest thing on the relay. |
| 21 | `venice-z/gemma-4-31b-it` | Gemma 4 31B IT | general | 32,768 | — | Google Gemma 4 31B instruct. |
| 22 | `venice-z/gemma-4-uncensored` | Gemma 4 Uncensored | uncensored | 32,768 | — | Uncensored Gemma 4. |
| 23 | `venice-z/mistral-31-24b` | Mistral 31 24B | general | 32,768 | — | Mistral 31 24B via Venice. |
| 24 | `venice-z/venice-uncensored-1-2` | Venice Uncensored 1.2 | uncensored | 32,768 | — | Venice's house uncensored build. |
| 25 | `venice-z/venice-uncensored-role-play` | Venice RP Uncensored | roleplay, uncensored | 32,768 | — | Tuned for roleplay. |
| 26 | `qwen-z/qwen-flash-character` | Qwen Flash Character | roleplay, character | 32,768 | — | Qwen tuned for character chat. |
| 27 | `qwen-z/qwen3.8-flash` | Qwen 3.8 Flash | fast, general | 131,072 | 🧠 yes | Qwen 3.8 flash. |
| 28 | `qwen-z/qwen3.6-flash` | Qwen 3.6 Flash | fast, general | 131,072 | 🧠 yes | Qwen 3.6 flash. |
| 29 | `qwen-z/qwen3-coder-flash` | Qwen 3 Coder Flash | coding, fast | 131,072 | 🧠 yes | Qwen's coding flash model. |
| 30 | `qwen-z/qwen3.7-plus` | Qwen 3.7 Plus | general, strong | 131,072 | 🧠 yes | Qwen 3.7 plus tier. |
| 31 | `fireworks-z/nemotron-lightning-3.5` | Nemotron Lightning 3.5 | fast, reasoning | 131,072 | — | NVIDIA Nemotron lightning via Fireworks. |
| 32 | `fireworks-z/muse-glimmer-30b` | Muse Glimmer 30B | creative | 32,768 | — | Meta Muse Glimmer 30B via Fireworks. |
| 33 | `groq-z/gpt-oss-20b` | GPT-OSS 20B | fast, open-weights | 131,072 | 🧠 yes | Open-weights GPT-OSS 20B on Groq hardware. |
| 34 | `groq-z/gpt-oss-120b` | GPT-OSS 120B | strong, open-weights | 131,072 | 🧠 yes | Open-weights GPT-OSS 120B on Groq hardware. |
| 35 | `deepseek-z/deepseek-v4-flash` | DeepSeek V4 Flash | reasoning, fast | 131,072 | 🧠 yes | DeepSeek V4 flash direct. |
| 36 | `deepseek-z/deepseek-v4-pro` | DeepSeek V4 Pro | reasoning, strong | 131,072 | 🧠 yes | DeepSeek V4 pro direct. |
| 37 | `mimo-z/mimo-v2.5` | MiMo v2.5 | general, conversational | 131,072 | — | Xiaomi MiMo v2.5. |
| 38 | `mimo-z/mimo-v2.5-pro` | MiMo v2.5 Pro | general, strong | 131,072 | — | MiMo v2.5 pro tier. |
| 39 | `gemini-z/gemini-2.5-flash-lite` | Gemini 2.5 Flash Lite | fast, cheap | 1,048,576 | — | Cheapest Gemini flash tier. |
| 40 | `gemini-z/gemini-3.1-flash-lite-preview` | Gemini 3.1 Flash Lite (preview) | fast, preview | 1,048,576 | — | Newest Gemini flash-lite preview. |
| 41 | `gemini-z/gemini-3.5-flash-lite` | Gemini 3.5 Flash Lite | fast | 1,048,576 | — | Gemini 3.5 flash lite. |
| 42 | `minimax-z/minimax-m3` | MiniMax M3 | general, creative | 131,072 | — | MiniMax M3 direct. |
| 43 | `mistral-z/mistral-small-2603` | Mistral Small 2603 | fast, general | 131,072 | — | Mistral Small (26.03 release). |
| 44 | `openrouter-z/qwen3.8-27b` | Qwen 3.8 27B | general | 131,072 | 🧠 yes | Qwen 3.8 27B via OpenRouter. |
| 45 | `inception-z/mercury-2` | Mercury 2 | experimental | 32,768 | — | Inception Labs Mercury 2. |
| 46 | `stealth/lion-alpha` | Lion Alpha | experimental, stealth | 32,768 | — | Mystery model from the stealth project. |
| 47 | `meta-z/muse-spark-1.2-contributor` | Muse Spark 1.2 | creative | 32,768 | — | Meta Muse Spark 1.2 contributor build. |
| 48 | `microsoft/bitnet-b1.58-2B-4T` | BitNet b1.58 2B | tiny, efficient, experimental | 32,768 | — | Microsoft 1.58-bit ternary network — tiny and fast. |

**Total: 48 models · 20 families · 17 with the reasoning feature.**

> Note: the model *IDs* above are reproduced exactly as cakey's live server
> reports them (e.g. `glm-4-7` not `glm-4.7`); the *names* keep the vendor's
> dotted notation. The frontend displays names; the API uses IDs.

---

## 6. Reasoning models — how thinking is extracted

Full research: [`research/04-REASONING-MODELS.md`](research/04-REASONING-MODELS.md).
The short version:

**Two wire patterns exist in the industry:**

1. **Visible chain in a side field** — the raw thinking stream returns in
   `reasoning_content` (message field / stream delta), separate from
   `content`. Used by **DeepSeek, Qwen, Z.ai GLM, Kimi**.
2. **Opaque thinking + summary/encrypted blob** — thinking happens
   internally; you get at most a human-readable summary and/or an encrypted
   token to echo back for multi-turn coherence. Used by **OpenAI (GPT-5)**
   and **xAI (Grok 4.3+)**.

**Per vendor (the actual mechanics):**

| Vendor | Control params | What you receive |
|---|---|---|
| DeepSeek (R1/V3.1/V3.2→V4) | model choice (reasoner vs chat) | `reasoning_content` (full CoT), thinking tokens billed in `completion_tokens` |
| Qwen (Qwen3 series) | `enable_thinking`, `thinking_budget`, `reasoning_effort` | `reasoning_content`; qwen3.7-plus is on by default; off = 60–75% faster |
| Z.ai GLM | `thinking:{type,clear_thinking}`, `reasoning_effort` | `reasoning_content`; **GLM-5.3 thinking is FORCED (cannot disable)**; 4.7 = interleaved + preserved + turn-level |
| Kimi / Moonshot | effort levels, `thinking:{type,keep}` | `reasoning_content`; **K3 reasoning always on**; history must include it |
| OpenAI (GPT-5/o) | `reasoning:{effort}` | **summary only** + optional `reasoning.encrypted_content` to echo back |
| xAI (Grok 4.3+) | `reasoning:{effort: low…xhigh}` | `reasoning_text` / `reasoning_summary_text` deltas; **grok-4-1-fast rejects the param (HTTP 400)** |
| Gemini | `thinkingBudget` (2.5) / `thinkingLevel` (3) + `include_thoughts` | thought **summaries**; flash-lite = off by default |
| GPT-OSS (open weights) | — | raw reasoning stream in a `reasoning` delta field |

**The 17 thinking models in our catalog** (verified live by
`route-py/check_all_models.py` — two-phase stream order confirmed on the
wire for each):

`openai-z/gpt-5.6-luna` · `zai-z/zai-org-glm-5-3-flash` · `zai-z/zai-org-glm-4.7-flash` · `zai-z/olafangensan-glm-4.7-flash-heretic` · `zai-z/zai-org-glm-4.6` · `logfare/kimi-k3` · `logfare/deepseek-v4-flash` · `logfare/deepseek-v4-pro` · `qwen-z/qwen3.8-flash` · `qwen-z/qwen3.6-flash` · `qwen-z/qwen3-coder-flash` · `groq-z/gpt-oss-20b` · `groq-z/gpt-oss-120b` · `deepseek-z/deepseek-v4-flash` · `deepseek-z/deepseek-v4-pro` · `qwen-z/qwen3.7-plus` · `openrouter-z/qwen3.8-27b`

**In this chatbot:** simulated mode emits a deterministic, family-flavored
trace in the real field with the real two-phase stream order; proxy mode
passes the upstream's *real* trace through untouched.

---

## 7. How model data is fetched from g4f.dev (the research)

The 48-model catalog was **not invented** — it was fetched from g4f.dev's
live public custom-server index and verified against the live production
relay. The exact endpoints (all public, no auth):

```
# 1) the public index of every public custom server (this is where cakey was found)
GET https://g4f.space/custom/api/servers/public

# 2) one server's full entry (base_url, allowed_models, is_valid, usage, timestamps)
GET https://g4f.space/custom/srv_mtsj8uzo97d3c0d49960/status

# 3) one server's models, sorted by usage
GET https://g4f.space/custom/srv_mtsj8uzo97d3c0d49960/models
```

**Cakey's server** (the one this chatbot mirrors):

| Field | Value |
|---|---|
| server ID | `srv_mtsj8uzo97d3c0d49960` |
| label | "custom server by cakey(Luna, GLM 5.3 Flash, Kimi K3, etc)" |
| base_url | `https://osaii.wyvernhub.net/api/v1` |
| is_public / is_valid | true / **true** (re-validated hourly in production) |
| allowed_models | **48** (the full list is this chatbot's catalog) |
| created | 2026-09-08T10:34:36.9Z |
| usage (at snapshot) | 119 requests · 3,518 tokens |
| most-used model | `poolside/laguna-xs-2.1` (227 reqs) |

Raw snapshots: [`research/live-data/`](research/live-data/)
(`cakey-server.json`, `cakey-models.json`, `cakey-status.json`).

The full end-to-end flow — browser → Cloudflare Worker → owner's upstream —
is documented with file/line references in
[`research/01-ARCHITECTURE.md`](research/01-ARCHITECTURE.md).

---

## 8. The g4f.dev research — what, how, findings

**What:** g4f.dev's "custom server" feature lets any user register their own
OpenAI-compatible endpoint; the platform proxies chats through it, tracks
usage, exposes it in a public index, and routes g4f.dev's own frontend
models like `custom:<server_id>` to it.

**How it was researched:** the production sources (the Cloudflare Workers
`api-worker.js` + `members-worker.js`, wrangler configs, frontend addon
code, provider registry) were retrieved and saved to
[`research/sources/`](research/sources/), then traced end-to-end; cakey's live
server was probed through the public API (section 7) and every claim was
verified against it.

**Findings — 10 real bugs in the production code** (2 crash bugs, 1 security
issue), documented with code refs and fixed in a verified, ready-to-apply
patch:

- [`research/03-BUGS-AND-IMPROVEMENTS.md`](research/03-BUGS-AND-IMPROVEMENTS.md) — the 10 bugs, explained
- [`research/fixes/g4f-dev-custom-server-fixes.patch`](research/fixes/g4f-dev-custom-server-fixes.patch) — 14 hunks, verified to apply cleanly on a fresh `gpt4free/g4f.dev` checkout

Highlights:
- **Security:** the public index was leaking `api_keys` of public servers (stripped in the patch)
- **Crash:** malformed JSON on create/update returned 500 (now 400)
- **Correctness:** bodyless DELETE, trailing-slash-only path strip, R2 write amplification on unchanged model lists, `updated_at` bumped on failed validation, `isOllama` mis-detection, anon-proxy 401s, missing owner-only `validate/status`, addon-init parse on non-OK responses, no create throttle

**The reference implementation** proves the analysis: a faithful
Python/FastAPI port of the worker (all endpoints, auth, proxying, easter egg,
rate limits) with the 10 bugs fixed, running against a mock of cakey's
upstream, covered by a 33-check E2E suite — all in
[`research/g4f-reference-impl/`](research/g4f-reference-impl/).

---

## 9. Testing — what is verified and how

| Suite | Location | Checks | What it proves |
|---|---|---|---|
| `test_chatbot.py` | `route-py/` | **49/49** | health, full 48-model catalog, SSE + `[DONE]`, distinct model voices, math/code/fact/greeting detection, **17 reasoning models return `reasoning_content`**, two-phase stream order, usage tracking, error handling, frontend serving |
| `check_all_models.py` | `route-py/` | 48 + 17 | **every model called directly — one plain user message each, NO system prompt** — replies OK, reasoning present exactly where flagged, stream phase order verified on the wire |
| `test_e2e.py` | `research/g4f-reference-impl/` | **33/33** | the g4f.dev re-implementation: CRUD, auth tiers, public index redaction, streaming usage capture, `Server?` easter egg, malformed-JSON 400s |

Run them:

```bash
cd route-py && python3 test_chatbot.py && python3 check_all_models.py
cd research/g4f-reference-impl && python3 test_e2e.py
```

---

## 10. Complete file map — every file in the repo

| File | Role |
|---|---|
| `README.md` | This document |
| `research/README.md` | Research index (how to read 01→04) |
| `research/01-ARCHITECTURE.md` | g4f.dev custom-server, end-to-end, with file/line refs |
| `research/02-CAKEY-LIVE-DATA.md` | Cakey's live server, verified against production |
| `research/03-BUGS-AND-IMPROVEMENTS.md` | The 10 production bugs + fixes |
| `research/04-REASONING-MODELS.md` | Which models think + the extraction process per vendor (sourced) |
| `research/sources/workers/api-worker.js` | The production backend Worker (the thing analyzed) |
| `research/sources/workers/members-worker.js` | The production auth Worker |
| `research/sources/workers/wrangler-*.toml` | Deploy configs (names, bindings) |
| `research/sources/chat/index.html` | The production custom-server chat page |
| `research/live-data/cakey-server.json` | Raw: cakey's server entry |
| `research/live-data/cakey-models.json` | Raw: cakey's 48 models by usage |
| `research/live-data/cakey-status.json` | Raw: cakey's status/validation |
| `research/fixes/g4f-dev-custom-server-fixes.patch` | Ready-to-apply patch (14 hunks) |
| `research/g4f-reference-impl/server.py` | Working g4f.dev re-implementation (router) |
| `research/g4f-reference-impl/mock_upstream.py` | Mock of cakey's upstream relay (:9101) |
| `research/g4f-reference-impl/static/index.html` | Demo chat UI for the re-implementation |
| `research/g4f-reference-impl/test_e2e.py` | 33-check E2E suite |
| `research/g4f-reference-impl/{README.md,requirements.txt,run.sh,.gitignore}` | Run it |
| `route-py/backend.py` | **The chatbot backend** — API + frontend on one port |
| `route-py/test_chatbot.py` | 49-check E2E suite |
| `route-py/check_all_models.py` | All-48-models direct audit sweep |
| `route-py/{requirements.txt,run.sh,.gitignore}` | Run it |
| `route-web/index.html` | Frontend page shell (sidebar + chat + composer) |
| `route-web/app.js` | All UI logic (models, search, SSE renderer, thinking box, stop) |
| `route-web/style.css` | Dark g4f-inspired theme |

**That's everything in the repo.** Nothing else remains — the previous
`chatbot/` and `g4f-custom-server-deep-dive/` folders were reorganized into
this pattern and deleted.

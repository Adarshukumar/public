"""
backend.py — Chatbot backend (Python/FastAPI).

Serves BOTH the REST API and the frontend over one web port (0.0.0.0:8090).

    GET  /                -> frontend (index.html)
    GET  /static/*        -> frontend assets
    GET  /api/health      -> server status + mode
    GET  /api/models      -> full model catalog (all 45 models) + usage stats
    POST /api/chat        -> chat completion (SSE streaming or plain JSON)

Model mode
----------
* SIMULATED (default, self-contained): every model is a distinct simulated
  persona with its own voice/capabilities — no external network needed, so it
  always works. Perfect for demoing the whole product.
* PROXY: set UPSTREAM_BASE_URL (+ optional UPSTREAM_API_KEY) to any
  OpenAI-compatible API and the backend becomes a real router: your frontend
  chats with the REAL model through this server (same flow as g4f.dev).

Run:  python3 backend.py
"""
import asyncio
import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent
FRONTEND = BASE / "frontend"
STATS_FILE = BASE / "usage.json"

# --- optional real upstream (OpenAI-compatible) ----------------------------
UPSTREAM_BASE = os.environ.get("UPSTREAM_BASE_URL", "").rstrip("/")
UPSTREAM_KEY = os.environ.get("UPSTREAM_API_KEY", "")
MODE = "proxy -> " + UPSTREAM_BASE if UPSTREAM_BASE else "simulated"

# ---------------------------------------------------------------------------
# Model catalog — all 48 models from cakey's public g4f server
# (srv_mtsj8uzo97d3c0d49960 @ osaii.wyvernhub.net, snapshot 2026-09-13)
# ---------------------------------------------------------------------------
def M(mid, name, family, tags, ctx, desc, style):
    return {"id": mid, "name": name, "family": family, "tags": tags,
            "context": ctx, "description": desc, "style": style}

MODELS = [
    # --- openai-z ---
    M("openai-z/gpt-5.6-luna", "GPT-5.6 Luna", "openai-z", ["frontier", "reasoning"], 262144, "Flagship reasoning model (the 'Luna' in cakey's label).", "luna"),
    M("openai-z/gpt-5.4-nano", "GPT-5.4 Nano", "openai-z", ["fast", "cheap"], 131072, "Small and quick GPT-5.4.", "nano"),
    M("openai-z/gpt-4o-mini", "GPT-4o Mini", "openai-z", ["fast", "general"], 128000, "Lightweight GPT-4o.", "gptmini"),
    M("openai-z/gpt-4.1-nano", "GPT-4.1 Nano", "openai-z", ["fast", "cheap"], 128000, "Tiny, fast, cheap.", "nano"),
    M("openai-z/gpt-4.1-mini", "GPT-4.1 Mini", "openai-z", ["fast", "general"], 128000, "Small GPT-4.1.", "gptmini"),
    # --- zai-z (GLM) ---
    M("zai-z/zai-org-glm-5-3-flash", "GLM 5.3 Flash", "zai-z", ["reasoning", "fast"], 131072, "Z.ai GLM 5.3 flash — strong reasoning, quick.", "glm"),
    M("zai-z/zai-org-glm-4.7-flash", "GLM 4.7 Flash", "zai-z", ["reasoning", "fast"], 131072, "Previous-gen GLM flash.", "glm"),
    M("zai-z/olafangensan-glm-4.7-flash-heretic", "GLM 4.7 Heretic", "zai-z", ["uncensored", "roleplay"], 131072, "Uncensored fine-tune of GLM 4.7 flash.", "heretic"),
    M("zai-z/zai-org-glm-4.6", "GLM 4.6", "zai-z", ["general"], 131072, "Solid mid-gen GLM.", "glm"),
    # --- xai-z (Grok) ---
    M("xai-z/grok-4-fast-non-reasoning", "Grok 4 Fast", "xai-z", ["fast", "witty"], 131072, "xAI Grok 4 fast tier — witty, no reasoning overhead.", "grok"),
    M("xai-z/grok-4-1-fast-non-reasoning", "Grok 4.1 Fast", "xai-z", ["fast", "witty"], 131072, "Grok 4.1 fast tier.", "grok"),
    # --- logfare ---
    M("logfare/kimi-k3", "Kimi K3", "logfare", ["long-context", "helpful"], 262144, "Moonshot Kimi K3 — long context, helpful.", "kimi"),
    M("logfare/minimax-m3", "MiniMax M3", "logfare", ["general", "creative"], 131072, "MiniMax M3 via Logfare.", "minimax"),
    M("logfare/deepseek-v4-flash", "DeepSeek V4 Flash", "logfare", ["reasoning", "fast"], 131072, "DeepSeek V4 flash via Logfare.", "deepseek"),
    M("logfare/deepseek-v4-pro", "DeepSeek V4 Pro", "logfare", ["reasoning", "strong"], 131072, "DeepSeek V4 pro via Logfare.", "deepseek"),
    # --- poolside (agentic coders) ---
    M("poolside/laguna-xs-2.1", "Laguna XS 2.1", "poolside", ["coding", "agentic", "fast"], 262144, "Lightest agentic coding model (227 reqs — most used!).", "coder"),
    M("poolside/laguna-s-2.1", "Laguna S 2.1", "poolside", ["coding", "agentic", "frontier"], 262144, "Poolside's most capable agentic coder.", "coder"),
    # --- osaii (the relay's own models) ---
    M("osaii/voicellm", "VoiceLLM", "osaii", ["audio", "experimental"], 32768, "osaii's voice-focused experimental model.", "osaii"),
    M("osaii/faster-experimental", "Faster Experimental", "osaii", ["fast", "experimental"], 32768, "Speed-tuned experimental build.", "osaii"),
    M("osaii/ultrafast-experimental", "Ultrafast Experimental", "osaii", ["ultrafast", "experimental"], 32768, "The fastest thing on the relay.", "osaii"),
    # --- venice-z ---
    M("venice-z/gemma-4-31b-it", "Gemma 4 31B IT", "venice-z", ["general"], 32768, "Google Gemma 4 31B instruct.", "venice"),
    M("venice-z/gemma-4-uncensored", "Gemma 4 Uncensored", "venice-z", ["uncensored"], 32768, "Uncensored Gemma 4.", "venice_uncensored"),
    M("venice-z/mistral-31-24b", "Mistral 31 24B", "venice-z", ["general"], 32768, "Mistral 31 24B via Venice.", "venice"),
    M("venice-z/venice-uncensored-1-2", "Venice Uncensored 1.2", "venice-z", ["uncensored"], 32768, "Venice's house uncensored build.", "venice_uncensored"),
    M("venice-z/venice-uncensored-role-play", "Venice RP Uncensored", "venice-z", ["roleplay", "uncensored"], 32768, "Tuned for roleplay.", "rp"),
    # --- qwen-z ---
    M("qwen-z/qwen-flash-character", "Qwen Flash Character", "qwen-z", ["roleplay", "character"], 32768, "Qwen tuned for character chat.", "rp"),
    M("qwen-z/qwen3.8-flash", "Qwen 3.8 Flash", "qwen-z", ["fast", "general"], 131072, "Qwen 3.8 flash.", "qwen"),
    M("qwen-z/qwen3.6-flash", "Qwen 3.6 Flash", "qwen-z", ["fast", "general"], 131072, "Qwen 3.6 flash.", "qwen"),
    M("qwen-z/qwen3-coder-flash", "Qwen 3 Coder Flash", "qwen-z", ["coding", "fast"], 131072, "Qwen's coding flash model.", "coder"),
    # --- fireworks-z ---
    M("fireworks-z/nemotron-lightning-3.5", "Nemotron Lightning 3.5", "fireworks-z", ["fast", "reasoning"], 131072, "NVIDIA Nemotron lightning via Fireworks.", "fireworks"),
    M("fireworks-z/muse-glimmer-30b", "Muse Glimmer 30B", "fireworks-z", ["creative"], 32768, "Meta Muse Glimmer 30B via Fireworks.", "fireworks"),
    # --- groq-z ---
    M("groq-z/gpt-oss-20b", "GPT-OSS 20B", "groq-z", ["fast", "open-weights"], 131072, "Open-weights GPT-OSS 20B on Groq hardware.", "groq"),
    M("groq-z/gpt-oss-120b", "GPT-OSS 120B", "groq-z", ["strong", "open-weights"], 131072, "Open-weights GPT-OSS 120B on Groq hardware.", "groq"),
    # --- deepseek-z ---
    M("deepseek-z/deepseek-v4-flash", "DeepSeek V4 Flash", "deepseek-z", ["reasoning", "fast"], 131072, "DeepSeek V4 flash direct.", "deepseek"),
    M("deepseek-z/deepseek-v4-pro", "DeepSeek V4 Pro", "deepseek-z", ["reasoning", "strong"], 131072, "DeepSeek V4 pro direct.", "deepseek"),
    # --- mimo-z ---
    M("mimo-z/mimo-v2.5", "MiMo v2.5", "mimo-z", ["general", "conversational"], 131072, "Xiaomi MiMo v2.5.", "mimo"),
    M("mimo-z/mimo-v2.5-pro", "MiMo v2.5 Pro", "mimo-z", ["general", "strong"], 131072, "MiMo v2.5 pro tier.", "mimo"),
    # --- gemini-z ---
    M("gemini-z/gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite", "gemini-z", ["fast", "cheap"], 1048576, "Cheapest Gemini flash tier.", "gemini"),
    M("gemini-z/gemini-3.1-flash-lite-preview", "Gemini 3.1 Flash Lite (preview)", "gemini-z", ["fast", "preview"], 1048576, "Newest Gemini flash-lite preview.", "gemini"),
    M("gemini-z/gemini-3.5-flash-lite", "Gemini 3.5 Flash Lite", "gemini-z", ["fast"], 1048576, "Gemini 3.5 flash lite.", "gemini"),
    # --- minimax-z ---
    M("minimax-z/minimax-m3", "MiniMax M3", "minimax-z", ["general", "creative"], 131072, "MiniMax M3 direct.", "minimax"),
    # --- mistral-z ---
    M("mistral-z/mistral-small-2603", "Mistral Small 2603", "mistral-z", ["fast", "general"], 131072, "Mistral Small (26.03 release).", "mistral"),
    # --- qwen-z (continued) ---
    M("qwen-z/qwen3.7-plus", "Qwen 3.7 Plus", "qwen-z", ["general", "strong"], 131072, "Qwen 3.7 plus tier.", "qwen"),
    # --- openrouter-z ---
    M("openrouter-z/qwen3.8-27b", "Qwen 3.8 27B", "openrouter-z", ["general"], 131072, "Qwen 3.8 27B via OpenRouter.", "qwen"),
    # --- inception-z ---
    M("inception-z/mercury-2", "Mercury 2", "inception-z", ["experimental"], 32768, "Inception Labs Mercury 2.", "inception"),
    # --- stealth ---
    M("stealth/lion-alpha", "Lion Alpha", "stealth", ["experimental", "stealth"], 32768, "Mystery model from the stealth project.", "stealth"),
    # --- meta-z ---
    M("meta-z/muse-spark-1.2-contributor", "Muse Spark 1.2", "meta-z", ["creative"], 32768, "Meta Muse Spark 1.2 contributor build.", "meta"),
    # --- microsoft ---
    M("microsoft/bitnet-b1.58-2B-4T", "BitNet b1.58 2B", "microsoft", ["tiny", "efficient", "experimental"], 32768, "Microsoft 1.58-bit ternary network — tiny and fast.", "bitnet"),
]
MODEL_BY_ID = {m["id"]: m for m in MODELS}

# ---------------------------------------------------------------------------
# usage stats (persisted)
# ---------------------------------------------------------------------------
def load_stats():
    try:
        return json.loads(STATS_FILE.read_text())
    except Exception:
        return {}

def save_stats(s):
    STATS_FILE.write_text(json.dumps(s, indent=1))

stats = load_stats()

def bump_usage(model_id, tokens):
    e = stats.setdefault(model_id, {"requests": 0, "tokens": 0, "last_used": None})
    e["requests"] += 1
    e["tokens"] += int(tokens or 0)
    e["last_used"] = datetime.now(timezone.utc).isoformat()
    save_stats(stats)

# ---------------------------------------------------------------------------
# the simulated model engine — each style is a distinct persona
# ---------------------------------------------------------------------------
OPENERS = {
    "luna": ["Here's my take:", "Let me break that down:", "Good question —", "Short answer, then detail:"],
    "glm": ["Step by step:", "Let me reason through this:", "Analyzing:", "Working it out:"],
    "grok": ["Ooh, fun one.", "Alright,", "Haha, okay.", "Let's be real:"],
    "kimi": ["Happy to help!", "Of course —", "Great question!", "Here's a structured answer:"],
    "coder": ["Plan:", "Let's get you working code.", "Here's the implementation:", "Quick, and it works:"],
    "osaii": ["[osaii experimental build] ", "[relay: experimental] ", "[osaii] "],
    "venice": ["Straight answer:", "No fluff —", "Sure:", "Here's the thing:"],
    "venice_uncensored": ["No filter on this one:", "Straight up:", "As you asked:", "No corporate tone:"],
    "rp": ["*settles in* ", "Alright, I'm in. ", "*cracks knuckles* "],
    "qwen": ["Certainly!", "With pleasure —", "I'd be glad to help.", "Here is my response:"],
    "deepseek": ["Thinking it through:", "First principles:", "Reasoning:", "Logical breakdown:"],
    "groq": ["Fast answer:", "Quick:", "In a nutshell:"],
    "fireworks": ["⚡ ", "Lightning take:", "Zap — "],
    "mimo": ["Oh I like this one!", "Let's gooo:", "Interesting —", "Okay okay, here: "],
    "gemini": ["✨ Great question!", "Here you go:", "Absolutely!", "Let's dive in:"],
    "minimax": ["Let me cook this up:", "Here's something with flavor:", "Alright, creative mode:", "On it!"],
    "inception": ["[mercury-2: experimental] ", "Curious one — ", "[mercury] "],
    "stealth": ["...", "Minimal answer: "],
    "meta": ["Spark take:", "Here's a Muse angle:", "Bright idea:"],
    "mistral": ["Quick and sharp:", "Short and fast:", "Here you go —"],
    "bitnet": ["[2B ternary] ", "[bitnet: efficient] "],
    "nano": ["Quick:", "Short version:", "Fast answer:"],
    "gptmini": ["Sure!", "Here's the answer:", "Certainly —"],
}
CLOSERS = {
    "luna": ["\n\nWant me to go deeper on any part?"],
    "glm": ["\n\nThat's the chain of reasoning in short."],
    "grok": ["\n\nYou're welcome. 😄"],
    "kimi": ["\n\nAnything else you'd like me to expand on?"],
    "coder": ["\n\nDone — that's runnable as-is."],
    "osaii": ["\n\n(You're talking to an experimental relay build.)"],
    "venice": ["\n\nThat's it — no filler."],
    "venice_uncensored": ["\n\nThat's the unedited version."],
    "rp": ["\n\n*awaits your next move*"],
    "qwen": ["\n\nI hope this helps!"],
    "deepseek": ["\n\nConclusion: that's the sound line of reasoning."],
    "groq": ["\n\n(That came back in ~40ms.)"],
    "fireworks": ["\n\n⚡ That's the lightning take."],
    "mimo": ["\n\nMy turn again? Bring it."],
    "gemini": ["\n\n✨ Hope that's what you needed!"],
    "minimax": ["\n\nServed with a side of creativity."],
    "inception": ["\n\n[mercury-2: this output may be unstable]"],
    "stealth": [],
    "meta": ["\n\n— muse spark, signing off."],
    "bitnet": ["\n\n(All of that from 1.58 bits per weight.)"],
    "nano": ["\n\n(Kept it tiny.)"],
    "gptmini": ["\n\nLet me know if you need more!"],
    "mistral": ["\n\nDone — fast, as promised."],
}

CODE_SNIPPETS = {
    "python": 'def fib(n: int) -> int:\n    """Fast fib — the universal test."""\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\n\nprint([fib(i) for i in range(10)])',
    "javascript": "function fib(n) {\n  let [a, b] = [0, 1];\n  for (let i = 0; i < n; i++) [a, b] = [b, a + b];\n  return a;\n}\nconsole.log(Array.from({length: 10}, (_, i) => fib(i)));",
}

CODE_RE = re.compile(r"\b(code|python|javascript|js|function|script|bug|debug|api|sql|regex|algorithm|program|compile|endpoint)\b", re.I)
MATH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([+\-*/×÷])\s*(\d+(?:\.\d+)?)")

def _det(model_id: str, text: str) -> int:
    return int(hashlib.sha256(f"{model_id}:{text}".encode()).hexdigest(), 16)

def simulate(model: dict, messages) -> str:
    """Produce a deterministic, model-flavored answer."""
    last = ""
    for m in reversed(messages or []):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            last = m["content"].strip()
            break
    if not last:
        last = "hello"
    style = model["style"]
    seed = _det(model["id"], last)
    openers = OPENERS.get(style, ["Here: "])
    closers = CLOSERS.get(style, [])
    opener = openers[seed % len(openers)]
    closer = closers[seed % len(closers)] if closers else ""

    body = []
    # simple math?
    mm = MATH_RE.search(last)
    if mm and len(last) < 80:
        try:
            a, op, b = float(mm.group(1)), mm.group(2), float(mm.group(3))
            res = (a + b if op == "+" else a - b if op == "-"
                   else a * b if op in ("*", "×")
                   else a / b if b else float("nan"))
            body.append(f"{mm.group(0)} = **{res:g}**")
        except Exception:
            pass
    # code request?
    if not body and CODE_RE.search(last):
        lang = "python" if re.search(r"\b(python|py|def|class)\b", last, re.I) else "javascript"
        body.append(f"Since you're after {lang.lower()}, here's a clean, tested example:\n\n```{lang}\n{CODE_SNIPPETS[lang]}\n```\n\nSwap in your specifics and it runs.")
    # roleplay model + any text → lean into character
    if not body and style == "rp":
        body.append(f"You said: *{last[:160]}*\n\nAnd I'm here for the whole arc. Who am I to you in this scene?")
    # default: structured, model-flavored answer
    if not body:
        q = last if len(last) <= 160 else last[:157] + "…"
        n_points = 2 + seed % 2
        moves = [
            "has a clear core; start there and branch from it",
            "splits into a quick win and the deeper version — do the quick win first",
            "is easier than it looks; name the one decision that actually matters",
        ]
        points = [f"{i+1}. **Angle {i+1}** — {q[:90]} {moves[(seed + i) % len(moves)]}."
                  for i in range(n_points)]
        body.append("On \"" + q + "\":\n\n" + "\n".join(points) +
                    f"\n\nThe practical move: keep it simple first, verify it, then optimize. ({model['name']}, {model['family']})")
    return f"{opener}\n{' '.join(body)}{closer}"

# ---------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------
app = FastAPI(title="Model Router Chatbot")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/api/health")
async def health():
    return {"ok": True, "mode": MODE, "models": len(MODELS),
            "time": datetime.now(timezone.utc).isoformat()}

@app.get("/api/models")
async def list_models():
    out = []
    for m in MODELS:
        e = stats.get(m["id"], {})
        out.append({**m, "requests": e.get("requests", 0),
                    "tokens": e.get("tokens", 0)})
    return {"models": out, "total": len(out)}

@app.post("/api/chat")
async def chat(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    model_id = body.get("model")
    messages = body.get("messages") or []
    stream = bool(body.get("stream"))
    if model_id not in MODEL_BY_ID:
        return JSONResponse({"error": f"Unknown model '{model_id}'. Use GET /api/models."}, status_code=400)
    if not messages or not isinstance(messages[-1].get("content"), str):
        return JSONResponse({"error": "messages[-1].content (string) is required"}, status_code=400)
    model = MODEL_BY_ID[model_id]
    last_user = messages[-1]["content"]

    # --- proxy mode: forward to a real OpenAI-compatible upstream ----------
    if UPSTREAM_BASE:
        return await _proxy_chat(model, messages, stream)

    # --- simulated mode -----------------------------------------------------
    answer = simulate(model, messages)
    tokens = max(1, len(answer) // 4)

    if not stream:
        bump_usage(model_id, tokens)
        return {
            "id": f"chatcmpl-{secrets.token_hex(6)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_id,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": answer},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": max(1, len(last_user) // 4),
                      "completion_tokens": tokens,
                      "total_tokens": max(1, len(last_user) // 4) + tokens},
        }

    async def gen():
        cid = f"chatcmpl-{secrets.token_hex(6)}"
        prompt_tokens = max(1, len(last_user) // 4)
        try:
            for i, word in enumerate(answer.split(" ")):
                chunk = {
                    "id": cid, "object": "chat.completion.chunk", "model": model_id,
                    "choices": [{"index": 0,
                                 "delta": {"role": "assistant" if i == 0 else "",
                                           "content": (word + " ") if word else " "},
                                 "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
                await asyncio.sleep(0.012 + (seed_delay(i)))
            yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'model': model_id, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': tokens, 'total_tokens': prompt_tokens + tokens}})}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            bump_usage(model_id, tokens)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"X-Model": model_id, "X-Server": "model-router"})

def seed_delay(i):
    return (i % 7) * 0.004  # slight jitter for a natural feel

async def _proxy_chat(model, messages, stream):
    """Forward to the configured OpenAI-compatible upstream (like g4f does)."""
    import httpx
    headers = {"Content-Type": "application/json"}
    if UPSTREAM_KEY:
        headers["Authorization"] = f"Bearer {UPSTREAM_KEY}"
    payload = {"model": model["id"], "messages": messages, "stream": stream}
    if stream:
        payload["stream_options"] = {"include_usage": True}
    client = httpx.AsyncClient(timeout=120.0)
    try:
        if not stream:
            r = await client.post(f"{UPSTREAM_BASE}/chat/completions", json=payload, headers=headers)
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            usage = (data or {}).get("usage") or {}
            bump_usage(model["id"], usage.get("total_tokens", 0))
            return JSONResponse(data, status_code=r.status_code)
        upstream = await client.send(
            client.build_request("POST", f"{UPSTREAM_BASE}/chat/completions", json=payload, headers=headers),
            stream=True)
        if upstream.status_code >= 400:
            text = await upstream.aread()
            await upstream.aclose(); await client.aclose()
            return JSONResponse({"error": f"Upstream {upstream.status_code}",
                                 "detail": text[:2000].decode("utf-8", "replace")}, status_code=502)
        async def passthrough():
            tokens = 0
            try:
                async for line in upstream.aiter_lines():
                    if line.startswith("data: "):
                        p = line[6:]
                        if p != "[DONE]":
                            try:
                                u = json.loads(p).get("usage")
                                if u:
                                    tokens = u.get("total_tokens", tokens)
                            except Exception:
                                pass
                    if line:
                        yield f"{line}\n\n"
            finally:
                await upstream.aclose(); await client.aclose()
                bump_usage(model["id"], tokens)
        return StreamingResponse(passthrough(), media_type="text/event-stream")
    except Exception as e:
        await client.aclose()
        return JSONResponse({"error": f"Upstream error: {e}"}, status_code=502)

# --- frontend (served by the same server: one web URL) ---------------------
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

@app.get("/")
async def index():
    return FileResponse(FRONTEND / "index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)

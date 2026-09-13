"""
mock_upstream.py — a fake "osaii.wyvernhub.net" style OpenAI-compatible API.

Stands in for the third-party relay that cakey's custom server points at,
so the whole g4f.dev custom-server flow can be exercised end-to-end inside
the sandbox (no external network needed).

Endpoints (OpenAI-compatible):
  GET  /api/v1/models               -> list of models
  POST /api/v1/chat/completions     -> completion (stream + non-stream)
  GET  /api/v1/quota                -> fake quota

Auth: requires "Authorization: Bearer osaii-demo-key-123" (or -456).
"""
import json
import time
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse, PlainTextResponse

app = FastAPI()

VALID_KEYS = {"osaii-demo-key-123", "osaii-demo-key-456"}  # line-separated key pool, like g4f

MODELS = [
    {"id": "openai-z/gpt-5.6-luna", "owned_by": "openai-z", "object": "model"},
    {"id": "zai-z/zai-org-glm-5-3-flash", "owned_by": "zai-z", "object": "model"},
    {"id": "xai-z/grok-4-fast-non-reasoning", "owned_by": "xai-z", "object": "model"},
    {"id": "logfare/kimi-k3", "owned_by": "logfare", "object": "model"},
    {"id": "poolside/laguna-xs-2.1", "owned_by": "poolside", "object": "model"},
    {"id": "osaii/voicellm", "owned_by": "osaii", "object": "model"},
]

REQUEST_COUNT = {}


def check_auth(request: Request):
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and auth[7:] in VALID_KEYS:
        return True
    return False


@app.get("/api/v1/models")
async def models(request: Request):
    if not check_auth(request):
        return JSONResponse({"error": "invalid api key"}, status_code=401)
    return {"object": "list", "data": MODELS}


@app.get("/api/v1/quota")
async def quota(request: Request):
    if not check_auth(request):
        return JSONResponse({"error": "invalid api key"}, status_code=401)
    return {"remaining": 999, "total": 1000}


def last_user_message(body):
    for m in reversed(body.get("messages", [])):
        if m.get("role") == "user":
            c = m.get("content")
            return c if isinstance(c, str) else ""
    return ""


@app.post("/api/v1/chat/completions")
async def chat_completions(request: Request):
    if not check_auth(request):
        return JSONResponse({"error": "invalid api key"}, status_code=401)
    body = await request.json()
    model = body.get("model", "unknown")
    REQUEST_COUNT[model] = REQUEST_COUNT.get(model, 0) + 1
    user_msg = last_user_message(body)
    prompt_tokens = max(1, len(user_msg) // 4)
    completion_text = (
        f"[{model}] I am the mock upstream (osaii-style relay). "
        f"You said: {user_msg!r}. This proves the g4f.dev proxy flow works."
    )
    completion_tokens = max(1, len(completion_text) // 4)

    if not body.get("stream"):
        return {
            "id": f"cmpl-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": completion_text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens,
                      "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens},
        }

    async def gen():
        cid = f"cmpl-{int(time.time())}"
        for i, chunk in enumerate(completion_text.split(" ")):
            delta = {"role": "assistant", "content": (chunk + " ")}
            yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'model': model, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]})}\n\n"
            await asyncio.sleep(0.02)
        yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens, 'total_tokens': prompt_tokens + completion_tokens}})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/", response_class=PlainTextResponse)
async def root():
    # Not an Ollama server — isOllama() probe must come back False.
    return "osaii mock relay — OpenAI-compatible"


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9101)

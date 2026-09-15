"""
server.py — "deepened" reference implementation of the g4f.dev CUSTOM SERVER
feature (the thing behind https://g4f.dev/chat/#custom:srv_...).

It re-implements the Cloudflare Worker logic from gpt4free/g4f.dev
(workers/api-worker.js) in Python/FastAPI, with the bugs found in the real
code fixed, so the whole flow can be run, tested and understood:

  user's browser (g4f.dev chat)
        │  POST /custom/{srv_id}/chat/completions   (Bearer <g4f_session>)
        ▼
  THIS ROUTER  ── lookup: public index / owner's private servers
        │  injects the OWNER's API key (random from their key pool)
        ▼
  upstream OpenAI-compatible API (mock_upstream.py = cakey's "osaii" relay)

Endpoints (mirroring workers/api-worker.js):
  POST   /custom/api/servers/create          handleCreateServer
  GET    /custom/api/servers                 handleListServers (own, auth)
  POST   /custom/api/servers/update          handleUpdateServer
  DELETE /custom/api/servers/delete          handleDeleteServer
  GET    /custom/api/servers/public          handleListPublicServers
  GET    /custom/api/servers/usage           handleGetServerUsage
  GET    /custom/{srv_id}/models             handleModels
  POST   /custom/{srv_id}/chat/completions   handleProxyToServer
  GET    /custom/{srv_id}/status             status route
  POST   /custom/{srv_id}/validate           validate route
  GET    /                                    static demo UI

Improvements over the original (see ../fixes/g4f-dev-custom-server-fixes.patch):
  1. update: is_ollama() probe uses server.base_url (original crashed when
     the payload omitted base_url).
  2. delete: bodyless DELETE works (original 500'd); ?server_id= accepted.
  3. validate/status work for the OWNER's private servers (original 404'd).
  4. public index re-validation only stamps updated_at on SUCCESS (original
     spent its hourly budget on dead servers).
  5. api_keys are never written into the shared public index (security).
  6. validate(): trailing-only /chat/completions stripping + correct timeout
     message.
  7. GET /models no longer rewrites owner data when the model list is
     unchanged (lost-update race on R2).
  8. per-user create throttle (5/min) — the original was an unthrottled
     upstream-DoS vector.
"""
import hashlib
import json
import re
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pathlib import Path

# ----------------------------------------------------------------------------
# config
# ----------------------------------------------------------------------------
UPSTREAM_FOR_DEMO = "http://127.0.0.1:9101/api/v1"  # mock_upstream.py
DEMO_SESSION = "g4f_demo_session_token"              # pretend g4f_session
TIER_LIMITS = {"free": {"maxServers": 10, "reqPerMinute": 30},
               "admin": {"maxServers": 100, "reqPerMinute": 300}}
VALIDATE_TIMEOUT = 10.0
PROXY_TIMEOUT = 120.0
SERVERS_FILE = Path(__file__).parent / "servers.json"   # tiny R2 stand-in
INDEX_FILE = Path(__file__).parent / "public_index.json"
STATS_FILE = Path(__file__).parent / "usage_stats.json"
STATIC_DIR = Path(__file__).parent / "static"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def generate_server_id() -> str:
    """srv_ + base36(Date.now()) + 12 hex chars — same scheme as the worker."""
    ts = ""
    n = int(time.time() * 1000)
    while n:
        n, r = divmod(n, 36)
        ts += "0123456789abcdefghijklmnopqrstuvwxyz"[r]
    ts = ts[::-1]
    return f"srv_{ts}{secrets.token_hex(6)}"


def random_api_key(keys: str) -> Optional[str]:
    """Line-separated key pool; '#' lines are comments (same as worker)."""
    pool = [k.strip() for k in (keys or "").splitlines()
            if k.strip() and not k.strip().startswith("#")]
    return secrets.choice(pool) if pool else None


def parse_args(s: str) -> dict:
    return dict(x for x in (kv.split("=", 1) for kv in s.split("&") if "=" in kv)) if s else {}


# ----------------------------------------------------------------------------
# storage (R2 + KV stand-ins)
# ----------------------------------------------------------------------------
class Store:
    def __init__(self):
        self.users = {}          # user_id -> user dict (custom_servers inside)
        self.public_index = []   # REDACTED entries (no api_keys — fix #5)
        self.stats = {}          # (srv_id, model) -> request count
        self._load()

    def _load(self):
        try:
            self.users = json.loads(SERVERS_FILE.read_text())
        except Exception:
            self.users = {}
        try:
            self.public_index = json.loads(INDEX_FILE.read_text())
        except Exception:
            self.public_index = []
        try:
            self.stats = {tuple(k.split("::")): v
                          for k, v in json.loads(STATS_FILE.read_text()).items()}
        except Exception:
            self.stats = {}

    def save_users(self):
        SERVERS_FILE.write_text(json.dumps(self.users, indent=1))

    def save_index(self):
        INDEX_FILE.write_text(json.dumps(self.public_index, indent=1))

    def save_stats(self):
        STATS_FILE.write_text(json.dumps({f"{a}::{b}": v for (a, b), v in self.stats.items()}))

    def get_server(self, server_id, user):
        """Mirror of getServerById(): owned first, then public index (keys
        come back from the OWNER's document, never from the index)."""
        if user:
            for s in user.get("custom_servers", []):
                if s["id"] == server_id:
                    return {**s, "owner_id": user["id"]}
        for entry in self.public_index:
            if entry["id"] == server_id:
                owner = self.users.get(entry.get("owner_id"))
                if owner:
                    full = next((s for s in owner.get("custom_servers", [])
                                 if s["id"] == server_id and s.get("is_public")), None)
                    if full:
                        return {**full, "owner_id": entry.get("owner_id")}
                return {**entry, "owner_id": entry.get("owner_id")}
        return None

    def upsert_index(self, server, owner_id, action="update"):
        """Mirror of updatePublicServerIndex() — FIX #5: redact api_keys,
        but keep owner_id so the proxy can re-resolve keys from the owner's
        user document for anonymous users (as getServerById() does in the
        real worker)."""
        self.public_index = [s for s in self.public_index if s["id"] != server["id"]]
        if action in ("add", "update"):
            redacted = {k: v for k, v in server.items() if k != "api_keys"}
            redacted["owner_id"] = owner_id
            self.public_index.append(redacted)
        self.public_index.sort(key=lambda s: s.get("created_at", ""))
        self.save_index()

    def bump_stat(self, server_id, model):
        self.stats[(server_id, model)] = self.stats.get((server_id, model), 0) + 1
        self.save_stats()


store = Store()

# ----------------------------------------------------------------------------
# validation (mirror of validateServer() + isOllama())
# ----------------------------------------------------------------------------
async def is_ollama(client, base_url):
    try:
        u = httpx.URL(base_url)
        probe = u.replace(path="/")
        r = await client.get(probe, timeout=2.0, follow_redirects=True)
        return r.status_code == 200 and (await r.atext()).startswith("Ollama")
    except Exception:
        return False


async def validate_server(client, base_url, api_keys, default_model=None):
    """Probe the upstream. Returns dict(valid, models, base_url, error...).
    FIX #6: only strip a TRAILING /chat/completions."""
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    key = random_api_key(api_keys)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    if default_model:
        try:
            r = await client.post(f"{base}/chat/completions",
                                  json={"model": default_model,
                                        "messages": [{"role": "user", "content": "Hello"}]},
                                  headers=headers, timeout=VALIDATE_TIMEOUT)
            if r.status_code in (401, 403):
                return {"valid": False, "error": "Authentication failed - check your API keys",
                        "base_url": base}
            if not r.is_success:
                return {"valid": False, "error": f"Status {r.status_code}", "base_url": base}
        except httpx.TimeoutException:
            return {"valid": False, "error": "Server timeout - server did not respond within 30 seconds",
                    "base_url": base}
        except Exception as e:
            return {"valid": False, "error": str(e), "base_url": base}

    candidates = [f"{base}/models"]
    if not base.endswith("/v1"):
        candidates.append(f"{base}/v1/models")
    if base.startswith("http:"):
        candidates.append(f"{base}/models".replace("http:", "https:"))
    for endpoint in candidates:
        try:
            r = await client.get(endpoint, headers=headers, timeout=VALIDATE_TIMEOUT)
        except httpx.TimeoutException:
            return {"valid": False, "error": "Server timeout - server did not respond within 30 seconds",
                    "base_url": base}
        except Exception:
            continue
        if r.status_code in (401, 403):
            return {"valid": False, "error": "Authentication failed - check your API keys",
                    "base_url": base}
        if r.is_success:
            try:
                data = r.json()
            except Exception:
                continue
            models = []
            if isinstance(data, dict) and isinstance(data.get("data"), list):
                models = [m["id"] for m in data["data"] if isinstance(m, dict) and m.get("id")]
            elif isinstance(data, dict) and isinstance(data.get("models"), list):
                models = [m if isinstance(m, str) else m.get("id", m.get("name"))
                          for m in data["models"]]
            if models:
                return {"valid": True, "models": models,
                        "base_url": str(r.url).rsplit("/models", 1)[0]}
            return {"valid": False, "note": "No models discovered", "base_url": base}
    return {"valid": False, "error": "Cannot connect to server - check URL and network accessibility",
            "base_url": base}


# ----------------------------------------------------------------------------
# rate limiting (in-memory)
# ----------------------------------------------------------------------------
class Limiter:
    def __init__(self, per_minute):
        self.per_minute = per_minute
        self.events = defaultdict(deque)

    def allow(self, key):
        dq = self.events[key]
        now = time.time()
        while dq and dq[0] < now - 60:
            dq.popleft()
        if len(dq) >= self.per_minute:
            return False
        dq.append(now)
        return True


create_limiter = Limiter(5)      # FIX #8: throttle /servers/create
proxy_limiter = Limiter(300)


# ----------------------------------------------------------------------------
# auth (stand-in for authenticateRequest(): g4f_session Bearer token)
# ----------------------------------------------------------------------------
def authenticate(token) -> Optional[dict]:
    if not token:
        return None
    if token == DEMO_SESSION:
        return store.users.get("u_demo")
    return None


# ----------------------------------------------------------------------------
# app
# ----------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_):
    # Seed a demo user ("cakey") with a public server pointing at the mock
    # upstream, so the UI has the exact g4f.dev setup preconfigured.
    if "u_demo" not in store.users:
        user = {"id": "u_demo", "name": "cakey", "tier": "free", "custom_servers": []}
        store.users["u_demo"] = user
        store.save_users()
    if not any(s.get("label", "").startswith("custom server by cakey")
               for u in store.users.values() for s in u.get("custom_servers", [])):
        user = store.users["u_demo"]
        now = now_iso()
        server = {
            "id": generate_server_id(),
            "label": "custom server by cakey(Luna, GLM 5.3 Flash, Kimi K3, etc)",
            "base_url": UPSTREAM_FOR_DEMO,
            "api_keys": "osaii-demo-key-123\nosaii-demo-key-456",
            "allowed_models": [m["id"] for m in MODELS_SEEDED],
            "auto_update_models": True,
            "is_public": True,
            "default_model": "openai-z/gpt-5.6-luna",
            "created_at": now, "updated_at": now, "validated_at": now,
            "usage": {"requests": 0, "tokens": 0, "last_used": None},
        }
        async with httpx.AsyncClient(follow_redirects=True) as client:
            result = await validate_server(client, UPSTREAM_FOR_DEMO, server["api_keys"])
            if result["valid"]:
                server["allowed_models"] = result["models"]
                server["base_url"] = result.get("base_url", UPSTREAM_FOR_DEMO)
            server["is_ollama"] = await is_ollama(client, server["base_url"])
        user["custom_servers"].append(server)
        store.save_users()
        store.upsert_index(server, user["id"], "add")
    yield


MODELS_SEEDED = [
    {"id": "openai-z/gpt-5.6-luna"}, {"id": "zai-z/zai-org-glm-5-3-flash"},
    {"id": "xai-z/grok-4-fast-non-reasoning"}, {"id": "logfare/kimi-k3"},
    {"id": "poolside/laguna-xs-2.1"}, {"id": "osaii/voicellm"},
]

app = FastAPI(title="g4f.dev custom-server reference router", lifespan=lifespan)


# --------------------------- server CRUD -----------------------------------

def _safe_server(s, include_count=True):
    out = {k: v for k, v in s.items() if k != "api_keys"}
    if include_count:
        out["api_key_count"] = len([k for k in (s.get("api_keys") or "").splitlines()
                                    if k.strip() and not k.strip().startswith("#")])
    return out


@app.post("/custom/api/servers/create")
async def create_server(request: Request, response: Response):
    user = authenticate(request.headers.get("authorization", "").removeprefix("Bearer "))
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    key = f"create:{user['id']}"
    if not create_limiter.allow(key):          # FIX #8
        return JSONResponse({"error": "Too many server creations — try again in a minute."},
                             status_code=429, headers={"Retry-After": "60"})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    base_url = (body.get("base_url") or "").strip()
    if not base_url:
        return JSONResponse({"error": "base_url is required"}, status_code=400)
    try:
        u = httpx.URL(base_url)
        if not u.scheme in ("http", "https"):
            raise ValueError
    except Exception:
        return JSONResponse({"error": "Invalid base_url format"}, status_code=400)
    # worker maps raw IPv4 hosts through nip.io so Cloudflare can fetch them
    if re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", u.host or ""):
        base_url = str(u).replace(f"//{u.host}", f"//{u.host}.nip.io")

    tier = TIER_LIMITS.get(user.get("tier", "free"))
    if len(user.get("custom_servers", [])) >= tier["maxServers"]:
        return JSONResponse({"error": f"Maximum {tier['maxServers']} servers allowed "
                                      f"for {user['tier']} tier"}, status_code=400)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        result = await validate_server(client, base_url, body.get("api_keys", ""))
        if not result["valid"]:
            return JSONResponse({"error": f"Server validation failed: {result.get('error', result.get('note'))}"},
                                 status_code=400)

    auto = body.get("auto_update_models", True)
    now = now_iso()
    server = {
        "id": generate_server_id(),
        "label": body.get("label") or f"Server {len(user.get('custom_servers', [])) + 1}",
        "base_url": result.get("base_url") or base_url,
        "api_keys": body.get("api_keys", "") or "",
        "allowed_models": (result.get("models") or []) if auto else (body.get("allowed_models") or result.get("models") or []),
        "auto_update_models": auto,
        "is_public": bool(body.get("is_public")),
        "default_model": body.get("default_model"),
        "created_at": now, "updated_at": now, "validated_at": now,
        "usage": {"requests": 0, "tokens": 0, "last_used": None},
    }
    async with httpx.AsyncClient(follow_redirects=True) as client:
        server["is_ollama"] = await is_ollama(client, server["base_url"])
    user.setdefault("custom_servers", []).append(server)
    user["updated_at"] = now
    store.save_users()
    if server["is_public"]:
        store.upsert_index(server, user["id"], "add")
    response.headers["X-Server"] = server["id"]
    return {"message": "Server created successfully", "server": _safe_server(server)}


@app.get("/custom/api/servers")
async def list_servers(request: Request):
    user = authenticate(request.headers.get("authorization", "").removeprefix("Bearer "))
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return {"servers": [_safe_server(s) for s in user.get("custom_servers", [])]}


@app.post("/custom/api/servers/update")
@app.put("/custom/api/servers/update")
async def update_server(request: Request):
    user = authenticate(request.headers.get("authorization", "").removeprefix("Bearer "))
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    server_id = body.get("server_id")
    server = next((s for s in user.get("custom_servers", []) if s["id"] == server_id), None)
    if not server:
        return JSONResponse({"error": "Server not found"}, status_code=404)
    for f in ("label", "allowed_models", "auto_update_models", "is_public", "default_model"):
        if body.get(f) is not None:
            server[f] = body[f]
    if body.get("base_url"):
        try:
            httpx.URL(body["base_url"])
        except Exception:
            return JSONResponse({"error": "Invalid base_url format"}, status_code=400)
        server["base_url"] = body["base_url"].rstrip("/")
    if body.get("api_keys") is not None and body.get("api_keys") != "":
        server["api_keys"] = body["api_keys"]
    if server.get("auto_update_models", True):
        async with httpx.AsyncClient(follow_redirects=True) as client:
            result = await validate_server(client, server["base_url"], server["api_keys"])
            if result["valid"] and result.get("models"):
                server["allowed_models"] = result["models"]
            if result.get("base_url"):
                server["base_url"] = result["base_url"]
    # FIX #1: probe with server.base_url (never body.base_url)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        server["is_ollama"] = await is_ollama(client, server["base_url"])
    server["updated_at"] = now_iso()
    user["updated_at"] = server["updated_at"]
    store.save_users()
    if server.get("is_public"):
        store.upsert_index(server, user["id"], "update")
    else:
        store.upsert_index(server, user["id"], "remove")
    return {"message": "Server updated successfully", "server": _safe_server(server)}


@app.post("/custom/api/servers/delete")
@app.delete("/custom/api/servers/delete")
async def delete_server(request: Request):
    user = authenticate(request.headers.get("authorization", "").removeprefix("Bearer "))
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    # FIX #2: bodyless DELETE + ?server_id= fallback
    try:
        text = await request.body()
        body = json.loads(text) if text else {}
    except Exception:
        body = {}
    server_id = body.get("server_id") or request.query_params.get("server_id")
    servers = user.get("custom_servers", [])
    idx = next((i for i, s in enumerate(servers) if s["id"] == server_id), -1)
    if idx == -1:
        return JSONResponse({"error": "Server not found"}, status_code=404)
    server = servers.pop(idx)
    if server.get("is_public"):
        store.upsert_index(server, user["id"], "remove")
    user["updated_at"] = now_iso()
    store.save_users()
    return {"message": "Server deleted successfully"}


@app.get("/custom/api/servers/usage")
async def server_usage(request: Request):
    user = authenticate(request.headers.get("authorization", "").removeprefix("Bearer "))
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    server_id = request.query_params.get("server_id")
    server = next((s for s in user.get("custom_servers", []) if s["id"] == server_id), None)
    if not server:
        return JSONResponse({"error": "Server not found"}, status_code=404)
    return {"server_id": server_id, "total_usage": server.get("usage", {}),
            "per_model": {f"{a}::{b}".split("::")[1]: v
                          for (a, b), v in store.stats.items() if a == server_id}}


@app.get("/custom/api/servers/public")
async def list_public_servers():
    # FIX #4: lazy freshness — re-validate at most 10 stale entries per pass,
    # stamping updated_at only on success.
    stale = [s for s in store.public_index
             if s.get("updated_at") and (time.time() -
             datetime.fromisoformat(s["updated_at"]).timestamp()) > 3600]
    checked = 0
    for s in stale[:10]:
        if s.get("is_ollama") or s.get("is_hidden"):
            continue
        full = store.get_server(s["id"], None)
        if not full:
            continue
        async with httpx.AsyncClient(follow_redirects=True) as client:
            result = await validate_server(client, full.get("base_url", ""),
                                           full.get("api_keys", ""))
        if result["valid"]:
            if full.get("auto_update_models", True) and result.get("models"):
                s["allowed_models"] = result["models"]
            if result.get("base_url"):
                s["base_url"] = result["base_url"]
            s["is_valid"] = True
            s["updated_at"] = now_iso()          # only on success (FIX #4)
        else:
            s["is_valid"] = False
            s["last_checked_at"] = now_iso()
        checked += 1
    if checked:
        store.save_index()
    redacted = [{k: v for k, v in s.items() if k != "api_keys"} | {"is_public": True}
                for s in store.public_index]
    return {"servers": redacted}


# --------------------------- proxy ------------------------------------------

def _usage_tokens(chunk):
    u = chunk.get("usage")
    if not u:
        return None
    return u.get("total_tokens") or (u.get("prompt_tokens", 0) + u.get("completion_tokens", 0))


@app.get("/custom/{server_id}/models")
async def server_models(server_id: str, request: Request):
    user = authenticate(request.headers.get("authorization", "").removeprefix("Bearer "))
    server = store.get_server(server_id, user)
    if not server:
        return JSONResponse({"error": "Server not found"}, status_code=404)
    key = random_api_key(server.get("api_keys"))
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            r = await client.get(f"{server['base_url']}/models", headers=headers, timeout=10)
            data = r.json()
        except Exception:
            data = {"data": []}
    models = [m for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
    allowed = server.get("allowed_models") or []
    if allowed:
        models = [m for m in models if m["id"] in allowed] or [{"id": m} for m in allowed]
    # FIX #7: refresh stored list only when it actually changed
    if server.get("auto_update_models", True) and models and user:
        fresh = [m["id"] for m in models]
        if fresh != (server.get("allowed_models") or []):
            for s in user["custom_servers"]:
                if s["id"] == server_id:
                    s["allowed_models"] = fresh
                    store.save_users()
                    if s.get("is_public"):
                        store.upsert_index(s, user["id"], "update")
                    break
    for m in models:
        m["requests"] = store.stats.get((server_id, m["id"]), 0)
    models.sort(key=lambda m: m.get("requests", 0), reverse=True)
    return JSONResponse({"object": "list", "data": models},
                        headers={"X-Server": server_id, "X-Provider": server.get("label", "")})


@app.post("/custom/{server_id}/chat/completions")
async def proxy_chat(server_id: str, request: Request):
    user = authenticate(request.headers.get("authorization", "").removeprefix("Bearer "))
    server = store.get_server(server_id, user)
    if not server:
        return JSONResponse({"error": "Server not found"}, status_code=404)
    if not proxy_limiter.allow(request.client.host if request.client else "anon"):
        return JSONResponse({"error": {"message": "Rate limit exceeded",
                                       "type": "rate_limit_exceeded"}}, status_code=429)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    messages = body.get("messages") or []
    last = messages[-1] if messages else {}
    content = last.get("content") if isinstance(last, dict) else None
    # easter egg kept from the real worker: "Server?" -> identity ping
    if content == "Server?":
        return {"choices": [{"message": {"role": "assistant",
                                         "content": f"{server['label']} - Server ID: {server['id']}"}}]}
    # spam pattern kept from the real worker
    if isinstance(content, str) and "Ты — SEO-ассистент и генератор поисковых запросов." in content:
        return JSONResponse({"error": {"message": "Request blocked", "type": "blocked_content"}},
                            status_code=403)

    model = body.get("model") or (server.get("default_model")
                                  or (server.get("allowed_models") or [None])[0])
    key = random_api_key(server.get("api_keys"))
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    proxy_body = {**body, "model": model}
    stream = bool(body.get("stream"))
    if stream:
        proxy_body["stream_options"] = {"include_usage": True}
    url = f"{server['base_url']}/chat/completions"

    if not stream:
        async with httpx.AsyncClient(follow_redirects=True, timeout=PROXY_TIMEOUT) as client:
            try:
                r = await client.post(url, json=proxy_body, headers=headers)
            except Exception as e:
                return JSONResponse({"error": f"Upstream error: {e}"}, status_code=502)
        data = {}
        if r.headers.get("content-type", "").startswith("application/json"):
            try:
                data = r.json()
            except Exception:
                pass
        tokens = _usage_tokens(data) if isinstance(data, dict) else None
        _record_usage(server, user, model, tokens, server_id)
        if isinstance(data, dict) and data:
            data.setdefault("model", model)
            return JSONResponse(data, status_code=r.status_code,
                                headers={"X-Server": server_id,
                                         "X-Provider": server.get("label", ""),
                                         # full URL only for owner/admin (as in worker)
                                         **({"X-Url": url}
                                            if (user and user["id"] == server.get("owner_id")) else {})})
        return JSONResponse({"error": "Bad upstream response"}, status_code=502)

    # Streaming: the client must outlive the handler (FastAPI drains the
    # generator AFTER the handler returns), so the generator owns the
    # client's lifecycle and closes it in `finally`.
    client = httpx.AsyncClient(follow_redirects=True, timeout=PROXY_TIMEOUT)
    try:
        upstream = await client.send(
            client.build_request("POST", url, json=proxy_body, headers=headers),
            stream=True)
    except Exception as e:
        await client.aclose()
        return JSONResponse({"error": f"Upstream error: {e}"}, status_code=502)
    if upstream.status_code >= 400:
        text = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        return JSONResponse(
            {"error": f"Upstream {upstream.status_code}",
             "detail": text[:2000].decode("utf-8", "replace")},
            status_code=502)

    async def passthrough():
        tokens = None
        try:
            async for line in upstream.aiter_lines():
                if line.startswith("data: "):
                    payload = line[6:]
                    if payload != "[DONE]":
                        try:
                            chunk = json.loads(payload)
                            tokens = _usage_tokens(chunk) or tokens
                        except Exception:
                            pass
                if line:
                    yield f"{line}\n\n"
        finally:
            await upstream.aclose()
            await client.aclose()
            _record_usage(server, user, model, tokens or 0, server_id)

    return StreamingResponse(passthrough(), media_type="text/event-stream",
                             headers={"X-Server": server_id,
                                      "X-Provider": server.get("label", "")})


def _record_usage(server, user, model, tokens, server_id):
    try:
        server.setdefault("usage", {"requests": 0, "tokens": 0, "last_used": None})
        server["usage"]["requests"] += 1
        server["usage"]["tokens"] += int(tokens or 0)
        server["usage"]["last_used"] = now_iso()
        if user:
            for s in user["custom_servers"]:
                if s["id"] == server_id:
                    s["usage"] = server["usage"]
                    break
            store.save_users()
        store.bump_stat(server_id, model or "unknown")
    except Exception:
        pass


@app.get("/custom/{server_id}/status")
@app.post("/custom/{server_id}/validate")
async def server_status_validate(server_id: str, request: Request):
    user = authenticate(request.headers.get("authorization", "").removeprefix("Bearer "))
    server = store.get_server(server_id, user)
    if not server:
        return JSONResponse({"error": "Server not found"}, status_code=404)
    if request.method == "GET":
        entry = {k: v for k, v in server.items() if k != "api_keys"}
        return entry
    # validate: FIX #3 — owner may validate their PRIVATE servers too
    model = request.query_params.get("model") or server.get("default_model")
    async with httpx.AsyncClient(follow_redirects=True) as client:
        result = await validate_server(client, server.get("base_url", ""),
                                       server.get("api_keys", ""), model)
    entry = {k: v for k, v in server.items() if k != "api_keys"}
    entry["test_result"] = None if result["valid"] else result
    entry["is_valid"] = result["valid"]
    if result.get("base_url"):
        entry["base_url"] = result["base_url"]
    if not result["valid"]:
        entry["is_offline"] = True
    else:
        entry["is_offline"] = False
    return entry


# --------------------------- UI ---------------------------------------------
@app.get("/")
async def ui():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health():
    return {"ok": True, "servers": len(store.public_index)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)

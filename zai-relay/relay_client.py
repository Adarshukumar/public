#!/usr/bin/env python3
"""
Z.ai chat relay client — runs on a GitHub Actions runner (real internet egress).

Reads job inputs from environment:
  ZAI_PROMPT      (required) the user prompt
  ZAI_CHAT_ID     (optional) continue this chat instead of creating a new one
  ZAI_PARENT      (optional) previous assistant message id (multi-turn chain)
  ZAI_GUEST_ID    (optional) guest user id from a previous turn
  ZAI_GUEST_TOKEN (optional) guest JWT from a previous turn
  ZAI_WEB_SEARCH  "true"/"false"
  ZAI_THINKING    "true"/"false"

Does the exact chat.z.ai flow (reverse-engineered from HAR, signature 3/3-verified):
  1. guest bootstrap (GET /api/v1/auths/)          [skipped when a guest is supplied]
  2. new chat (POST /api/v1/chats/new, first message embedded)   [when ZAI_CHAT_ID empty]
  3. signed streaming completions (x-signature, 34-param URL)
  4. parses SSE phases thinking/tool_call/tool_response/answer/usage/done

Writes:
  out/result.json  {ok, chat_id, guest:{id,email,token}, assistant_id,
                    thinking, tools, toolres, usage, answer, error, http_status}
  out/stream.sse   raw captured SSE (when available)

Exit code 0 even on API errors (so the artifact is always uploaded);
the `ok`/`error` fields in result.json carry the outcome.
"""
import base64, hashlib, hmac, json, os, sys, time, uuid
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)

BASE = "https://chat.z.ai"
MODEL = "x-preview-l"
SIGN_KEY = "key-@@@@)))()((9))-xxxx&&&%%%%%"
FE_VERSION = "prod-fe-1.1.95"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/5336")


def log(msg):
    print(f"[relay] {msg}", flush=True)


def compute_signature(prompt, ts_ms, request_id, user_id):
    inner = hmac.new(SIGN_KEY.encode(), str(ts_ms // 300000).encode(),
                     hashlib.sha256).hexdigest()
    o = {"timestamp": str(ts_ms), "requestId": request_id, "user_id": user_id}
    sorted_payload = ",".join(f"{k},{o[k]}" for k in sorted(o))
    b64p = base64.b64encode(prompt.encode("utf-8")).decode()
    return hmac.new(inner.encode(), f"{sorted_payload}|{b64p}|{ts_ms}".encode(),
                    hashlib.sha256).hexdigest()


def main():
    from curl_cffi import requests as cr

    prompt = os.environ.get("ZAI_PROMPT", "").strip()
    chat_id = os.environ.get("ZAI_CHAT_ID", "").strip() or None
    parent = os.environ.get("ZAI_PARENT", "").strip() or None
    guest_id = os.environ.get("ZAI_GUEST_ID", "").strip() or None
    guest_token = os.environ.get("ZAI_GUEST_TOKEN", "").strip() or None
    web_search = os.environ.get("ZAI_WEB_SEARCH", "false") == "true"
    thinking = os.environ.get("ZAI_THINKING", "true") == "true"
    device_id = "uid_" + uuid.uuid4().hex[:16]

    result = {"ok": False, "chat_id": chat_id, "guest": None, "assistant_id": None,
              "thinking": "", "tools": [], "toolres": "", "usage": None,
              "answer": "", "error": None, "http_status": None}

    try:
        headers = {
            "user-agent": UA, "accept-language": "en-US", "origin": BASE,
            "referer": BASE + "/",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty", "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-device-id": device_id, "x-fe-version": FE_VERSION, "x-region": "overseas",
        }

        # ---------------- 1) guest ---------------- #
        if guest_id and guest_token:
            guest = {"id": guest_id, "token": guest_token,
                     "email": f"guest {guest_id[:8]}", "name": "Guest"}
            log(f"continuing with supplied guest {guest_id[:8]}…")
        else:
            r = cr.get(BASE + "/api/v1/auths/", headers={**headers, "accept": "application/json"},
                       impersonate="chrome124", timeout=30)
            result["http_status"] = r.status_code
            if r.status_code != 200:
                result["error"] = f"guest bootstrap HTTP {r.status_code}: {r.text[:300]}"
                raise RuntimeError(result["error"])
            guest = r.json()
            log(f"guest created: id={guest['id']} email={guest.get('email')}")
        result["guest"] = {k: guest.get(k) for k in ("id", "email", "token")}

        # ---------------- 2) chat ---------------- #
        assistant_id = None
        if not chat_id:
            ts = int(time.time())
            mid = str(uuid.uuid4())
            body = {"chat": {
                "id": "", "title": "New Chat", "models": [MODEL], "params": {},
                "history": {"messages": {mid: {"id": mid, "parentId": None, "childrenIds": [],
                                              "role": "user", "content": prompt,
                                              "timestamp": ts, "models": [MODEL]}},
                            "currentId": mid},
                "tags": [], "flags": [],
                "features": [{"server": "tool_selector_h", "status": "hidden",
                              "type": "tool_selector"}],
                "mcp_servers": ["advanced-search"] if web_search else [],
                "enable_thinking": thinking,
                "reasoning_effort": "max" if thinking else "low",
                "auto_web_search": web_search, "message_version": 1, "extra": {},
                "timestamp": int(time.time() * 1000), "type": "default",
            }}
            r = cr.post(BASE + "/api/v1/chats/new",
                        headers={**headers, "content-type": "application/json"},
                        json=body, impersonate="chrome124", timeout=30)
            result["http_status"] = r.status_code
            if r.status_code not in (200, 201):
                result["error"] = f"chat create HTTP {r.status_code}: {r.text[:300]}"
                raise RuntimeError(result["error"])
            chat_id = r.json()["id"]
            result["chat_id"] = chat_id
            log(f"NEW CHAT CREATED: chat_id={chat_id}")
        else:
            log(f"continuing chat {chat_id} (parent={parent})")

        # ---------------- 3) signed completions ---------------- #
        ts_ms = int(time.time() * 1000)
        request_id = str(uuid.uuid4())
        sig = compute_signature(prompt, ts_ms, request_id, guest["id"])
        P = {
            "timestamp": str(ts_ms), "requestId": request_id, "user_id": guest["id"],
            "version": "0.0.1", "platform": "web", "token": guest["token"],
            "user_agent": UA, "language": "en-US", "languages": "en-US,en",
            "timezone": "Asia/Calcutta", "cookie_enabled": "true",
            "screen_width": "1366", "screen_height": "768",
            "screen_resolution": "1366x768", "viewport_height": "712",
            "viewport_width": "955", "viewport_size": "955x712",
            "color_depth": "24", "pixel_ratio": "0.8999999761581421",
            "current_url": f"{BASE}/c/{chat_id}", "pathname": f"/c/{chat_id}",
            "host": "chat.z.ai", "hostname": "chat.z.ai", "protocol": "https:",
            "title": "Z.ai - Advanced AI Chatbot & Agent powered by GLM-5.3-Flash",
            "timezone_offset": "-330",
            "local_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "utc_time": time.strftime("%a, %d %b %Y %H GMT", time.gmtime()),
            "is_mobile": "false", "is_touch": "false", "max_touch_points": "0",
            "browser_name": "Chrome", "os_name": "Windows",
            "dnt": "false", "webdriver": "false", "signature_timestamp": str(ts_ms),
        }
        qs = "&".join(f"{k}={v}" for k, v in P.items())
        assistant_id = str(uuid.uuid4())
        body = {
            "stream": True, "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "signature_prompt": prompt, "params": {}, "extra": {},
            "features": {"image_generation": False, "web_search": web_search,
                         "auto_web_search": web_search, "preview_mode": True,
                         "flags": [], "vlm_tools_enable": False,
                         "vlm_web_search_enable": False, "vlm_website_mode": False,
                         "enable_thinking": thinking,
                         "reasoning_effort": "max" if thinking else "low"},
            "variables": {"{{USER_NAME}}": guest.get("name") or "Guest",
                          "{{USER_LOCATION}}": "Unknown",
                          "{{CURRENT_DATETIME}}": time.strftime("%Y-%m-%d %H:%M:%S"),
                          "{{CURRENT_DATE}}": time.strftime("%Y-%m-%d"),
                          "{{CURRENT_TIME}}": time.strftime("%H:%M:%S"),
                          "{{CURRENT_WEEKDAY}}": time.strftime("%A"),
                          "{{CURRENT_TIMEZONE}}": "Asia/Calcutta",
                          "{{USER_LANGUAGE}}": "en-US"},
            "chat_id": chat_id, "id": assistant_id,
            "current_user_message_id": str(uuid.uuid4()),
            "current_user_message_parent_id": parent,
            "background_tasks": {"title_generation": True, "tags_generation": True},
        }
        if web_search:
            body["mcp_servers"] = ["advanced-search"]
        log(f"sending signed completion for chat {chat_id} (search={web_search}, "
            f"thinking={thinking})…")

        raw = bytearray()
        r = cr.post(f"{BASE}/api/v2/chat/completions?{qs}",
                    headers={**headers, "content-type": "application/json",
                             "accept": "text/event-stream", "x-signature": sig},
                    json=body, impersonate="chrome124", timeout=(30, 600), stream=True)
        result["http_status"] = r.status_code
        if r.status_code != 200:
            text = r.read()
            result["error"] = f"completions HTTP {r.status_code}: {text[:400].decode(errors='replace')}"
            raise RuntimeError(result["error"])
        for line in r.iter_lines():
            ln = line.decode(errors="replace") if isinstance(line, bytes) else line
            raw += (ln + "\n").encode()
            if not ln.startswith("data:"):
                continue
            p = ln[5:].strip()
            if not p or p == "[DONE]":
                continue
            try:
                ev = json.loads(p)
            except json.JSONDecodeError:
                continue
            d = ev.get("data", {})
            if d.get("phase") == "thinking" and d.get("delta_content"):
                result["thinking"] += d["delta_content"]
            elif d.get("phase") == "tool_call" and d.get("delta_name"):
                result["tools"].append({"name": d["delta_name"], "args": ""})
            elif d.get("phase") == "tool_call" and d.get("delta_arguments") and result["tools"]:
                result["tools"][-1]["args"] += d["delta_arguments"]
            elif d.get("phase") == "tool_response" and d.get("delta_content"):
                result["toolres"] += d["delta_content"]
            elif d.get("phase") == "answer" and d.get("delta_content"):
                result["answer"] += d["delta_content"]
            elif d.get("phase") == "other" and d.get("usage"):
                result["usage"] = d["usage"]
            elif d.get("done"):
                break
        (OUT / "stream.sse").write_bytes(bytes(raw))
        result["assistant_id"] = assistant_id
        result["ok"] = True
        log(f"DONE: answer {len(result['answer'])} chars, "
            f"thinking {len(result['thinking'])} chars, "
            f"tools {len(result['tools'])}, usage {result['usage']}")

    except Exception as e:
        result["error"] = result.get("error") or f"{type(e).__name__}: {e}"
        log(f"ERROR: {result['error']}")
    finally:
        (OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
        log(f"result.json written (ok={result['ok']})")

    return 0


if __name__ == "__main__":
    sys.exit(main())

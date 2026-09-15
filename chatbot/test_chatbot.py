"""
test_chatbot.py — E2E tests for the G4F Model Router chatbot.

Spawns backend.py and verifies: health, full model catalog (all 48 models,
groupable families), streaming + non-streaming chat for several models,
distinct model voices, math/code detection, usage tracking, error handling,
and that the frontend is served.

Run:  python3 test_chatbot.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
RT = "http://127.0.0.1:8090"
PASSED = 0


def check(name, cond, extra=""):
    global PASSED
    if not cond:
        print(f"  ✗ FAIL {name} {extra}")
        sys.exit(1)
    PASSED += 1
    print(f"  ✓ {name}")


def wait_for(url, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except Exception:
            pass
        time.time()
        time.sleep(0.3)
    raise SystemExit(f"server did not come up: {url}")


def chat(model, text, stream=True):
    r = httpx.post(RT + "/api/chat", timeout=60, json={
        "model": model, "stream": stream,
        "messages": [{"role": "user", "content": text}]})
    return r


def main():
    (HERE / "usage.json").unlink(missing_ok=True)
    p = subprocess.Popen([sys.executable, str(HERE / "backend.py")],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(RT + "/api/health")

        print("\n[1] health + mode")
        h = httpx.get(RT + "/api/health").json()
        check("health ok", h["ok"] is True)
        check("48 models registered", h["models"] == 48, str(h["models"]))
        check("mode reported", h["mode"].startswith("simulated") or h["mode"].startswith("proxy"))

        print("\n[2] model catalog")
        models = httpx.get(RT + "/api/models").json()["models"]
        check("catalog has 48 entries", len(models) == 48, str(len(models)))
        fams = {m["family"] for m in models}
        check("multiple families (20)", len(fams) == 20, str(len(fams)))
        for req in ("openai-z/gpt-5.6-luna", "zai-z/zai-org-glm-5-3-flash",
                    "logfare/kimi-k3", "xai-z/grok-4-fast-non-reasoning",
                    "poolside/laguna-xs-2.1", "venice-z/venice-uncensored-role-play"):
            check(f"cakey's model present: {req}",
                  any(m["id"] == req for m in models))
        check("catalog carries usage fields",
              all("requests" in m and "tokens" in m and "tags" in m and "context" in m for m in models))

        print("\n[3] streaming chat (SSE) with Luna")
        with httpx.stream("POST", RT + "/api/chat", timeout=60, json={
            "model": "openai-z/gpt-5.6-luna", "stream": True,
            "messages": [{"role": "user", "content": "hello"}]}) as r:
            check("SSE content-type", r.headers.get("content-type", "").startswith("text/event-stream"))
            chunks, done = 0, False
            for line in r.iter_lines():
                if line.startswith("data: "):
                    if line[6:] == "[DONE]":
                        done = True
                    else:
                        chunks += 1
        check("got chunks", chunks > 3, str(chunks))
        check("got [DONE]", done)

        print("\n[4] non-streaming chat + distinct voices")
        replies = {}
        for model, text in [
            ("zai-z/zai-org-glm-5-3-flash", "what is recursion?"),
            ("xai-z/grok-4-fast-non-reasoning", "what is recursion?"),
            ("poolside/laguna-xs-2.1", "write python code for a prime checker"),
            ("venice-z/venice-uncensored-role-play", "you are a noir detective"),
        ]:
            r = chat(model, text, stream=False)
            check(f"{model.split('/')[1]} -> 200", r.status_code == 200, r.text[:120])
            d = r.json()
            replies[model] = d["choices"][0]["message"]["content"]
            check(f"{model.split('/')[1]} has usage", d.get("usage", {}).get("total_tokens", 0) > 0)
        check("voices differ (GLM vs Grok)", replies["zai-z/zai-org-glm-5-3-flash"] != replies["xai-z/grok-4-fast-non-reasoning"])
        check("coder model returns a code block", "```" in replies["poolside/laguna-xs-2.1"])
        check("roleplay model leans in", "*" in replies["venice-z/venice-uncensored-role-play"])

        print("\n[5] capability detection")
        d = chat("logfare/kimi-k3", "what's 27 × 43?", stream=False).json()
        check("math answered (1161)", "1161" in d["choices"][0]["message"]["content"])
        d = chat("groq-z/gpt-oss-120b", "give me a python function", stream=False).json()
        check("code detected -> fenced code", "```python" in d["choices"][0]["message"]["content"])

        print("\n[6] usage tracking")
        models2 = httpx.get(RT + "/api/models").json()["models"]
        luna = next(m for m in models2 if m["id"] == "openai-z/gpt-5.6-luna")
        check("luna counted (>=1 req)", luna["requests"] >= 1, str(luna["requests"]))
        check("luna tokens > 0", luna["tokens"] > 0, str(luna["tokens"]))

        print("\n[7] error handling")
        check("unknown model -> 400", chat("nope/none", "hi", stream=False).status_code == 400)
        r = httpx.post(RT + "/api/chat", content=b"{{bad")
        check("malformed JSON -> 400", r.status_code == 400)

        print("\n[8] frontend served by the same server")
        r = httpx.get(RT + "/")
        check("index.html served", r.status_code == 200 and "G4F Model Router" in r.text)
        check("app.js served", httpx.get(RT + "/static/app.js").status_code == 200)
        check("style.css served", httpx.get(RT + "/static/style.css").status_code == 200)

        print(f"\nALL {PASSED} CHECKS PASSED ✅")
    finally:
        p.terminate()
        p.wait(timeout=5)


if __name__ == "__main__":
    main()

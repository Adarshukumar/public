"""
test_e2e.py — end-to-end tests for the custom-server reference implementation.

Spawns the mock upstream + the router, then exercises the whole g4f.dev
flow: create/validate server -> public index -> models -> chat
(non-stream + stream) -> usage tracking -> update/delete -> "Server?"
easter egg -> auth failures.

Run:  ../../.venv/bin/python test_e2e.py   (from this directory)
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
PY = sys.executable
UP = "http://127.0.0.1:9101"
RT = "http://127.0.0.1:8090"
AUTH = {"Authorization": "Bearer g4f_demo_session_token"}

PASSED = 0


def check(name, cond, extra=""):
    global PASSED
    if not cond:
        print(f"  ✗ FAIL {name} {extra}")
        sys.exit(1)
    PASSED += 1
    print(f"  ✓ {name}")


def wait_for(url, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except Exception:
            pass
        time.sleep(0.3)
    raise SystemExit(f"server did not come up: {url}")


def main():
    # fresh state
    for f in ("servers.json", "public_index.json", "usage_stats.json"):
        (HERE / f).unlink(missing_ok=True)

    p_up = subprocess.Popen([PY, str(HERE / "mock_upstream.py")])
    p_rt = subprocess.Popen([PY, str(HERE / "server.py")])
    try:
        wait_for(UP + "/api/v1/models")
        wait_for(RT + "/health")

        print("\n[1] server CRUD")
        r = httpx.post(RT + "/custom/api/servers/create", headers=AUTH, json={
            "label": "custom server by cakey(Luna, GLM 5.3 Flash, Kimi K3, etc)",
            "base_url": UP + "/api/v1",
            "api_keys": "osaii-demo-key-123\nosaii-demo-key-456\n# comment line",
            "is_public": True,
            "default_model": "openai-z/gpt-5.6-luna",
        }, timeout=30)
        check("create returns 200", r.status_code == 200, r.text[:200])
        srv = r.json()["server"]
        check("server id has srv_ prefix + ts/hex", srv["id"].startswith("srv_") and len(srv["id"]) == 24, srv["id"])
        check("models auto-discovered", "openai-z/gpt-5.6-luna" in srv["allowed_models"], str(srv["allowed_models"]))
        check("api_keys never in response", "api_keys" not in srv and srv.get("api_key_count") == 2)
        check("X-Server header set", r.headers.get("X-Server") == srv["id"])
        sid = srv["id"]

        print("\n[2] public index (security: no keys at rest)")
        r = httpx.get(RT + "/custom/api/servers/public")
        pub = [s for s in r.json()["servers"] if s["id"] == sid][0]
        check("server listed publicly", pub["is_public"] is True)
        check("public response has no api_keys", "api_keys" not in pub)
        raw_index = (HERE / "public_index.json").read_text()
        check("public index file has NO api keys (fix #5)", "osaii-demo-key" not in raw_index)

        print("\n[3] models endpoint")
        r = httpx.get(RT + f"/custom/{sid}/models", headers=AUTH)
        data = r.json()["data"]
        check("models listed", len(data) == 6, str(len(data)))
        check("sorted by usage desc", data[0]["requests"] >= data[-1]["requests"])
        check("X-Server header", r.headers.get("X-Server") == sid)

        print("\n[4] chat completions (non-stream)")
        r = httpx.post(RT + f"/custom/{sid}/chat/completions", headers=AUTH, json={
            "model": "zai-z/zai-org-glm-5-3-flash",
            "messages": [{"role": "user", "content": "hello from e2e"}],
            "stream": False,
        }, timeout=30)
        check("completion 200", r.status_code == 200, r.text[:200])
        body = r.json()
        check("content from upstream mock", "mock upstream" in body["choices"][0]["message"]["content"])
        check("usage reported", body.get("usage", {}).get("total_tokens", 0) > 0)
        check("X-Provider header", r.headers.get("X-Provider", "").startswith("custom server by cakey"))
        check("owner sees full X-Url", "9101" in r.headers.get("X-Url", ""), str(r.headers.get("X-Url")))
        r_anon = httpx.post(RT + f"/custom/{sid}/chat/completions", json={
            "model": "zai-z/zai-org-glm-5-3-flash",
            "messages": [{"role": "user", "content": "anon ping"}], "stream": False}, timeout=30)
        check("anon X-Url hides the upstream host", "9101" not in r_anon.headers.get("X-Url", ""),
              str(r_anon.headers.get("X-Url")))

        print("\n[5] chat completions (stream SSE + usage capture)")
        chunks = []
        with httpx.stream("POST", RT + f"/custom/{sid}/chat/completions", headers=AUTH, json={
            "model": "logfare/kimi-k3",
            "messages": [{"role": "user", "content": "stream test"}],
            "stream": True,
        }, timeout=30) as r:
            check("SSE content-type", r.headers.get("content-type", "").startswith("text/event-stream"))
            for line in r.iter_lines():
                if line.startswith("data: "):
                    chunks.append(line[6:])
        check("got [DONE]", "[DONE]" in chunks)
        usage_chunks = [json.loads(c) for c in chunks if c != "[DONE]" and "usage" in json.loads(c)]
        check("final chunk carries usage (stream_options)", len(usage_chunks) == 1 and usage_chunks[0]["usage"]["total_tokens"] > 0)

        print("\n[6] usage tracking")
        r = httpx.get(RT + "/custom/api/servers/usage", headers=AUTH, params={"server_id": sid})
        usage = r.json()["total_usage"]
        check("requests counted (>=3)", usage["requests"] >= 3, str(usage))
        check("tokens counted", usage["tokens"] > 0, str(usage))
        per = r.json()["per_model"]
        check("per-model stats", per.get("zai-z/zai-org-glm-5-3-flash", 0) >= 1 and per.get("logfare/kimi-k3", 0) >= 1, str(per))

        print("\n[7] easter egg + auth")
        r = httpx.post(RT + f"/custom/{sid}/chat/completions", headers=AUTH, json={
            "messages": [{"role": "user", "content": "Server?"}], "stream": False})
        check("'Server?' returns identity", f"Server ID: {sid}" in r.json()["choices"][0]["message"]["content"])
        r = httpx.post(RT + f"/custom/{sid}/chat/completions", json={
            "model": "x", "messages": [{"role": "user", "content": "hi"}]})
        check("unauthenticated chat still proxied for public servers (g4f behavior)", r.status_code == 200)
        r = httpx.get(RT + "/custom/api/servers")
        check("unauthenticated private list -> 401", r.status_code == 401)

        print("\n[8] update (fix #1: no base_url in payload must not crash)")
        r = httpx.post(RT + "/custom/api/servers/update", headers=AUTH, json={
            "server_id": sid, "label": "cakey v2"})
        check("label-only update 200 (was 500 in original)", r.status_code == 200, r.text[:200])
        check("label updated", r.json()["server"]["label"] == "cakey v2")

        print("\n[9] validate (fix #3: private servers work for owner)")
        r = httpx.post(RT + f"/custom/{sid}/validate", headers=AUTH, json={})
        check("owner validate 200", r.status_code == 200, r.text[:200])
        check("validate says valid", r.json()["is_valid"] is True)

        print("\n[10] delete (fix #2: bodyless DELETE)")
        r = httpx.request("DELETE", RT + "/custom/api/servers/delete?server_id=" + sid, headers=AUTH)
        check("bodyless DELETE 200 (was 500 in original)", r.status_code == 200, r.text[:200])
        r = httpx.get(RT + "/custom/api/servers/public")
        check("removed from public index", all(s["id"] != sid for s in r.json()["servers"]))

        print("\n[11] robustness")
        r = httpx.post(RT + "/custom/api/servers/create", headers=AUTH, content=b"{broken json")
        check("malformed JSON -> 400 not 500 (fix #10)", r.status_code == 400, str(r.status_code))

        print(f"\nALL {PASSED} CHECKS PASSED ✅")
    finally:
        p_rt.terminate()
        p_up.terminate()
        p_rt.wait(timeout=5)
        p_up.wait(timeout=5)


if __name__ == "__main__":
    main()

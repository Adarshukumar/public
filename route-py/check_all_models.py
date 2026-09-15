"""
check_all_models.py — live sweep of ALL models: does each one work, and does it REASON?

For every model in the catalog this script:
  1. sends ONE plain user message — NO system prompt, the model is used
     directly — and checks the reply (status + presence of a reasoning
     trace in `reasoning_content`);
  2. for every flagged thinking model, re-asks via streaming and verifies
     the two-phase order (reasoning deltas first, then answer deltas).

Run:  python3 check_all_models.py     (server must be up on port 8090)
"""
import json
import sys

import httpx

RT = "http://127.0.0.1:8090"
# one plain question, identical for every model, no system prompt
PROMPT = ("A bat and a ball cost $1.10 in total. The bat costs $1.00 more "
          "than the ball. How much does the ball cost?")


def ask(model_id: str, stream: bool):
    return httpx.post(RT + "/api/chat", timeout=60, json={
        "model": model_id,
        "stream": stream,
        "messages": [{"role": "user", "content": PROMPT}],  # direct, no system prompt
    })


def stream_order(model_id: str):
    """returns the phase order actually observed on the wire, e.g. ['t','t','a',...]"""
    order = []
    with httpx.stream("POST", RT + "/api/chat", timeout=60, json={
            "model": model_id, "stream": True,
            "messages": [{"role": "user", "content": PROMPT}]}) as r:
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            delta = json.loads(payload).get("choices", [{}])[0].get("delta", {})
            if delta.get("reasoning_content"):
                order.append("t")
            elif delta.get("content"):
                order.append("a")
    return order


def main():
    d = httpx.get(RT + "/api/models", timeout=10).json()
    models = d["models"]
    print(f"sweeping {len(models)} models — one plain user message each, no system prompt\n")

    rows = []
    for m in models:
        r = ask(m["id"], stream=False)
        if r.status_code != 200:
            rows.append((m, f"HTTP {r.status_code}", False, 0))
            continue
        msg = r.json()["choices"][0]["message"]
        rc = msg.get("reasoning_content", "")
        rows.append((m, "ok", bool(rc), len(rc)))

    # streaming two-phase verification for every thinking model
    stream_ok, stream_bad = {}, {}
    for m in models:
        if not m.get("thinking"):
            continue
        order = stream_order(m["id"])
        good = (order and order[0] == "t" and "a" in order
                and all(x == "t" for x in order[:order.index("a")]))
        (stream_ok if good else stream_bad)[m["id"]] = order

    w = max(len(m["name"]) for m in models)
    print(f"{'MODEL':<{w}}  {'FAMILY':<13} {'STATUS':<9} {'REASONS':<7} {'STREAM PHASES'}")
    print("-" * (w + 42))
    ok_count = think_count = 0
    for (m, st, has, ln) in rows:
        ok_count += (st == "ok")
        think_count += has
        if m["id"] in stream_ok:
            o = stream_ok[m["id"]]
            so = f"✓ {len([x for x in o if x=='t'])} think → {len([x for x in o if x=='a'])} answer"
        elif m["id"] in stream_bad:
            so = "✗ BAD ORDER " + "".join(stream_bad[m["id"]][:10])
        else:
            so = "— (no thinking claimed)"
        print(f"{m['name']:<{w}}  {m['family']:<13} {st:<9} {'YES' if has else 'no':<7} {so}")
    print("-" * (w + 42))
    flagged = d["thinking_models"]
    print(f"working:      {ok_count}/{len(rows)}")
    print(f"reasoning:    {think_count} models returned a reasoning trace (catalog flagged {flagged})")
    fails = [(m["id"], st) for (m, st, _, _) in rows if st != "ok"]
    if fails or think_count != flagged or stream_bad:
        print("FAILURES:", fails, stream_bad)
        sys.exit(1)
    print("ALL MODELS WORKING — reasoning exactly where flagged ✅")


if __name__ == "__main__":
    main()

# 04 — Reasoning / "Thinking" Models: Which of the 48 Actually Think, and How the Thinking Is Extracted

*Deep-dive companion to the chatbot (`route-py/` + `route-web/`), 2026-09-15.*

Question answered: **of the 48 models on cakey's relay, which ones are real
reasoning models, and what is the "extraction process" — how does a client
actually get the model's thinking out of the API?**

Short answer: **17 of the 48 are thinking models**, and the industry settled on
two extraction patterns:

1. **Visible chain in a side field** — the model's raw thinking stream comes
   back in `reasoning_content` (message field / stream delta), separate from
   `content`. Used by DeepSeek, Qwen, Z.ai GLM, Kimi (Moonshot).
2. **Opaque thinking + optional summary/encrypted blob** — thinking happens
   internally; you get at most a human-readable summary and/or an encrypted
   token you echo back for multi-turn coherence. Used by OpenAI (GPT-5/o-line)
   and xAI (Grok 4.3+).

---

## 1. Per-vendor extraction process (the actual wire formats)

### DeepSeek (R1 / V3.1 / V3.2 → our `deepseek-z` + `logfare/deepseek-v4-*`)
- **R1** (`deepseek-reasoner`): RL-trained to emit an explicit thinking trace.
  The API returns **two fields**: `message.reasoning_content` (the CoT) and
  `message.content` (the answer). Streaming deltas carry `reasoning_content`
  first, then `content`. Thinking tokens are billed inside
  `completion_tokens`; `temperature=1` is required.
- **V3.1** (Aug 2025): *hybrid* — one model, two chat-template modes
  (think / non-think), exposed as `deepseek-reasoner` vs `deepseek-chat`.
- **V3.2** (Dec 2025): *unified* — auto-routes between fast answer and deep
  reasoning inside one model, ~97–98% of R1 reasoning quality. **V4 (our
  catalog) inherits this unified behavior** → reasoning is available,
  auto-engaged.

### Qwen (Alibaba) — our `qwen-z`
- **Hybrid thinking**: per-request toggle `enable_thinking: true/false`, plus
  `thinking_budget` (token cap) and `reasoning_effort`
  (low/medium/xhigh). **`qwen3.7-plus` has thinking ON by default.**
- Extraction: same two-phase stream — `delta.reasoning_content` (phase 1),
  then `delta.content` (phase 2). Official docs: *"If the response returns
  the reasoning_content field, thinking mode is active."* Turning thinking off
  cuts latency 60–75%.
- Multi-turn: include the assistant's `reasoning_content` when sending history
  or accuracy degrades.

### Z.ai / GLM — our `zai-z` (the most aggressive of the four)
Per Z.ai's docs (June 2026):
- **GLM-5.3 / GLM-5.3-FLASH: thinking is FORCED — it cannot be disabled.**
  So `zai-z/zai-org-glm-5-3-flash` in our catalog is unconditionally a
  thinking model.
- GLM-5.2/5.1/5.0, GLM-4.7: thinking by default (4.7: forced); **GLM-4.6:
  hybrid** (auto-decides per turn).
- **GLM-4.7's three thinking modes**: *Interleaved Thinking* (thinks before
  every response **and every tool call**), *Preserved Thinking* (keeps thinking
  blocks across turns), *Turn-level Thinking* (on/off per request via
  `thinking: {type: "enabled"|"disabled", clear_thinking: bool}`).
  `reasoning_effort` accepted too.
- Extraction: `message.reasoning_content` + `delta.reasoning_content`,
  identical to the DeepSeek/Qwen pattern. Docs warn: **return the historical
  `reasoning_content` to keep reasoning coherent** across turns.

### Kimi / Moonshot — our `logfare/kimi-k3`
- **Kimi K3: "reasoning is always on"** — always returns `reasoning_content`,
  with effort levels low/high/max (default max). Multi-turn requires sending
  back the full assistant message *including* reasoning ("preserved thinking
  history").
- K2.x line: thinking by default, `thinking: {type, keep: "all"}` for
  preservation; streaming = `delta.reasoning_content` then `delta.content`.

### OpenAI — our `openai-z`
- GPT-5/o-line: thinking is **internal and opaque**. Request-side:
  `reasoning: {effort: "low"|"medium"|"high"}`.
- Extraction: you do **not** get the raw CoT. What you get:
  - a **reasoning summary** (short human-readable plan; streamed as
    `response.reasoning_summary_text.delta` in the Responses API),
  - optionally an **encrypted reasoning blob**
    (`include: ["reasoning.encrypted_content"]`) which you must echo back on
    subsequent turns to preserve the model's private context.
- Practical consequence for the chatbot: GPT-5-style "thinking" is a summary,
  not a trace.

### xAI / Grok — our `xai-z`
- **Grok 4 (original)**: reasoning-only model, but **no reasoning trace is
  exposed** and no effort parameter — final answer only.
- **Grok 4.3+ / 4.5 / 4.6**: `reasoning: {effort: "low"|"medium"|"high"|"xhigh"}`;
  thinking streams via `response.reasoning_text.delta` (full) or
  `response.reasoning_summary_text.delta` (summary). Cannot be fully disabled.
- **`grok-4-1-fast` does not accept the reasoning parameter** (xAI returns
  HTTP 400 for it) → our two xai entries are both non-thinking tiers, which
  matches their names.

### Google Gemini — our `gemini-z`
- Gemini 2.5: `thinkingBudget` (0 = off, −1 = dynamic, N = exact tokens) +
  `include_thoughts` → returns **thought summaries** (not full CoT).
- Gemini 3: `thinkingLevel` (minimal/low/medium/high).
- **All our gemini-z entries are flash-**lite** tiers, where thinking is
  disabled by default** → non-thinking in practice.

### GPT-OSS (open weights) — our `groq-z`
- `gpt-oss-120b` (and 20b, weaker) **expose their raw reasoning stream** —
  OpenAI-compatible servers (Ollama et al.) emit it as a `reasoning` delta
  field alongside `content`. Visible CoT, like R1, but shorter.

---

## 2. Roster — which of our 48 are thinking models

| # | Model (catalog id) | Real-world basis | Thinking type | Trace exposed? |
|---|---|---|---|---|
| 1 | `openai-z/gpt-5.6-luna` | GPT-5 line | always (opaque) | **summary only** + encrypted blob |
| 2 | `zai-z/zai-org-glm-5-3-flash` | GLM-5.3-Flash | **forced, cannot disable** | ✅ full CoT (`reasoning_content`) |
| 3 | `zai-z/zai-org-glm-4.7-flash` | GLM-4.7 | forced + interleaved/preserved/turn-level | ✅ full CoT |
| 4 | `zai-z/olafangensan-glm-4.7-flash-heretic` | GLM-4.7 fine-tune | inherited from 4.7 | ✅ full CoT |
| 5 | `zai-z/zai-org-glm-4.6` | GLM-4.6 | hybrid (auto per turn) | ✅ full CoT |
| 6 | `logfare/kimi-k3` | Kimi K3 | **always on** (effort low/high/max) | ✅ full CoT |
| 7 | `logfare/deepseek-v4-flash` | DeepSeek V line | unified auto-routing | ✅ full CoT |
| 8 | `logfare/deepseek-v4-pro` | DeepSeek V line | unified auto-routing | ✅ full CoT |
| 9 | `deepseek-z/deepseek-v4-flash` | DeepSeek V line | unified auto-routing | ✅ full CoT |
| 10 | `deepseek-z/deepseek-v4-pro` | DeepSeek V line | unified auto-routing | ✅ full CoT |
| 11 | `qwen-z/qwen3.8-flash` | Qwen3.8 | hybrid, `enable_thinking` | ✅ full CoT |
| 12 | `qwen-z/qwen3.6-flash` | Qwen3.6 | hybrid, `enable_thinking` | ✅ full CoT |
| 13 | `qwen-z/qwen3-coder-flash` | Qwen3 Coder | hybrid | ✅ full CoT |
| 14 | `qwen-z/qwen3.7-plus` | Qwen3.7 | **on by default** | ✅ full CoT |
| 15 | `openrouter-z/qwen3.8-27b` | Qwen3.8 27B (via OpenRouter) | hybrid, `enable_thinking` | ✅ full CoT |
| 16 | `groq-z/gpt-oss-120b` | GPT-OSS-120B | medium (open weights) | ✅ raw reasoning stream |
| 17 | `groq-z/gpt-oss-20b` | GPT-OSS-20B | low | ✅ raw reasoning stream |

**Not thinking:** `xai-z/*` (both are the non-reasoning/fast tiers; Grok 4.1
fast rejects the reasoning param), `gemini-z/*` (flash-lite, thinking off by
default), `openai-z/gpt-5.4-nano` + GPT-4.x nano/mini (small fast models),
`poolside/laguna-*` (agentic coders — they act, they don't narrate CoT), and
the rest (venice-z, meta-z, microsoft bitnet, osaii, inception, stealth,
mimo-z, minimax-z, mistral-z, fireworks-z, openrouter-z, qwen-flash-character).

That's **17/48 thinking** — the chatbot flags them with a 🧠 badge and streams
their thinking in a collapsible "🧠 thinking…" box before the answer.

---

## 3. What this means for g4f.dev (and for our chatbot)

- **g4f.dev's custom-server proxy is a raw pass-through** (see
  `sources/api-worker.js` in this folder): it forwards
  `POST /chat/completions` to `server.base_url` with the owner's key and
  streams the upstream body back untouched. So **any `reasoning_content`
  field from the upstream flows through g4f unchanged** — a client that
  understands the field sees the thinking; one that doesn't just ignores the
  extra JSON key. That's why no special "reasoning support" was ever added to
  the custom-server feature: pass-through is the support.
- **Our chatbot does the same in PROXY mode**: the SSE passthrough forwards
  upstream `reasoning_content` deltas verbatim, and the request body can carry
  the provider knobs (`enable_thinking`, `thinking_budget`,
  `thinking: {type, keep}`, `reasoning: {effort}`, `max_tokens`) which the
  backend merges into the upstream request. Point it at a real DeepSeek /
  Qwen / GLM / Kimi endpoint and the real model's actual thinking trace
  streams into the 🧠 box — no extra code.
- **In SIMULATED mode** the 17 thinking models emit a deterministic,
  family-flavored trace in the same field the real vendors use
  (`reasoning_content`), streamed in the same two-phase order (thinking
  deltas first, then answer deltas, then usage + `[DONE]`), with
  `usage.completion_tokens_details.reasoning_tokens` set — i.e. the UI you see
  is exactly the shape real reasoning responses have.

### Wire format the chatbot speaks (both modes)

```
streaming (SSE), thinking model:
data: {"choices":[{"delta":{"role":"assistant","reasoning_content":"Step 1 — …"}}]}   ← phase 1
data: {"choices":[{"delta":{"reasoning_content":"Step 2 — …"}}]}
data: {"choices":[{"delta":{"content":"The answer is …"}}]}                          ← phase 2
data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{…,"completion_tokens_details":{"reasoning_tokens":96}}}
data: [DONE]

non-streaming:
{"choices":[{"message":{"role":"assistant",
                        "reasoning_content":"Step 1 — …\nStep 2 — …",
                        "content":"The answer is …"},"finish_reason":"stop"}],
 "usage":{…}}
```

## Sources

- Z.ai — Thinking Mode / Deep Thinking docs (docs.z.ai, June 2026): GLM-5.3 forced thinking, 4.7 interleaved/preserved/turn-level, `reasoning_content`, `reasoning_effort`.
- Moonshot/Kimi — Thinking Models guide (platform.kimi.ai, Sept 2026) + Kimi K3 open-weights note (innFactory AI model database, Aug 2026): "reasoning is always on", effort levels, preserved thinking history.
- Alibaba/Qwen — Deep thinking docs (alibabacloud.com, June 2026) + QwenCloud thinking guide (docs.qwencloud.com): `enable_thinking`, `thinking_budget`, `reasoning_effort`, qwen3.7-plus default-on, 60–75% latency saving, multi-turn `reasoning_content` requirement.
- DeepSeek — V3.1 hybrid design (ai-tldr.dev, runpod.io), V3.2 unified reasoning (deepseak.org, bentoml.com), R1 `reasoning_content` API shape (sitepoint.com R1 guide, 2026).
- OpenAI — GPT-5.x reasoning: `reasoning.effort`, encrypted reasoning content, reasoning-summary streaming (LiteLLM issue #24570, JetBrains koog #1264, Vercel AI Gateway docs, Sept 2026).
- xAI — Reasoning docs (docs.x.ai, Aug 2026): effort levels incl. xhigh, summary/text deltas; Grok 4 no-trace behavior (datacamp.com tutorial, July 2025); grok-4-1-fast rejecting `reasoningEffort` (hermes-agent issue #23088, May 2026).
- Google — Gemini thinking docs (ai.google.dev): `thinkingBudget` 2.5, `thinkingLevel` 3, `include_thoughts`, flash-lite default-off (help.apiyi.com parameter-evolution table, Jan 2026).
- GPT-OSS — visible `reasoning` stream field (huggingface.co/openai/gpt-oss-20b discussion #28, Aug 2025).
- g4f.dev pass-through — local source: `research/sources/workers/api-worker.js` (proxy = raw fetch forward, extra fields untouched).

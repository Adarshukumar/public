# public

Research + fixes + a working re-implementation of the **g4f.dev "custom server"** feature
(the thing behind `https://g4f.dev/chat/#custom:srv_mtsj8uzo97d3c0d49960` —
"custom server by cakey(Luna, GLM 5.3 Flash, Kimi K3, etc)").

## Start here

👉 **[`g4f-custom-server-deep-dive/README.md`](g4f-custom-server-deep-dive/README.md)**

| | |
|---|---|
| [`01-ARCHITECTURE.md`](g4f-custom-server-deep-dive/01-ARCHITECTURE.md) | Full end-to-end explanation (browser → Cloudflare Worker → owner's upstream) with file/line refs |
| [`02-CAKEY-LIVE-DATA.md`](g4f-custom-server-deep-dive/02-CAKEY-LIVE-DATA.md) | Cakey's server verified against the live production API |
| [`03-BUGS-AND-IMPROVEMENTS.md`](g4f-custom-server-deep-dive/03-BUGS-AND-IMPROVEMENTS.md) | 9 real bugs found in the production code (2 crashes + 1 security issue) |
| [`fixes/g4f-dev-custom-server-fixes.patch`](g4f-custom-server-deep-dive/fixes/g4f-dev-custom-server-fixes.patch) | Ready-to-apply patch for `gpt4free/g4f.dev` (verified to apply cleanly upstream) |
| [`reference-impl/`](g4f-custom-server-deep-dive/reference-impl/) | Runnable Python/FastAPI re-implementation + 32-check E2E suite + demo chat UI |

# zai-relay

GitHub-Actions relay for chat.z.ai (sealed-sandbox egress).

- `relay_client.py` — runs on the GitHub runner: guest bootstrap → new chat
  (`POST /api/v1/chats/new`) → signed SSE completions (x-signature) → result.json
- `inbox/<id>.json` — job file pushed by the sandbox chat server (port 8092);
  pushing here triggers `.github/workflows/zai-relay.yml`
- result is returned as a private Actions artifact `zai-result-<id>`

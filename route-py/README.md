# route-py — all Python code

The chatbot **backend** (`backend.py`) — FastAPI, one port (8090), serves the
API **and** the frontend (`../route-web/`). Plus the test suites.

```bash
python3 -m pip install -r requirements.txt
./run.sh                  # → http://localhost:8090
python3 test_chatbot.py   # 49 checks
python3 check_all_models.py  # sweep all 48 models (server running)
```

Full documentation: [`../README.md`](../README.md) · Reasoning research: [`../research/04-REASONING-MODELS.md`](../research/04-REASONING-MODELS.md)

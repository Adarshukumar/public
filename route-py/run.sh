#!/usr/bin/env bash
# Run the G4F Model Router chatbot: backend API + frontend on ONE web port (8090).
# Optionally proxy to a REAL OpenAI-compatible upstream:
#   UPSTREAM_BASE_URL=https://your.api/v1 UPSTREAM_API_KEY=sk-... ./run.sh
set -e
cd "$(dirname "$0")"
exec python3 backend.py

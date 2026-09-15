#!/usr/bin/env bash
# Run the g4f.dev custom-server reference implementation:
#   - mock upstream  on http://127.0.0.1:9101  (fake "osaii" relay)
#   - router + UI    on http://0.0.0.0:8090
# Usage: ./run.sh
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
$PY mock_upstream.py &
UP_PID=$!
trap 'kill $UP_PID 2>/dev/null || true' EXIT
$PY server.py

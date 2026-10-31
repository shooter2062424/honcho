#!/bin/bash
# usage: start_provider.sh <latency_ms>
cd /home/user/honcho
source /home/user/poc/honcho_env.sh
[ -f /home/user/poc/provider.pid ] && kill "$(cat /home/user/poc/provider.pid)" 2>/dev/null
sleep 1
PYTHONPATH=/home/user/honcho LLM_LATENCY_MS=${1:-0} nohup uv run uvicorn --app-dir /home/user/poc provider:app --port 9100 --log-level warning > /home/user/poc/provider.log 2>&1 &
echo $! > /home/user/poc/provider.pid
sleep 5
curl -s -m 3 localhost:9100/health
echo

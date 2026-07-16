#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h:h}"
PYTHON="$ROOT/.venv/bin/python"
GH="/opt/homebrew/bin/gh"
NODE="/Users/llss-mac/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
LOCALTUNNEL="/Users/llss-mac/.local/share/dsa-online-tunnel/node_modules/.bin/lt"
KEY_FILE="$ROOT/data/.online_gateway_key"
GIST_ID_FILE="$ROOT/data/.online_gateway_gist_id"
ORIGIN_FILE="$ROOT/data/.online_gateway_origin"
GATEWAY_LOG="$ROOT/logs/online_gateway.log"
TUNNEL_LOG="$ROOT/logs/online_tunnel.log"
WEB_LOG="$ROOT/logs/online_web.log"
SCHEDULER_LOG="$ROOT/logs/online_scheduler.log"
GATEWAY_PORT=18010

mkdir -p "$ROOT/data" "$ROOT/logs"
if [[ ! -s "$KEY_FILE" ]]; then
  umask 077
  openssl rand -hex 32 > "$KEY_FILE"
fi
chmod 600 "$KEY_FILE"

started_web_pid=""
started_scheduler_pid=""
gateway_pid=""
tunnel_pid=""

cleanup() {
  for pid in "$tunnel_pid" "$gateway_pid" "$started_scheduler_pid" "$started_web_pid"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT
trap 'exit 0' INT TERM HUP

[[ -x "$NODE" ]] || { echo "Node runtime not found: $NODE" >&2; exit 1; }
[[ -x "$LOCALTUNNEL" ]] || { echo "LocalTunnel client not found: $LOCALTUNNEL" >&2; exit 1; }

if ! curl -fsS --max-time 2 http://127.0.0.1:8010/api/health >/dev/null 2>&1; then
  cd "$ROOT"
  "$PYTHON" main.py --serve-only --host 127.0.0.1 --port 8010 >> "$WEB_LOG" 2>&1 &
  started_web_pid=$!
fi

if ! pgrep -f "main.py --schedule --no-run-immediately" >/dev/null 2>&1; then
  cd "$ROOT"
  WEBUI_ENABLED=false "$PYTHON" main.py --schedule --no-run-immediately >> "$SCHEDULER_LOG" 2>&1 &
  started_scheduler_pid=$!
fi

for _ in {1..90}; do
  curl -fsS --max-time 2 http://127.0.0.1:8010/api/health >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS --max-time 3 http://127.0.0.1:8010/api/health >/dev/null

cd "$ROOT"
DSA_GATEWAY_KEY_FILE="$KEY_FILE" "$PYTHON" -m uvicorn scripts.online_gateway:app \
  --host 127.0.0.1 --port "$GATEWAY_PORT" >> "$GATEWAY_LOG" 2>&1 &
gateway_pid=$!

for _ in {1..30}; do
  if curl -fsS --max-time 2 -H "X-DSA-Gateway-Key: $(<"$KEY_FILE")" \
    "http://127.0.0.1:$GATEWAY_PORT/__local_gateway_health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if [[ ! -s "$GIST_ID_FILE" ]]; then
  pointer_file="$(mktemp -t dsa-online-origin).json"
  print -r -- '{"origin":"https://starting.invalid","updated_at":"initializing"}' > "$pointer_file"
  gist_url="$($GH gist create --public --desc "DSA online origin pointer" \
    --filename dsa-online-origin.json "$pointer_file")"
  print -r -- "${gist_url##*/}" > "$GIST_ID_FILE"
  chmod 600 "$GIST_ID_FILE"
  rm -f "$pointer_file"
fi
gist_id="$(<"$GIST_ID_FILE")"

while true; do
  : > "$TUNNEL_LOG"
  PATH="${NODE:h}:$PATH" "$LOCALTUNNEL" --port "$GATEWAY_PORT" \
    --local-host 127.0.0.1 > "$TUNNEL_LOG" 2>&1 &
  tunnel_pid=$!

  origin=""
  for _ in {1..60}; do
    origin="$(grep -Eo 'https://[-a-z0-9]+\.loca\.lt' "$TUNNEL_LOG" | tail -1 || true)"
    [[ -n "$origin" ]] && break
    kill -0 "$tunnel_pid" 2>/dev/null || break
    sleep 1
  done

  if [[ -n "$origin" ]]; then
    print -r -- "$origin" > "$ORIGIN_FILE"
    chmod 600 "$ORIGIN_FILE"
    update_file="$(mktemp -t dsa-gist-update).json"
    "$PYTHON" - "$origin" "$update_file" <<'PY'
import json
import sys
from datetime import datetime, timezone

origin, output = sys.argv[1:]
content = json.dumps({
    "origin": origin,
    "updated_at": datetime.now(timezone.utc).isoformat(),
}, ensure_ascii=False, separators=(",", ":"))
with open(output, "w", encoding="utf-8") as handle:
    json.dump({"files": {"dsa-online-origin.json": {"content": content}}}, handle)
PY
    "$GH" api --method PATCH "gists/$gist_id" --input "$update_file" >/dev/null
    rm -f "$update_file"
  fi

  wait "$tunnel_pid" || true
  tunnel_pid=""
  sleep 3
done

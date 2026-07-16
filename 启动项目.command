#!/bin/zsh

set -u

PROJECT_DIR="${0:A:h}"
PORT=8010
URL="http://127.0.0.1:${PORT}"
ONLINE_SCREEN="dsa_online_access"

cd "$PROJECT_DIR" || exit 1

start_online_access() {
  if ! screen -ls 2>/dev/null | grep -q "\.${ONLINE_SCREEN}[[:space:]]"; then
    screen -dmS "$ONLINE_SCREEN" /bin/zsh -c \
      "cd '$PROJECT_DIR' && exec ./scripts/run_online_access.sh"
  fi
}

if lsof -nP -iTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1; then
  start_online_access
  echo "DSA 已在 ${URL} 运行，在线访问服务也已启动。"
  open "$URL"
  exit 0
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "未找到项目虚拟环境：${PROJECT_DIR}/.venv"
  echo "请先完成项目依赖安装。"
  read "?按回车键关闭..."
  exit 1
fi

echo "正在以 Web + 定时分析模式启动 DSA..."
echo "项目目录：${PROJECT_DIR}"
echo "访问地址：${URL}"
echo "关闭此窗口或按 Control-C 可停止项目。"
echo

SCHEDULER_PID=""
WEB_PID=""
OPENER_PID=""

cleanup() {
  trap - EXIT
  for pid in "$OPENER_PID" "$WEB_PID" "$SCHEDULER_PID"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      kill -TERM "$pid" >/dev/null 2>&1 || true
    fi
  done
  for pid in "$WEB_PID" "$SCHEDULER_PID"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" >/dev/null 2>&1 || true
    fi
  done
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

(
  for _ in {1..90}; do
    if curl -fsS --max-time 1 "$URL" >/dev/null 2>&1; then
      start_online_access
      open "$URL"
      exit 0
    fi
    sleep 1
  done
) &
OPENER_PID=$!

# Keep scheduled analysis and alert polling outside the Web process. A slow
# market-data SDK call can then delay one refresh without freezing the page.
WEBUI_ENABLED=false .venv/bin/python main.py --schedule --no-run-immediately &
SCHEDULER_PID=$!

.venv/bin/python main.py --serve-only --host 127.0.0.1 --port "$PORT" &
WEB_PID=$!

wait "$WEB_PID"
STATUS=$?

exit "$STATUS"

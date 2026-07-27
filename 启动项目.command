#!/bin/zsh

set -u

PROJECT_DIR="${0:A:h}"
PORT=8010
URL="http://127.0.0.1:${PORT}"
DOMAIN="gui/$(id -u)"
WEB_LABEL="com.llss.dsa.web"
SCHEDULER_LABEL="com.llss.dsa.scheduler"
WEB_LOG="/tmp/dsa-web.out"
WEB_ERROR_LOG="/tmp/dsa-web.err"
SCHEDULER_LOG="/tmp/dsa-scheduler.out"
SCHEDULER_ERROR_LOG="/tmp/dsa-scheduler.err"

cd "$PROJECT_DIR" || exit 1

frontend_healthy() {
  local index_html
  index_html=$(curl -fsS --max-time 5 "$URL/" 2>/dev/null) || return 1
  [[ "$index_html" == *'<div id="root"></div>'* ]] || return 1

  local entry_asset
  entry_asset=$(printf '%s' "$index_html" | sed -n 's/.*src="\([^"]*\/assets\/[^"]*\.js\)".*/\1/p' | head -n 1)
  [[ -n "$entry_asset" ]] || return 1
  curl -fsS --max-time 8 "$URL$entry_asset" -o /dev/null 2>/dev/null
}

job_exists() {
  launchctl print "$DOMAIN/$1" >/dev/null 2>&1
}

submit_job() {
  local label="$1"
  local stdout_path="$2"
  local stderr_path="$3"
  local command="$4"
  launchctl submit -l "$label" -o "$stdout_path" -e "$stderr_path" -- /bin/zsh -c "$command"
}

if frontend_healthy; then
  echo "DSA 已在 ${URL} 运行，正在打开浏览器。"
  open "$URL"
  exit 0
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "未找到项目虚拟环境：${PROJECT_DIR}/.venv"
  echo "请先完成项目依赖安装。"
  read "?按回车键关闭..."
  exit 1
fi

echo "正在以后台 Web + 定时分析模式启动 DSA..."
echo "项目目录：${PROJECT_DIR}"
echo "访问地址：${URL}"
echo "启动成功后可以关闭此窗口，项目会继续运行。"
echo

# Keep scheduled analysis and alert polling outside the Web process. A slow
# market-data SDK call can then delay one refresh without freezing the page.
if ! job_exists "$SCHEDULER_LABEL"; then
  submit_job \
    "$SCHEDULER_LABEL" \
    "$SCHEDULER_LOG" \
    "$SCHEDULER_ERROR_LOG" \
    "cd ${(q)PROJECT_DIR} && exec env WEBUI_ENABLED=false .venv/bin/python main.py --schedule --no-run-immediately"
fi

if job_exists "$WEB_LABEL"; then
  echo "检测到 Web 后台任务，但页面响应异常，正在重启。"
  launchctl kickstart -k "$DOMAIN/$WEB_LABEL"
else
  if lsof -nP -iTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1; then
    echo "端口 ${PORT} 已被其他进程占用，无法启动 DSA。"
    echo "请先关闭占用该端口的程序后重试。"
    read "?按回车键关闭..."
    exit 1
  fi
  submit_job \
    "$WEB_LABEL" \
    "$WEB_LOG" \
    "$WEB_ERROR_LOG" \
    "cd ${(q)PROJECT_DIR} && exec .venv/bin/python main.py --serve-only --host 127.0.0.1 --port $PORT"
fi

for _ in {1..90}; do
  if frontend_healthy; then
    echo "DSA 启动成功，正在打开浏览器。"
    open "$URL"
    exit 0
  fi
  sleep 1
done

echo "DSA 启动超时，请查看："
echo "  $WEB_ERROR_LOG"
echo "  $WEB_LOG"
read "?按回车键关闭..."
exit 1

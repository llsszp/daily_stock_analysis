#!/bin/zsh

set -u

PROJECT_DIR="${0:A:h}"
PORT=8010
URL="http://127.0.0.1:${PORT}"

cd "$PROJECT_DIR" || exit 1

if lsof -nP -iTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1; then
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

echo "正在以 Web + 定时分析模式启动 DSA..."
echo "项目目录：${PROJECT_DIR}"
echo "访问地址：${URL}"
echo "关闭此窗口或按 Control-C 可停止项目。"
echo

(
  for _ in {1..90}; do
    if curl -fsS --max-time 1 "$URL" >/dev/null 2>&1; then
      open "$URL"
      exit 0
    fi
    sleep 1
  done
) &

exec .venv/bin/python main.py --serve --schedule --host 127.0.0.1 --port "$PORT"

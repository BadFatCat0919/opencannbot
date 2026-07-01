#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# cannbot-proxy.sh — 管理 CANNBOT Claude 代理进程(启动 / 停止 / 状态)
#
#   ./cannbot-proxy.sh start     启动后台代理(别名: install)
#   ./cannbot-proxy.sh stop      停止代理        (别名: uninstall)
#   ./cannbot-proxy.sh status    查看运行状态
#   ./cannbot-proxy.sh restart   重启
#
# VK 来源(代理自身解析,优先级从高到低):
#   请求头 ANTHROPIC_AUTH_TOKEN(vk- 开头) > $CANNBOT_VK 环境变量 > ~/.cannbot/vk 文件。
# 端口/地址可用 CANNBOT_CLAUDE_PROXY_PORT(默认 8766) / CANNBOT_PROXY_HOST(默认 127.0.0.1) 覆盖。
# ----------------------------------------------------------------------------
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PROXY="$DIR/cannbot-claude-proxy.py"
PID_FILE="$DIR/proxy.pid"
LOG_FILE="$DIR/proxy.log"
PORT="${CANNBOT_CLAUDE_PROXY_PORT:-8766}"
HOST="${CANNBOT_PROXY_HOST:-127.0.0.1}"
HEALTH="http://$HOST:$PORT/_health"

green() { printf '\033[32m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }

# Echo the PID of a live proxy process, or nothing.
running_pid() {
  if [ -f "$PID_FILE" ]; then
    local p; p="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then echo "$p"; return; fi
  fi
  pgrep -f "$PROXY" 2>/dev/null | head -1 || true
}

health() { curl -fsS --noproxy '*' "$HEALTH" 2>/dev/null || true; }

start() {
  local pid; pid="$(running_pid)"
  if [ -n "$pid" ]; then yellow "already running (PID $pid) on $HOST:$PORT"; return 0; fi
  [ -f "$PROXY" ] || { red "proxy not found: $PROXY"; exit 1; }
  if [ -z "${CANNBOT_VK:-}" ] && [ ! -s "$HOME/.cannbot/vk" ]; then
    yellow "note: no VK yet — proxy will start and pick it up later from"
    yellow "      \$CANNBOT_VK / ~/.cannbot/vk / the request's ANTHROPIC_AUTH_TOKEN."
  fi

  nohup python3 "$PROXY" --port "$PORT" --host "$HOST" >>"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  local newpid; newpid="$(cat "$PID_FILE")"

  # Prewarm does a VK->JWT exchange before binding, so give it a few seconds.
  for _ in $(seq 1 16); do
    if [ -n "$(health)" ]; then green "started (PID $newpid), listening on $HOST:$PORT"; return 0; fi
    kill -0 "$newpid" 2>/dev/null || { red "failed to start; last log:"; tail -n 8 "$LOG_FILE"; rm -f "$PID_FILE"; exit 1; }
    sleep 0.5
  done
  yellow "started (PID $newpid) but health not OK yet — check: tail -f $LOG_FILE"
}

stop() {
  local pid; pid="$(running_pid)"
  if [ -z "$pid" ]; then yellow "not running"; rm -f "$PID_FILE"; return 0; fi
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 0.3; done
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  green "stopped (was PID $pid)"
}

status() {
  local pid; pid="$(running_pid)"
  if [ -z "$pid" ]; then red "● stopped"; return 1; fi
  green "● running (PID $pid) on $HOST:$PORT"
  local h; h="$(health)"
  if [ -n "$h" ]; then echo "  health: $h"; else yellow "  health: no response (still warming up?)"; fi
}

case "${1:-}" in
  start|install)     start ;;
  stop|uninstall)    stop ;;
  status)            status ;;
  restart)           stop; start ;;
  *) echo "Usage: $0 {start|stop|status|restart}"; exit 2 ;;
esac

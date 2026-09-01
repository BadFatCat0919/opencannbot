#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# install-cannbot-claude.sh
#
# One-shot installer that wires CANNBOT into Claude Code on macOS / Linux.
#
# What it does:
#   1. Downloads cannbot-claude-proxy.py and cannbot-proxy.sh to
#      ~/.cannbot/proxy/
#   2. Asks for your Virtual Key (or reads $CANNBOT_VK) and writes it to
#      ~/.cannbot/vk with mode 0600.
#   3. Appends ANTHROPIC_* / CLAUDE_CODE_* environment variables to your
#      shell rc file (~/.bashrc or ~/.zshrc).
#   4. Optionally installs a systemd user unit / launchd agent for auto-start
#      (pass --service to enable).
#   5. Starts the proxy and prints a Claude Code configuration summary.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main/install-cannbot-claude.sh | bash
#   # or, from a local clone:
#   ./install-cannbot-claude.sh
#   ./install-cannbot-claude.sh --service   # also install auto-start service
#
# Environment overrides:
#   CANNBOT_VK                Pre-fill the Virtual Key (skips the prompt).
#   CANNBOT_CLAUDE_MODEL      Default model for ANTHROPIC_MODEL (default deepseek-v4-pro).
#   CANNBOT_CLAUDE_PROXY_PORT Port to listen on (default 8766).
#   CANNBOT_PROXY_HOST        Bind address (default 127.0.0.1).
#   CANNBOT_REPO_RAW          Override the raw-content base URL (for forks).
# ----------------------------------------------------------------------------
set -euo pipefail

REPO_RAW="${CANNBOT_REPO_RAW:-https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main}"
PROXY_URL="$REPO_RAW/cannbot-claude-proxy.py"
MANAGER_URL="$REPO_RAW/cannbot-proxy.sh"

INSTALL_DIR="${CANNBOT_INSTALL_DIR:-$HOME/.cannbot/proxy}"
PROXY_FILE="$INSTALL_DIR/cannbot-claude-proxy.py"
MANAGER_FILE="$INSTALL_DIR/cannbot-proxy.sh"
VK_FILE="$HOME/.cannbot/vk"
LOG_FILE="$INSTALL_DIR/proxy.log"
PID_FILE="$INSTALL_DIR/proxy.pid"
PORT="${CANNBOT_CLAUDE_PROXY_PORT:-8766}"
HOST="${CANNBOT_PROXY_HOST:-127.0.0.1}"
MODEL="${CANNBOT_CLAUDE_MODEL:-deepseek-v4-pro}"

INSTALL_SERVICE=false
[ "${1:-}" = "--service" ] && INSTALL_SERVICE=true

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }

bold "======================================="
bold "  CANNBOT Proxy for Claude Code"
bold "======================================="
echo

# ── Sanity checks ──────────────────────────────────────────────────────
command -v python3 >/dev/null 2>&1 || { red "python3 not found. Please install Python 3.8+."; exit 1; }
PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 8 ]; }; then
  red "Python $PY_MAJOR.$PY_MINOR found, need 3.8+."; exit 1
fi

mkdir -p "$INSTALL_DIR" "$(dirname "$VK_FILE")"

# ── 1. Obtain proxy components ─────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

download() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --connect-timeout 10 --max-time 30 "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then
    wget -q --timeout=30 --tries=1 -O "$2" "$1"
  else
    red "Neither curl nor wget found."; exit 1
  fi
}

if [ -f "$SCRIPT_DIR/cannbot-claude-proxy.py" ]; then
  cp "$SCRIPT_DIR/cannbot-claude-proxy.py" "$PROXY_FILE"
  green "[1/4] Proxy script copied from local clone -> $PROXY_FILE"
else
  download "$PROXY_URL" "$PROXY_FILE"
  green "[1/4] Proxy script downloaded -> $PROXY_FILE"
fi
chmod +x "$PROXY_FILE"

if [ -f "$SCRIPT_DIR/cannbot-proxy.sh" ]; then
  cp "$SCRIPT_DIR/cannbot-proxy.sh" "$MANAGER_FILE"
  green "[1/4] Manager script copied from local clone -> $MANAGER_FILE"
else
  download "$MANAGER_URL" "$MANAGER_FILE"
  green "[1/4] Manager script downloaded -> $MANAGER_FILE"
fi
chmod +x "$MANAGER_FILE"

# ── 2. Configure VK ───────────────────────────────────────────────────
if [ -n "${CANNBOT_VK:-}" ]; then
  VK="$CANNBOT_VK"
elif [ -s "$VK_FILE" ]; then
  VK="$(cat "$VK_FILE")"
  yellow "[2/4] Reusing existing VK from $VK_FILE"
else
  printf "Enter your CANNBOT Virtual Key (vk-...): "
  read -r VK < /dev/tty || VK=""
fi
if [ -z "${VK:-}" ]; then
  red "No Virtual Key provided. Set CANNBOT_VK and re-run, or pass it interactively."
  exit 1
fi
printf '%s\n' "$VK" > "$VK_FILE"
chmod 600 "$VK_FILE"
green "[2/4] Virtual Key saved -> $VK_FILE (mode 0600)"

# ── 3. Write shell rc ─────────────────────────────────────────────────
# Detect shell rc file. Prefer .zshrc over .bashrc on macOS, .bashrc otherwise.
ERRATA=""
RC_FILE=""
if [ "$(uname -s)" = "Darwin" ]; then
  [ -f "$HOME/.zshrc" ] && RC_FILE="$HOME/.zshrc"
  [ -z "$RC_FILE" ] && [ -f "$HOME/.bash_profile" ] && RC_FILE="$HOME/.bash_profile"
  [ -z "$RC_FILE" ] && [ -f "$HOME/.bashrc" ] && RC_FILE="$HOME/.bashrc"
else
  [ -f "$HOME/.bashrc" ] && RC_FILE="$HOME/.bashrc"
  [ -z "$RC_FILE" ] && [ -f "$HOME/.zshrc" ] && RC_FILE="$HOME/.zshrc"
  [ -z "$RC_FILE" ] && [ -f "$HOME/.bash_profile" ] && RC_FILE="$HOME/.bash_profile"
fi

if [ -n "$RC_FILE" ]; then
  # Remove any existing CANNBOT gateway block to avoid duplicates.
  sed -i '/^# >>> CANNBOT gateway (claude)/,/^# <<< CANNBOT gateway (claude)/d' "$RC_FILE"
  cat >> "$RC_FILE" <<EOF

# >>> CANNBOT gateway (claude) >>>
# Installed by install-cannbot-claude.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
export CANNBOT_VK="\$(head -1 $VK_FILE 2>/dev/null)"
export ANTHROPIC_BASE_URL="http://${HOST}:${PORT}"
export ANTHROPIC_AUTH_TOKEN="\$CANNBOT_VK"
export ANTHROPIC_MODEL="${MODEL}"
# Claude Code context window: deepseek-v4-pro = 1M / 393216 (set per-model)
export CLAUDE_CODE_MAX_CONTEXT_TOKENS=393216
# <<< CANNBOT gateway (claude) <<<
EOF
  green "[3/4] Environment variables appended to $RC_FILE"
else
  yellow "[3/4] No shell rc file found. You will need to set env vars manually:"
  yellow "      export ANTHROPIC_BASE_URL=http://${HOST}:${PORT}"
  yellow "      export ANTHROPIC_AUTH_TOKEN=${VK}"
  yellow "      export ANTHROPIC_MODEL=${MODEL}"
  ERRATA="1"
fi

# ── 4. Install service (optional) ─────────────────────────────────────
SERVICE_NAME="com.cannbot.claude"
if [ "$INSTALL_SERVICE" = true ]; then
  # Write a launcher that the service unit can call.
  RUN_SCRIPT="$INSTALL_DIR/run.sh"
  cat > "$RUN_SCRIPT" <<EOF
#!/usr/bin/env bash
# Auto-generated by install-cannbot-claude.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
set -e
export CANNBOT_VK="\$(cat "$VK_FILE")"
export CANNBOT_CLAUDE_PROXY_PORT="$PORT"
export CANNBOT_PROXY_HOST="$HOST"
exec "$PROXY_FILE" \\
    --vk "\$CANNBOT_VK" \\
    --port "\$CANNBOT_CLAUDE_PROXY_PORT" \\
    --host "\$CANNBOT_PROXY_HOST" \\
    --log "$LOG_FILE"
EOF
  chmod +x "$RUN_SCRIPT"

  if [ "$(uname -s)" = "Darwin" ]; then
    PLIST="$HOME/Library/LaunchAgents/$SERVICE_NAME.plist"
    mkdir -p "$(dirname "$PLIST")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$SERVICE_NAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$RUN_SCRIPT</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$LOG_FILE</string>
    <key>StandardErrorPath</key><string>$LOG_FILE</string>
    <key>WorkingDirectory</key><string>$INSTALL_DIR</string>
</dict>
</plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load -w "$PLIST"
    green "[4/4] launchd agent installed -> $PLIST"
  else
    UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$UNIT_DIR"
    UNIT="$UNIT_DIR/cannbot-claude.service"
    cat > "$UNIT" <<EOF
[Unit]
Description=CANNBOT Gateway Proxy for Claude Code
After=network-online.target

[Service]
Type=simple
ExecStart=$RUN_SCRIPT
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now cannbot-claude.service
    green "[4/4] systemd user unit installed -> $UNIT"
  fi
else
  green "[4/4] Auto-start service not installed (use --service to enable)"
fi

# ── 5. Start proxy & verify ───────────────────────────────────────────
# Kill any existing process on our port first.
"$MANAGER_FILE" stop 2>/dev/null || true
sleep 0.5
"$MANAGER_FILE" start 2>&1 || true

HEALTH="$(curl -fsS --noproxy '*' "http://${HOST}:${PORT}/_health" 2>/dev/null || echo '{"status":"starting"}')"
green "[5/5] Proxy health: $HEALTH"

echo
bold "===================== Done! ====================="
echo
echo "  Claude Code is now configured to use CANNBOT."
echo
echo "  Start Claude Code:"
echo "    claude"
echo
echo "  Switch model temporarily:"
echo "    ANTHROPIC_MODEL=glm-5.2 claude"
echo
echo "  Available models:"
echo "    deepseek-v4-pro   (default, 1M ctx / 393K out)"
echo "    glm-5.2           (1M ctx / 131K out)"
echo "    glm-5.1           (202K ctx / 131K out)"
echo "    qwen-3-7-max      (1M ctx / 65K out)"
echo "    qwen-3-6-plus     (1M ctx / 65K out)"
echo
echo "  Manage proxy:"
echo "    $MANAGER_FILE start     start"
echo "    $MANAGER_FILE stop      stop"
echo "    $MANAGER_FILE status    status"
echo "    $MANAGER_FILE restart   restart"
echo
echo "  Logs: tail -f $LOG_FILE"
echo "  Uninstall: $SCRIPT_DIR/uninstall-cannbot-claude.sh"
echo
if [ -n "$ERRATA" ]; then
  echo "  ⚠ No shell rc file was found. Set the env vars above in your shell profile."
  echo
fi
echo "  Open a new terminal or run: source $RC_FILE"
echo "================================================"
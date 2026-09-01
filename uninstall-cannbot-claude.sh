#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# uninstall-cannbot-claude.sh
#
# Removes everything installed by install-cannbot-claude.sh:
#   * Stops the proxy (via cannbot-proxy.sh stop)
#   * Removes the CANNBOT gateway env-var block from your shell rc file
#   * Stops and deletes the launchd agent / systemd user unit (if any)
#   * Optionally removes the proxy files and saved VK
#
# Usage:
#   ./uninstall-cannbot-claude.sh          # stop + remove rc block + service
#   ./uninstall-cannbot-claude.sh --purge  # also delete proxy files & VK
# ----------------------------------------------------------------------------
set -euo pipefail

INSTALL_DIR="${CANNBOT_INSTALL_DIR:-$HOME/.cannbot/proxy}"
MANAGER_FILE="$INSTALL_DIR/cannbot-proxy.sh"
VK_FILE="$HOME/.cannbot/vk"
SERVICE_NAME="com.cannbot.claude"

PURGE=false
[ "${1:-}" = "--purge" ] && PURGE=true

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }

bold "Uninstalling CANNBOT proxy for Claude Code..."

# ── 1. Stop the proxy ─────────────────────────────────────────────────
if [ -x "$MANAGER_FILE" ]; then
  "$MANAGER_FILE" stop 2>/dev/null || true
  green "  - stopped proxy via $MANAGER_FILE"
else
  # Fallback: kill any live cannbot-claude-proxy.py we can find.
  PIDS="$(pgrep -f "$INSTALL_DIR/cannbot-claude-proxy.py" 2>/dev/null || true)"
  if [ -n "$PIDS" ]; then
    # shellcheck disable=SC2086
    kill $PIDS 2>/dev/null || true
    green "  - killed proxy processes: $PIDS"
  fi
fi

# ── 2. Remove shell rc block ──────────────────────────────────────────
for RC in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.profile"; do
  if [ -f "$RC" ]; then
    if grep -q '^# >>> CANNBOT gateway (claude)' "$RC" 2>/dev/null; then
      sed -i '/^# >>> CANNBOT gateway (claude)/,/^# <<< CANNBOT gateway (claude)/d' "$RC"
      green "  - removed CANNBOT block from $RC"
    fi
  fi
done

# ── 3. Remove service unit ────────────────────────────────────────────
if [ "$(uname -s)" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/$SERVICE_NAME.plist"
  if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    green "  - removed launchd agent $PLIST"
  fi
else
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now cannbot-claude.service 2>/dev/null || true
    UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/cannbot-claude.service"
    rm -f "$UNIT"
    systemctl --user daemon-reload 2>/dev/null || true
    green "  - removed systemd user unit (if any)"
  fi
fi

# ── 4. Optionally purge files ─────────────────────────────────────────
if [ "$PURGE" = true ]; then
  rm -rf "$INSTALL_DIR"
  green "  - removed $INSTALL_DIR"
  printf "Also remove saved Virtual Key at %s? [y/N] " "$VK_FILE"
  read -r ANS < /dev/tty || ANS="n"
  if [[ "$ANS" =~ ^[Yy]$ ]]; then
    rm -f "$VK_FILE"
    green "  - removed $VK_FILE"
  fi
else
  echo "  (use --purge to also delete $INSTALL_DIR and the saved VK)"
fi

green "Done. Env vars will disappear from new terminals."
echo "Run 'unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_MODEL CANNBOT_VK' in the current one."
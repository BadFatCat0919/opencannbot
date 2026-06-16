#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# uninstall-cannbot-trae.sh
#
# Removes everything installed by install-cannbot-trae.sh:
#   * Stops the launchd agent / systemd user unit
#   * Deletes the plist / unit file
#   * Optionally removes ~/.cannbot/proxy and the saved VK
# ----------------------------------------------------------------------------
set -euo pipefail

INSTALL_DIR="${CANNBOT_INSTALL_DIR:-$HOME/.cannbot/proxy}"
VK_FILE="$HOME/.cannbot/vk"
SERVICE_NAME="com.cannbot.proxy"

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }

bold "Uninstalling CANNBOT proxy for Trae..."

if [ "$(uname -s)" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/$SERVICE_NAME.plist"
  if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    green "  - removed launchd agent $PLIST"
  fi
else
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now cannbot-proxy.service 2>/dev/null || true
    UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/cannbot-proxy.service"
    rm -f "$UNIT"
    systemctl --user daemon-reload 2>/dev/null || true
    green "  - removed systemd user unit"
  fi
fi

if [ -d "$INSTALL_DIR" ]; then
  rm -rf "$INSTALL_DIR"
  green "  - removed $INSTALL_DIR"
fi

printf "Also remove saved Virtual Key at %s? [y/N] " "$VK_FILE"
read -r ANS < /dev/tty || ANS="n"
if [[ "$ANS" =~ ^[Yy]$ ]]; then
  rm -f "$VK_FILE"
  green "  - removed $VK_FILE"
fi

green "Done."

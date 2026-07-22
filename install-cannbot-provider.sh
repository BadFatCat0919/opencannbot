#!/usr/bin/env bash
set -euo pipefail

REPO_RAW="${CANNBOT_REPO_RAW:-https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main}"
PLUGIN_URL="$REPO_RAW/cannbot-auth.js"
PRO_PLUGIN_URL="$REPO_RAW/cannbot-pro-auth.js"

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/opencode"
PLUGIN_DIR="$CONFIG_DIR/plugins"
PLUGIN_FILE="$PLUGIN_DIR/cannbot-auth.js"
PRO_PLUGIN_FILE="$PLUGIN_DIR/cannbot-pro-auth.js"
OPENCODE_JSON="$CONFIG_DIR/opencode.json"

bold()   { printf '\033[1m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red()    { printf '\033[31m%s\033[0m\n' "$*"; }

bold "======================================="
bold "  CANNBOT Provider for OpenCode"
bold "======================================="
echo

command -v opencode >/dev/null 2>&1 || { red "opencode not found. Please install opencode first."; exit 1; }
command -v node >/dev/null 2>&1 || { red "node not found."; exit 1; }

mkdir -p "$PLUGIN_DIR" "$DATA_DIR"

# ── 1. Download plugins ─────────────────────────────────────────────────

download() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --connect-timeout 10 --max-time 30 "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then
    wget -q --timeout=30 --tries=1 -O "$2" "$1"
  else
    red "Neither curl nor wget found."; exit 1
  fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
if [ -f "$SCRIPT_DIR/cannbot-auth.js" ]; then
  cp "$SCRIPT_DIR/cannbot-auth.js" "$PLUGIN_FILE"
  green "[1/4] CANNBOT plugin copied from local clone -> $PLUGIN_FILE"
else
  download "$PLUGIN_URL" "$PLUGIN_FILE"
  green "[1/4] CANNBOT plugin downloaded -> $PLUGIN_FILE"
fi

if [ -f "$SCRIPT_DIR/cannbot-pro-auth.js" ]; then
  cp "$SCRIPT_DIR/cannbot-pro-auth.js" "$PRO_PLUGIN_FILE"
  green "[2/4] CANNBOT-Pro plugin copied from local clone -> $PRO_PLUGIN_FILE"
else
  download "$PRO_PLUGIN_URL" "$PRO_PLUGIN_FILE"
  green "[2/4] CANNBOT-Pro plugin downloaded -> $PRO_PLUGIN_FILE"
fi

# ── 2. Update opencode.json ─────────────────────────────────────────────

PLUGIN_URI="file://$PLUGIN_FILE"
PRO_PLUGIN_URI="file://$PRO_PLUGIN_FILE"

if [ -f "$OPENCODE_JSON" ]; then
  node -e "
    const fs = require('fs');
    const cfg = JSON.parse(fs.readFileSync('$OPENCODE_JSON', 'utf-8'));
    const plugins = cfg.plugin || [];
    for (const uri of ['$PLUGIN_URI', '$PRO_PLUGIN_URI']) {
      if (!plugins.includes(uri)) plugins.push(uri);
    }
    cfg.plugin = plugins;
    fs.writeFileSync('$OPENCODE_JSON', JSON.stringify(cfg, null, 2) + '\n');
  "
else
  node -e "
    const fs = require('fs');
    const cfg = {
      '\$schema': 'https://opencode.ai/config.json',
      plugin: ['$PLUGIN_URI', '$PRO_PLUGIN_URI']
    };
    fs.writeFileSync('$OPENCODE_JSON', JSON.stringify(cfg, null, 2) + '\n');
  "
fi

green "[3/4] opencode.json updated -> $OPENCODE_JSON"
green "[4/4] Done!"

echo
bold "Restart opencode, then run:"
echo
echo "  /connect"
echo
echo "Select 'CANNBOT' (standard) or 'CANNBOT-Pro' (Claude Opus / GPT) and enter your VK."
echo

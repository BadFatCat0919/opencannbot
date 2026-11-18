#!/usr/bin/env bash
set -euo pipefail

REPO_RAW="${CANNBOT_REPO_RAW:-https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main}"
PLUGIN_URL="$REPO_RAW/cannbot-auth.js"
PRO_PLUGIN_URL="$REPO_RAW/cannbot-pro-auth.js"
V2_PLUGIN_URL="$REPO_RAW/cannbot-v2/index.js"

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/opencode"
PLUGIN_DIR="$CONFIG_DIR/plugins"
PLUGIN_FILE="$PLUGIN_DIR/cannbot-auth.js"
PRO_PLUGIN_FILE="$PLUGIN_DIR/cannbot-pro-auth.js"
V2_DIR="$PLUGIN_DIR/cannbot"
V2_FILE="$V2_DIR/index.js"
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

# ── 0. Detect plugin API version ────────────────────────────────────────
# opencode 2.x requires the V2 plugin API; 1.x uses the legacy plugin files.

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

VERSION_TEXT="$(opencode --version 2>/dev/null | head -n1 || true)"
MAJOR="$(printf '%s' "$VERSION_TEXT" | grep -o '[0-9][0-9]*' | head -n1 || true)"
if [ -z "$MAJOR" ]; then
  yellow "Could not determine the opencode version (\"$VERSION_TEXT\"); assuming 2.x."
  MAJOR=2
fi

if [ "$MAJOR" -ge 2 ]; then
  bold "Detected opencode $VERSION_TEXT (V2 plugin API)."

  mkdir -p "$V2_DIR"
  if [ -f "$SCRIPT_DIR/cannbot-v2/index.js" ]; then
    cp "$SCRIPT_DIR/cannbot-v2/index.js" "$V2_FILE"
    green "[1/3] CANNBOT V2 plugin copied from local clone -> $V2_FILE"
  else
    download "$V2_PLUGIN_URL" "$V2_FILE"
    green "[1/3] CANNBOT V2 plugin downloaded -> $V2_FILE"
  fi

  removed=0
  for legacy in "$PLUGIN_FILE" "$PRO_PLUGIN_FILE"; do
    if [ -f "$legacy" ]; then
      rm -f "$legacy"
      green "[2/3] Removed legacy V1 plugin -> $legacy"
      removed=1
    fi
  done
  [ "$removed" -eq 1 ] || green "[2/3] No legacy V1 plugin files found"

  if [ -f "$OPENCODE_JSON" ]; then
    node - "$OPENCODE_JSON" <<'NODE'
const fs = require("fs");
const file = process.argv[2];
let cfg;
try {
  cfg = JSON.parse(fs.readFileSync(file, "utf-8"));
} catch {
  console.warn("Could not parse " + file + " (comments/JSONC). Remove cannnbot-auth.js entries from the plugin array manually.");
  process.exit(0);
}
let changed = false;
for (const key of ["plugin", "plugins"]) {
  const list = cfg[key];
  if (!Array.isArray(list)) continue;
  const kept = list.filter((entry) => {
    const text = typeof entry === "string" ? entry : entry && entry.package;
    const legacy = typeof text === "string" && /cannbot(-pro)?-auth\.js$/i.test(text);
    if (legacy) changed = true;
    return !legacy;
  });
  if (kept.length === 0) { delete cfg[key]; changed = true; }
  else cfg[key] = kept;
}
if (changed) fs.writeFileSync(file, JSON.stringify(cfg, null, 2) + "\n");
NODE
    green "[3/3] Cleaned legacy cannnbot entries in opencode.json (if any)"
  else
    green "[3/3] No opencode.json changes needed (plugins/cannbot is auto-discovered)"
  fi

  echo
  bold "Restart opencode, then:"
  echo
  echo "  1) /connect  -> select CANNBOT or CANNBOT-Pro, enter your VK"
  echo "  2) pick a CANNBOT model in the model list"
  echo
  yellow "Models appear after the VK is connected."
  yellow "For scripted runs, set the model in opencode.json instead of --model:"
  echo '  { "model": "cannbot/deepseek-v4-pro" }'
  echo
  exit 0
fi

bold "Detected opencode $VERSION_TEXT (V1 plugin API)."
if [ -d "$V2_DIR" ]; then
  rm -rf "$V2_DIR"
  green "[1/4] Removed V2 plugin -> $V2_DIR"
else
  green "[1/4] V1 plugin layout selected"
fi

# ── 1. Download plugins ─────────────────────────────────────────────────

if [ -f "$SCRIPT_DIR/cannbot-auth.js" ]; then
  cp "$SCRIPT_DIR/cannbot-auth.js" "$PLUGIN_FILE"
  green "[2/4] CANNBOT plugin copied from local clone -> $PLUGIN_FILE"
else
  download "$PLUGIN_URL" "$PLUGIN_FILE"
  green "[2/4] CANNBOT plugin downloaded -> $PLUGIN_FILE"
fi

if [ -f "$SCRIPT_DIR/cannbot-pro-auth.js" ]; then
  cp "$SCRIPT_DIR/cannbot-pro-auth.js" "$PRO_PLUGIN_FILE"
  green "[3/4] CANNBOT-Pro plugin copied from local clone -> $PRO_PLUGIN_FILE"
else
  download "$PRO_PLUGIN_URL" "$PRO_PLUGIN_FILE"
  green "[3/4] CANNBOT-Pro plugin downloaded -> $PRO_PLUGIN_FILE"
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

green "[4/4] opencode.json updated -> $OPENCODE_JSON"

echo
bold "Restart opencode, then run:"
echo
echo "  /connect"
echo
echo "Select 'CANNBOT' (standard) or 'CANNBOT-Pro' (Claude Opus / GPT) and enter your VK."
echo

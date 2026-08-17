#!/usr/bin/env bash
set -euo pipefail

# install-cannbot-dsh.sh — 一键把 CANNBOT 接入 DeepSeek Harness (DSH)
#
# 插件已独立发布到 github.com/RemakerHammand/dsh-llm-cannbot
# 本脚本通过官方 `dsh plugin add` 命令从 GitHub 安装。
#
# 前置要求：
#   - 已安装 Node.js >= 22.19 或 >= 24
#   - 已通过 npx @deepseek-ai/dsh 运行过 dsh（生成了 ~/.dsh）
#   - 有一个有效的 CANNBOT Virtual Key (vk-xxxx)

DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
WEB_PROFILE_DIR="$DSH_HOME/profiles/web"
PLUGIN_REPO="github:RemakerHammand/dsh-llm-cannbot"

bold()   { printf '\033[1m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red()    { printf '\033[31m%s\033[0m\n' "$*"; }

bold "======================================="
bold "  CANNBOT LLM Adapter for DSH"
bold "======================================="
echo

# ── 前置检查 ────────────────────────────────────────────────────────────

command -v node >/dev/null 2>&1 || { red "Node.js not found. Please install Node.js >= 22.19 first."; exit 1; }

if [ ! -d "$DSH_HOME" ]; then
  yellow "DSH home not found at $DSH_HOME"
  yellow "Please run 'npx @deepseek-ai/dsh web' once first to initialize DSH."
  exit 1
fi

if [ ! -d "$WEB_PROFILE_DIR" ]; then
  yellow "Web profile not found at $WEB_PROFILE_DIR"
  yellow "Please run 'npx @deepseek-ai/dsh web' once first to create the web profile."
  exit 1
fi

# ── 检查是否已安装 ──────────────────────────────────────────────────────

PACKAGE_JSON="$WEB_PROFILE_DIR/package.json"
if [ -f "$PACKAGE_JSON" ] && grep -q "dsh-llm-cannbot" "$PACKAGE_JSON"; then
  yellow "Plugin already in web profile bundles (skipped)"
  yellow "To update:  dsh plugin --profile web update dsh-llm-cannbot"
  echo
else
  # ── 1. 通过官方命令安装 ────────────────────────────────────────────────
  bold "[1/3] Installing plugin from GitHub..."

  DSH_BIN="${DSH_BIN:-npx @deepseek-ai/dsh}"

  if $DSH_BIN plugin --profile web add "$PLUGIN_REPO" 2>&1; then
    green "[1/3] Plugin installed via dsh plugin add"
  else
    red "[1/3] Failed to install plugin via dsh plugin add"
    yellow ""
    yellow "Manual install alternatives:"
    yellow "  1. dsh plugin --profile web add $PLUGIN_REPO"
    yellow "  2. Or clone https://github.com/RemakerHammand/dsh-llm-cannbot"
    yellow "     and copy into ~/.dsh/plugins/dsh-llm-cannbot/"
    exit 1
  fi
fi

# ── 2. 引导配置 VK ──────────────────────────────────────────────────────

echo
bold "[2/3] Configure CANNBOT Virtual Key"
echo

CRED_FILE="$DSH_HOME/.credentials.yaml"
HAS_VK=false
if [ -f "$CRED_FILE" ] && grep -q "CANNBOT_API_KEY" "$CRED_FILE"; then
  HAS_VK=true
fi

if [ "$HAS_VK" = true ]; then
  green "CANNBOT_API_KEY already configured in credentials"
else
  if [ -n "${CANNBOT_API_KEY:-}" ]; then
    VK="$CANNBOT_API_KEY"
  else
    printf "请输入你的 CANNBOT Virtual Key (vk-xxxx): "
    read -r VK
  fi

  if [[ ! "$VK" =~ ^vk- ]]; then
    red "Invalid VK format. Expected 'vk-xxxx'."
    yellow "You can configure it later via the DSH Web UI Settings → Models page."
  else
    if [ ! -f "$CRED_FILE" ]; then
      printf "CANNBOT_API_KEY: %s\n" "$VK" > "$CRED_FILE"
      chmod 600 "$CRED_FILE"
    else
      if grep -q "^CANNBOT_API_KEY:" "$CRED_FILE"; then
        sed -i.bak "s/^CANNBOT_API_KEY:.*/CANNBOT_API_KEY: $VK/" "$CRED_FILE"
        rm -f "$CRED_FILE.bak"
      else
        printf "CANNBOT_API_KEY: %s\n" "$VK" >> "$CRED_FILE"
      fi
    fi
    green "CANNBOT_API_KEY stored in credentials"
  fi
fi

# ── 3. 验证插件加载 ─────────────────────────────────────────────────────

echo
bold "[3/3] Verifying plugin..."
DSH_BIN="${DSH_BIN:-npx @deepseek-ai/dsh}"
if $DSH_BIN --profile web --dump-default-config 2>&1 | grep -q "llm-cannbot"; then
  green "[3/3] Plugin verified in composed web profile config"
else
  yellow "[3/3] Warning: plugin not found in config dump."
  yellow "       Check that the DSH install is accessible and retry."
fi

# ── 完成 ────────────────────────────────────────────────────────────────

echo
bold "======================================="
green "  CANNBOT DSH adapter installed!"
bold "======================================="
echo
echo "下一步："
echo "  1. 重启 DSH Web:  npx @deepseek-ai/dsh web"
echo "  2. 刷新浏览器页面"
echo "  3. 在模型选择器中选择 cannbot / glm-5.2 等模型"
echo "  4. 在 Settings → Models 页面可以修改 VK 和查看配置"
echo "  5. 输入 /cannbot-usage 查看当日 token 用量"
echo
echo "可用模型：glm-5.2, glm-5.1, deepseek-v4-pro, deepseek-v4-flash-0731, qwen3.7-max"
echo
echo "插件源码：https://github.com/RemakerHammand/dsh-llm-cannbot"

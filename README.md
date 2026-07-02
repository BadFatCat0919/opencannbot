# OpenCANNBot — 把 CANNBOT 接到你顺手的开发环境里

> 良将当配好马。CANNBOT 网关提供的模型实力不俗，可一旦只能困在自带的那道命令行里，就有几分「吕布骑狗」的憋屈——身手施展不开。
>
> [OpenCode](https://opencode.ai)、[Trae IDE](https://www.trae.ai)、[Claude Code](https://claude.com/claude-code)、[VS Code Copilot Chat](https://code.visualstudio.com/docs/copilot/overview) 这些日常常用的 Agent 框架，正是称手的坐骑。OpenCANNBot 一键把 CANNBot 网关的模型接进它们，让良将配上好马，在你每天都在用的框架里跑出全部身手。

## 目录

- [支持的模型](#支持的模型)
- [通用约定](#通用约定)
- [OpenCode 接入](#opencode-接入)
- [Trae IDE 接入](#trae-ide-接入)
- [Claude Code 接入](#claude-code-接入)
- [VS Code Copilot Chat 接入](#vs-code-copilot-chat-接入)
- [仓库结构](#仓库结构)
- [许可](#许可)

---

## OpenCode 接入

### 前置要求

- 已安装 [opencode](https://opencode.ai/docs/installation/)
- 已安装 Node.js

### 安装

直连：

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main/install-cannbot-provider.sh | bash
```

```powershell
# Windows（PowerShell）
irm https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main/install-cannbot-provider.ps1 | iex
```

国内加速（以 `gh-proxy.com` 为例）：

```bash
# macOS / Linux
B="https://gh-proxy.com/https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main"; curl -fsSL "$B/install-cannbot-provider.sh" | CANNBOT_REPO_RAW="$B" bash
```

```powershell
# Windows（PowerShell）
$B="https://gh-proxy.com/https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main"; $env:CANNBOT_REPO_RAW=$B; irm "$B/install-cannbot-provider.ps1" | iex
```

### 配置

安装完成后重启 opencode，输入 `/connect`，选择 **CANNBOT** 并填入你的 VK 即可。

### 验证

在 opencode 中发起一次对话，能正常返回即接入成功。

---

## Trae IDE 接入

### 前置要求

- Python 3.8+（macOS / Linux 通常自带；Windows 从 <https://python.org> 下载）
- Trae IDE（任意版本）

### 安装

直连：

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main/install-cannbot-trae.sh | bash
```

```powershell
# Windows（PowerShell）
irm https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main/install-cannbot-trae.ps1 | iex
```

国内加速（以 `gh-proxy.com` 为例）：

```bash
# macOS / Linux
B="https://gh-proxy.com/https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main"; curl -fsSL "$B/install-cannbot-trae.sh" | CANNBOT_REPO_RAW="$B" bash
```

```powershell
# Windows（PowerShell）
$B="https://gh-proxy.com/https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main"; $env:CANNBOT_REPO_RAW=$B; irm "$B/install-cannbot-trae.ps1" | iex
```

安装脚本会下载运行组件、提示输入 VK 并保存到 `~/.cannbot/vk`，然后注册为登录自启、崩溃自重启的后台服务（macOS 用 launchd，Linux 用 systemd user unit，Windows 用名为 `CANNBOTProxyForTrae` 的 Scheduled Task），最后自动启动并做一次健康检查。

**手动前台运行**（不装后台服务时）：

```bash
curl -fsSL https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main/cannbot-proxy.py -o /tmp/cannbot-proxy.py
CANNBOT_VK="vk-xxxxxxxxxxxxxxxxxxxx" python3 /tmp/cannbot-proxy.py
```

支持的 flag / 环境变量：

| Flag           | Env 变量                 | 默认值      | 说明 |
|----------------|--------------------------|-------------|------|
| `--vk`         | `CANNBOT_VK`             | （必填）    | 你的 Virtual Key |
| `--port`       | `CANNBOT_PROXY_PORT`     | `8765`      | 监听端口 |
| `--host`       | `CANNBOT_PROXY_HOST`     | `127.0.0.1` | 监听地址（请勿暴露到公网） |
| `--log-level`  | `CANNBOT_LOG_LEVEL`      | `INFO`      | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `--log`        | —                        | —           | 把日志同时写到指定文件 |
| `--daemon`     | —                        | —           | fork 到后台，PID 写入 `~/.cannbot/proxy/proxy.pid`（仅 POSIX） |
| —              | `CANNBOT_KEEPALIVE_IDLE` | `300`       | 无数据最大等待秒数 |
| —              | `CANNBOT_SOCKET_TIMEOUT` | `30`        | 单次 socket 读超时 |

### 配置

打开 Trae IDE → **Settings → AI → Model Provider → Add Provider**（自定义），按下表填写：

| 字段          | 值 |
|---------------|-----|
| Provider 名称 | `CANNBOT`（任意，方便识别即可） |
| API Base URL  | `http://127.0.0.1:8765/v1` |
| API Key       | 你的 Virtual Key（`vk-xxxxxx`） |
| Model         | `claude-opus-4-8`（或其它，见[支持的模型](#支持的模型)） |

保存后即可在 Trae 中使用。

### 验证

```bash
curl -sS http://127.0.0.1:8765/_health
# {"status": "ok", "vk_configured": true, "jwt_cached": true, "jwt_expires_in": 3540, "gateway": "..."}
```

返回 `ok` 即接入正常。

### 卸载

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main/uninstall-cannbot-trae.sh | bash
```

```powershell
# Windows
Unregister-ScheduledTask -TaskName CANNBOTProxyForTrae -Confirm:$false
Remove-Item -Recurse -Force "$env:USERPROFILE\.cannbot"
```

### 常见问题

**Q: 提示 `No Virtual Key configured`？**
A: 用 `--vk vk-xxxxx` 传入，或设置 `CANNBOT_VK=vk-xxxxx`，或在 `~/.cannbot/vk` 写一行（权限 `0600`）。

**Q: Trae 报 `connection refused`？**
A: 先 `curl http://127.0.0.1:8765/_health` 确认服务在跑；若用了自定义 host，确认 Trae 的 `API Base URL` 与之匹配；macOS 防火墙可能拦截 8765，在「系统设置 → 网络 → 防火墙」放行 `python3`。

**Q: 能暴露到公网吗？**
A: **不要**。VK/JWT 均为明文，本地环回（`127.0.0.1`）是唯一安全方式。

---

## Claude Code 接入

### 前置要求

- Python 3.8+（macOS / Linux 通常自带；Windows 从 <https://python.org> 下载）
- [Claude Code](https://claude.com/claude-code)

### 安装

用管理脚本 `cannbot-proxy.sh` 后台启停：

```bash
./cannbot-proxy.sh start     # 启动（别名 install）
./cannbot-proxy.sh status    # 查看运行状态与健康检查
./cannbot-proxy.sh stop      # 停止（别名 uninstall）
./cannbot-proxy.sh restart   # 重启
```

VK 来源优先级：请求头 `ANTHROPIC_AUTH_TOKEN`（`vk-` 开头）> `$CANNBOT_VK` > `~/.cannbot/vk`（`0600`）。端口 / 地址可用 `CANNBOT_CLAUDE_PROXY_PORT`（默认 `8766`）/ `CANNBOT_PROXY_HOST`（默认 `127.0.0.1`）覆盖。

### 配置

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8766"
export ANTHROPIC_AUTH_TOKEN="vk-xxxxxxxxxxxxxxxxxxxx"
export ANTHROPIC_MODEL="claude-opus-4-8"                # 或其它，见「支持的模型」
claude
```

### 验证

```bash
curl -sS http://127.0.0.1:8766/_health
# {"status": "ok", "vk_configured": true, "jwt_cached": true, ...}
```

返回 `ok` 即可在 Claude Code 中直接对话。

---

## VS Code Copilot Chat 接入

`cannbot-vscode` 扩展把 CANNBot 模型注册进 GitHub Copilot Chat 的模型选择器，与 GPT、Claude 并列显示，Agent 模式、工具调用、Skills 等能力开箱即用。

### 前置要求

- VS Code + GitHub Copilot Chat
- Node.js（用于构建 VSIX）

### 安装

```bash
cd cannbot-vscode && npm install && npm run compile && npx vsce package
code --install-extension cannbot-vscode-*.vsix
```

### 配置

1. `Ctrl+Shift+P` → **`CANNBot: Set Virtual Key (VK)`** → 输入你的 VK
2. 打开 Copilot Chat（`Ctrl+Shift+I`），点击模型下拉菜单
3. 选择任意 CANNBot 模型（见[支持的模型](#支持的模型)）
4. 正常聊天

VK 存储在 VS Code 的 `SecretStorage`（操作系统密钥链），不会出现在 `settings.json` 中。

### 验证

在 Copilot Chat 模型选择器中能看到 CANNBot 模型，且对话正常返回即接入成功。

### 构建

```bash
cd cannbot-vscode
npm install       # 安装依赖
npm run compile   # 编译 TypeScript
npm run package   # 打包 .vsix
```

---

## 支持的模型

| 模型 | Context | Max Output |
|------|---------|------------|
| DeepSeek V4 Pro | 1M | 393,216 |
| GLM 5.2 | 1M | 131,072 |
| GLM 5.1 | 202K | 131,072 |
| Qwen 3.7 Max | 1M | 65,535 |
| Qwen 3.6 Plus | 1M | 65,535 |

---

## 仓库结构

| 文件 | 作用 |
|------|------|
| `cannbot-auth.js` | OpenCode 用的 provider 插件 |
| `install-cannbot-provider.sh` / `.ps1` | 一键把插件装进 opencode |
| `cannbot-proxy.py` | Trae IDE 用的本地运行组件 |
| `install-cannbot-trae.sh` / `.ps1` | 一键安装 Trae 组件（macOS / Linux / Windows） |
| `uninstall-cannbot-trae.sh` | 卸载 Trae 组件 |
| `cannbot-claude-proxy.py` | Claude Code 用的本地运行组件 |
| `cannbot-proxy.sh` | Claude Code 组件的启停管理脚本（start / stop / status / restart） |
| `cannbot-vscode/` | VS Code 扩展 |

## 许可

MIT License。

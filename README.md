# OpenCANNBot — CANNBOT Provider for OpenCode / Trae

一键为 [OpenCode](https://opencode.ai) 和 [Trae IDE](https://www.trae.ai) 添加 CANNBOT Provider，无需安装 cannbot CLI。

## 目录

- [OpenCode 接入](#opencode-接入)
  - [快速开始](#快速开始)
  - [工作原理](#工作原理)
- [Trae IDE 接入](#trae-ide-接入)
  - [为什么需要代理](#为什么需要代理)
  - [快速开始](#快速开始-1)
  - [手动运行](#手动运行)
  - [配置 Trae](#配置-trae)
  - [验证](#验证)
  - [卸载](#卸载)
  - [常见问题](#常见问题)
- [仓库结构](#仓库结构)
- [许可](#许可)

---

## OpenCode 接入

### 前置要求

- 已安装 [opencode](https://opencode.ai/docs/installation/)
- 已安装 Node.js

### 快速开始

**一键安装：**

macOS / Linux：

```bash
curl -fsSL https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main/install-cannbot-provider.sh | bash
```

Windows（PowerShell）：

```powershell
irm https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main/install-cannbot-provider.ps1 | iex
```

**国内加速（GitHub 代理）：** 若直连 `raw.githubusercontent.com` 超时，用 GitHub 镜像前缀代替上面的命令。镜像前缀同时套在「脚本地址」和 `CANNBOT_REPO_RAW` 上（前者下载脚本本身，后者让脚本内部下载插件也走镜像）。以 `gh-proxy.com` 为例（也可换成 `ghfast.top`、`ghproxy.net` 等）：

macOS / Linux：

```bash
B="https://gh-proxy.com/https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main"; curl -fsSL "$B/install-cannbot-provider.sh" | CANNBOT_REPO_RAW="$B" bash
```

Windows（PowerShell）：

```powershell
$B="https://gh-proxy.com/https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main"; $env:CANNBOT_REPO_RAW=$B; irm "$B/install-cannbot-provider.ps1" | iex
```

脚本只负责注册 provider，安装完成后重启 opencode，在 opencode 中输入 `/connect`，输入 **CANNBOT** 并填入你的 Virtual Key (VK)。

### 工作原理

OpenCode 通过 `cannbot-auth.js` 插件完成 VK→JWT 兑换并把 `x-api-vkey`、`Authorization: Bearer <jwt>` 两个 header 注入到每次请求中。JWT 缓存在 `~/.cannbot/jwt.json`，过期前 60 秒自动续期。

---

## Trae IDE 接入

### 为什么需要代理

Trae IDE 的自定义 Provider 只支持一个 `Authorization: Bearer <key>` header，而 CANNBOT 网关要求**两个** header：

```
x-api-vkey:    <你的 Virtual Key>
Authorization: Bearer <短期 JWT>
```

其中 JWT 需要先调用 `/cannbot/api/auth/authenticate` 兑换，而且有效期只有 1 小时左右。所以本仓库提供一个 200 行的本地代理 `cannbot-proxy.py`（仅使用 Python 标准库），把 Trae 发来的单 header 请求转换成网关要求的两 header 请求。

代理与上游 OpenCode 插件共用相同的 VK→JWT 兑换协议和相同的网关地址，所以两边得到的 token / 模型列表完全一致。

### 前置要求

- Python 3.8+（macOS / Linux 通常自带；Windows 从 https://python.org 下载）
- Trae IDE（任意版本）

### 快速开始

macOS / Linux：

```bash
curl -fsSL https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main/install-cannbot-trae.sh | bash
```

Windows（PowerShell）：

```powershell
irm https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main/install-cannbot-trae.ps1 | iex
```

**国内加速（GitHub 代理）：** 若直连 `raw.githubusercontent.com` 超时，用 GitHub 镜像前缀代替上面的命令（镜像前缀同时套在「脚本地址」和 `CANNBOT_REPO_RAW` 上，后者让脚本内部下载 `cannbot-proxy.py` 也走镜像）。以 `gh-proxy.com` 为例：

macOS / Linux：

```bash
B="https://gh-proxy.com/https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main"; curl -fsSL "$B/install-cannbot-trae.sh" | CANNBOT_REPO_RAW="$B" bash
```

Windows（PowerShell）：

```powershell
$B="https://gh-proxy.com/https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main"; $env:CANNBOT_REPO_RAW=$B; irm "$B/install-cannbot-trae.ps1" | iex
```

安装脚本会：

1. 把 `cannbot-proxy.py` 下载到 `~/.cannbot/proxy/`；
2. 提示（或读取环境变量）输入你的 Virtual Key，并保存到 `~/.cannbot/vk`（权限 0600）；
3. 写一个启动脚本 `~/.cannbot/proxy/run.sh`；
4. **macOS**：写一份 `~/Library/LaunchAgents/com.cannbot.proxy.plist`，登录自启并崩溃自重启；
   **Linux**：写一份 `~/.config/systemd/user/cannbot-proxy.service` 并 enable；
   **Windows**：注册一个名为 `CANNBOTProxyForTrae` 的 Scheduled Task，登录时启动、失败自动重启；
5. 启动代理，并通过 `/_health` 端点确认。

### 手动运行

如果你不想装成后台服务，可以前台跑：

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main/cannbot-proxy.py -o /tmp/cannbot-proxy.py
CANNBOT_VK="vk-xxxxxxxxxxxxxxxxxxxx" python3 /tmp/cannbot-proxy.py
```

前台运行参数（也支持 CLI flags 覆盖环境变量）：

| Flag           | Env 变量               | 默认值         | 说明 |
|----------------|------------------------|----------------|------|
| `--vk`         | `CANNBOT_VK`           | （必填）       | 你的 Virtual Key |
| `--port`       | `CANNBOT_PROXY_PORT`   | `8765`         | 监听端口 |
| `--host`       | `CANNBOT_PROXY_HOST`   | `127.0.0.1`    | 监听地址（请勿暴露到公网） |
| `--log-level`  | `CANNBOT_LOG_LEVEL`    | `INFO`         | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `--log`        | —                      | —              | 把日志同时写到指定文件 |
| `--daemon`     | —                      | —              | fork 到后台，并把 PID 写到 `~/.cannbot/proxy/proxy.pid`（仅 POSIX） |

如果懒得每次设置环境变量，也可以把 VK 写进 `~/.cannbot/vk`（chmod 0600），代理启动时会自动读取。

### 配置 Trae

1. 打开 Trae IDE，进入 **Settings → AI → Model Provider**。
2. 点击 **Add Provider**（自定义）。
3. 按下表填写：

   | 字段           | 值                                |
   |----------------|-----------------------------------|
   | Provider 名称  | `CANNBOT`（任意，方便识别即可）   |
   | API Base URL   | `http://127.0.0.1:8765/v1`         |
   | API Key        | 你的 Virtual Key（`vk-xxxxxx`）   |
   | Model          | `glm-5.1` / `qwen3.7-max` / 其它  |

4. 保存。Trae 第一次请求时会通过代理拿到模型列表，之后的对话/补全请求都会被透明转发到 CANNBOT。

### 验证

代理启动后自带一个轻量健康检查端点：

```bash
curl -sS http://127.0.0.1:8765/_health
# {"status": "ok", "vk_configured": true, "jwt_cached": true, "jwt_expires_in": 3540, "gateway": "..."}
```

测试一次真实请求：

```bash
curl -sS http://127.0.0.1:8765/v1/models \
  -H "Authorization: Bearer vk-xxxxxxxxxxxxxxxxxxxx"
```

如果看到模型列表（JSON），说明代理工作正常。

### 卸载

macOS / Linux：

```bash
curl -fsSL https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main/uninstall-cannbot-trae.sh | bash
```

Windows：

```powershell
Unregister-ScheduledTask -TaskName CANNBOTProxyForTrae -Confirm:$false
Remove-Item -Recurse -Force "$env:USERPROFILE\.cannbot"
```

### 常见问题

**Q: 启动时提示 `No Virtual Key configured` 怎么办？**
A: 通过 `--vk vk-xxxxx` 显式传入，或设置环境变量 `CANNBOT_VK=vk-xxxxx`，或在 `~/.cannbot/vk` 中写一行（文件权限 0600）。

**Q: 401 / JWT exchange failed 怎么排查？**
A:
- 先用 `curl -i https://cannbot.hicann.cn/cannbot/api/auth/authenticate -H "x-api-vkey: vk-xxxxx" -H "Content-Type: application/json" -d '{"type":"cli","mac":"00:00:00:00:00:00"}'` 直接调一次，确保 VK 是有效的。
- 然后 `python3 -m http.server 8765` 这种端口冲突也会被代理捕获并以 502 返回。

**Q: Trae 报 `connection refused` 怎么办？**
A:
- 检查代理是否在跑：`curl http://127.0.0.1:8765/_health`。
- 如果用了 `--host 0.0.0.0` 或自定义 host，确认 Trae 配置的 `API Base URL` 与之匹配。
- macOS 防火墙可能拦截 8765；「系统设置 → 网络 → 防火墙」里允许 `python3` 即可。

**Q: 能不能把代理暴露到公网？**
A: **不要**。代理没有鉴权，VK/JWT 都是明文。本地环回（`127.0.0.1`）是唯一安全的使用方式。

**Q: 流式输出（streaming）能工作吗？**
A: 可以。代理是纯 HTTP 透传，不会缓冲 SSE chunk，OpenAI 风格 `data: ...\n\n` 流照常工作。

**Q: 跟 OpenCode 插件会冲突吗？**
A: 不会。两者独立：OpenCode 插件（`cannbot-auth.js`）只注入 opencode 的请求；本代理只监听 `127.0.0.1:8765`，处理 Trae 的请求。同一台机器可以同时装两份。

---

## 仓库结构

| 文件 | 作用 |
|------|------|
| `cannbot-auth.js` | OpenCode 插件：在 opencode 内部完成 VK→JWT 兑换 + 注入双 header |
| `install-cannbot-provider.sh` / `.ps1` | 一键把 `cannbot-auth.js` 装进 opencode |
| `cannbot-proxy.py` | **新增**：本地 HTTP 代理，让 Trae IDE 也能走 CANNBOT 网关 |
| `install-cannbot-trae.sh` / `.ps1` | **新增**：一键安装 Trae 代理（macOS/Linux/Windows 三个平台） |
| `uninstall-cannbot-trae.sh` | **新增**：卸载 Trae 代理 |

## 许可

MIT License。

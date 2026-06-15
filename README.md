# OpenCANNBot - CANNBOT Provider for OpenCode

一键为 [OpenCode](https://opencode.ai) 添加 CANNBOT Provider，无需安装 cannbot CLI。

## 功能

- 自动注册 CANNBOT 作为 opencode provider
- 支持 CANNBOT 网关的所有模型（Qwen、DeepSeek 等）
- 通过 Virtual Key (VK) 认证，兼容多种环境

## 快速开始

### 前置要求

- 已安装 [opencode](https://opencode.ai/docs/installation/)
- 已安装 Node.js

### 安装

1. 获取你的 Virtual Key (VK)：访问 https://cannbot.hicann.cn -> 设置 -> API Keys

2. 运行安装脚本：

```bash
bash install-cannbot-provider.sh
```

脚本会提示输入 VK，输入后自动完成配置。

### 验证

重启 opencode，在模型选择器中应该能看到 `cannbot/` 前缀的模型：

```
cannbot/qwen-plus
cannbot/qwen-max
cannbot/deepseek-v3
...
```

## 文件说明

- `install-cannbot-provider.sh` - 一键安装脚本
- `cannbot-auth.js` - OpenCode 认证插件

## 工作原理

安装脚本会自动：

1. 将认证插件写入 `~/.config/opencode/plugins/cannbot-auth.js`
2. 在 `~/.config/opencode/opencode.json` 中注册插件
3. 将 VK 写入 `~/.local/share/opencode/auth.json`（权限 600）

插件通过以下 hook 工作：

- **config** - 动态注册 CANNBOT provider 及模型列表
- **auth** - 加载 VK 认证
- **chat.headers** - 注入 `x-api-vkey` 和 `Authorization` 请求头

## 手动配置

如果需要手动添加或修改 VK：

```bash
# 编辑 auth.json
vim ~/.local/share/opencode/auth.json
```

添加：

```json
{
  "cannbot": {
    "type": "api",
    "key": "vk-your-virtual-key-here"
  }
}
```

## 许可证

MIT

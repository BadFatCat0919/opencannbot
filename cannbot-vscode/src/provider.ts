/**
 * CANNBot LanguageModelChatProvider
 *
 * Implements vscode.LanguageModelChatProvider so CANNBOT models appear
 * directly in the Copilot Chat model picker (like deepseek-v4-for-copilot).
 */

import * as vscode from 'vscode';
import { getJwt, clearJwtCache, jwtStatus } from './auth';

const VK_SECRET = 'cannbot.virtualKey';
const GATEWAY_URL = 'https://cannbot.hicann.cn/gateway/compatible-mode/v1';

// ── Model definitions ───────────────────────────────────────────────────
interface CannbotModelDef {
  id: string;
  name: string;
  family: string;
  version: string;
  maxInputTokens: number;
  maxOutputTokens: number;
}

const MODELS: CannbotModelDef[] = [
  { id: 'claude-opus-4-8', name: 'Claude Opus 4.8', family: 'claude', version: '4.8', maxInputTokens: 1048576, maxOutputTokens: 131072 },
  { id: 'gpt-5.4', name: 'GPT 5.4', family: 'gpt', version: '5.4', maxInputTokens: 1048576, maxOutputTokens: 128000 },
  { id: 'deepseek-v4-pro', name: 'DeepSeek V4 Pro', family: 'deepseek', version: '4', maxInputTokens: 1048576, maxOutputTokens: 393216 },
  { id: 'glm-5.2', name: 'GLM 5.2', family: 'glm', version: '5.2', maxInputTokens: 1048576, maxOutputTokens: 131072 },
  { id: 'glm-5.1', name: 'GLM 5.1', family: 'glm', version: '5.1', maxInputTokens: 206848, maxOutputTokens: 131072 },
  { id: 'glm-5', name: 'GLM 5', family: 'glm', version: '5', maxInputTokens: 169984, maxOutputTokens: 131072 },
  { id: 'qwen3.7-max', name: 'Qwen 3.7 Max', family: 'qwen', version: '3.7', maxInputTokens: 1048576, maxOutputTokens: 65535 },
  { id: 'qwen3.6-plus', name: 'Qwen 3.6 Plus', family: 'qwen', version: '3.6', maxInputTokens: 1048576, maxOutputTokens: 65535 },
];

// ── Provider ─────────────────────────────────────────────────────────────
export class CannbotChatProvider implements vscode.LanguageModelChatProvider {
  private readonly _onDidChange = new vscode.EventEmitter<void>();
  readonly onDidChangeLanguageModelChatInformation = this._onDidChange.event;

  private isActive = true;

  constructor(private readonly context: vscode.ExtensionContext) {
    context.subscriptions.push(
      context.secrets.onDidChange((e) => {
        if (e.key === VK_SECRET) {
          this._onDidChange.fire();
        }
      }),
      this._onDidChange,
    );
  }

  // ── Public commands ─────────────────────────────────────────────────

  async configureApiKey(): Promise<void> {
    const current = await this.context.secrets.get(VK_SECRET);
    const vk = await vscode.window.showInputBox({
      title: 'CANNBot Virtual Key',
      prompt: 'Enter your CANNBot Virtual Key (vk-xxxx...)',
      placeHolder: 'vk-xxxxxxxxxxxxxxxxxxxx',
      value: current ?? '',
      password: true,
      ignoreFocusOut: true,
      validateInput: (value: string) => {
        if (!value) return 'Virtual Key is required';
        if (!value.startsWith('vk-')) return 'Must start with "vk-"';
        if (value.length < 10) return 'Virtual Key seems too short';
        return null;
      },
    });

    if (vk !== undefined) {
      clearJwtCache();
      await this.context.secrets.store(VK_SECRET, vk);
      this._onDidChange.fire();
      vscode.window.showInformationMessage(
        'CANNBot Virtual Key saved. Select a CANNBot model from the Copilot Chat model picker.'
      );
    }
  }

  async clearApiKey(): Promise<void> {
    const confirm = await vscode.window.showWarningMessage(
      'Clear CANNBot Virtual Key?',
      { modal: true },
      'Yes, clear it'
    );
    if (confirm === 'Yes, clear it') {
      clearJwtCache();
      await this.context.secrets.delete(VK_SECRET);
      this._onDidChange.fire();
      vscode.window.showInformationMessage('CANNBot Virtual Key cleared.');
    }
  }

  async showStatus(): Promise<void> {
    const vk = await this.getVk();
    const status = jwtStatus();
    const config = vscode.workspace.getConfiguration('cannbot');
    const model = config.get('defaultModel') || 'glm-5.1';

    if (!vk) {
      const set = await vscode.window.showWarningMessage(
        'CANNBot: No Virtual Key configured.',
        'Set Virtual Key'
      );
      if (set) await this.configureApiKey();
      return;
    }

    const vkPreview = vk.length > 12 ? vk.slice(0, 8) + '...' + vk.slice(-4) : vk;
    vscode.window.showInformationMessage(
      `CANNBot: ${vkPreview} | Model: ${model} | JWT: ${status.valid ? 'cached' : 'pending'}`
    );
  }

  refreshModelPicker(): void {
    this._onDidChange.fire();
  }

  // ── LanguageModelChatProvider ────────────────────────────────────────

  async provideLanguageModelChatInformation(
    _options: vscode.PrepareLanguageModelChatModelOptions,
    _token: vscode.CancellationToken
  ): Promise<vscode.LanguageModelChatInformation[]> {
    if (!this.isActive) return [];

    const hasKey = !!(await this.getVk());

    return MODELS.map((m) => ({
      id: m.id,
      name: m.name,
      family: m.family,
      version: m.version,
      detail: hasKey ? 'CANNBot model — ready' : 'Set Virtual Key in CANNBot: Set VK',
      statusIcon: hasKey ? undefined : new vscode.ThemeIcon('warning'),
      maxInputTokens: m.maxInputTokens,
      maxOutputTokens: m.maxOutputTokens,
      isBYOK: true,
      isUserSelectable: true,
      capabilities: {
        toolCalling: true,
        imageInput: false,
      },
    }));
  }

  async provideLanguageModelChatResponse(
    modelInfo: vscode.LanguageModelChatInformation,
    messages: readonly vscode.LanguageModelChatRequestMessage[],
    options: vscode.ProvideLanguageModelChatResponseOptions,
    progress: vscode.Progress<vscode.LanguageModelResponsePart>,
    token: vscode.CancellationToken
  ): Promise<void> {
    const vk = await this.getVk();
    if (!vk) {
      progress.report(
        new vscode.LanguageModelTextPart(
          '⚠️ No CANNBot Virtual Key configured. Run "CANNBot: Set Virtual Key" from the Command Palette.'
        )
      );
      return;
    }

    // Convert VS Code messages to OpenAI format
    const config = vscode.workspace.getConfiguration('cannbot');
    const temperature = config.get<number>('temperature') ?? 0.3;
    const maxTokens = config.get<number>('maxTokens') ?? 0;

    const openaiMessages: { role: string; content: string }[] = [];

    for (const msg of messages) {
      let content = '';
      for (const part of msg.content) {
        if (part instanceof vscode.LanguageModelTextPart) {
          content += part.value;
        }
      }
      if (content) {
        const role =
          msg.role === vscode.LanguageModelChatMessageRole.User
            ? 'user'
            : msg.role === vscode.LanguageModelChatMessageRole.Assistant
            ? 'assistant'
            : 'user';
        openaiMessages.push({ role, content });
      }
    }

    // Convert tools
    const tools = options.tools?.map((t) => ({
      type: 'function' as const,
      function: {
        name: t.name,
        description: t.description,
        parameters: t.inputSchema,
      },
    }));

    // Exchange VK for JWT
    let jwt: string;
    try {
      jwt = await getJwt(vk);
    } catch (err: any) {
      progress.report(new vscode.LanguageModelTextPart(`❌ Auth error: ${err.message}`));
      return;
    }

    // Build request
    const body: any = {
      model: modelInfo.id,
      messages: openaiMessages,
      temperature,
      stream: true,
    };
    if (tools?.length) body.tools = tools;
    if (maxTokens > 0) body.max_tokens = maxTokens;

    const reqHeaders: Record<string, string> = {
      'Content-Type': 'application/json',
      'x-api-vkey': vk,
      Authorization: `Bearer ${jwt}`,
    };

    try {
      const response = await fetch(`${GATEWAY_URL}/chat/completions`, {
        method: 'POST',
        headers: reqHeaders,
        body: JSON.stringify(body),
        signal: token.isCancellationRequested
          ? AbortSignal.abort()
          : AbortSignal.timeout(300_000),
      });

      if (!response.ok) {
        const errText = await response.text().catch(() => 'unknown');
        progress.report(
          new vscode.LanguageModelTextPart(
            `❌ Gateway error (HTTP ${response.status}): ${errText.slice(0, 300)}`
          )
        );
        return;
      }

      const reader = response.body?.getReader();
      if (!reader) {
        progress.report(new vscode.LanguageModelTextPart('❌ No response body'));
        return;
      }

      const decoder = new TextDecoder();
      let buffer = '';

      try {
        while (true) {
          if (token.isCancellationRequested) break;

          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || !trimmed.startsWith('data: ')) continue;

            const payload = trimmed.slice(6).trim();
            if (payload === '[DONE]') return;

            try {
              const parsed = JSON.parse(payload);
              const delta = parsed.choices?.[0]?.delta;

              // Handle text content
              if (delta?.content) {
                progress.report(new vscode.LanguageModelTextPart(delta.content));
              }

              // Handle tool calls
              if (delta?.tool_calls) {
                for (const tc of delta.tool_calls) {
                  if (tc.function?.name) {
                    progress.report(
                      new vscode.LanguageModelToolCallPart(
                        tc.id || tc.index?.toString() || '0',
                        tc.function.name,
                        tc.function.arguments ? JSON.parse(tc.function.arguments) : {}
                      )
                    );
                  }
                }
              }
            } catch {
              // skip malformed JSON lines
            }
          }
        }
      } finally {
        reader.releaseLock();
      }
    } catch (err: any) {
      if (err.name === 'AbortError') return;
      progress.report(new vscode.LanguageModelTextPart(`❌ Error: ${err.message}`));
    }
  }

  async provideTokenCount(
    _modelInfo: vscode.LanguageModelChatInformation,
    text: string | vscode.LanguageModelChatRequestMessage,
    _token: vscode.CancellationToken
  ): Promise<number> {
    if (typeof text === 'string') {
      return Math.max(1, Math.ceil(text.length / 4));
    }
    let chars = 0;
    if (text?.content && Array.isArray(text.content)) {
      for (const part of text.content) {
        if (part instanceof vscode.LanguageModelTextPart) {
          chars += part.value.length;
        }
      }
    }
    return Math.max(1, Math.ceil(chars / 4));
  }

  // ── Helpers ─────────────────────────────────────────────────────────

  private async getVk(): Promise<string | undefined> {
    return this.context.secrets.get(VK_SECRET);
  }
}

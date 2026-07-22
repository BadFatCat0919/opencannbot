import * as vscode from 'vscode';
import { CannbotChatProvider } from './provider';

export function activate(context: vscode.ExtensionContext): void {
  const provider = new CannbotChatProvider(context);

  context.subscriptions.push(
    vscode.commands.registerCommand('cannbot.setVk', () => provider.configureApiKey()),
    vscode.commands.registerCommand('cannbot.clearVk', () => provider.clearApiKey()),
    vscode.commands.registerCommand('cannbot.status', () => provider.showStatus()),
    vscode.lm.registerLanguageModelChatProvider('cannbot', provider),
  );

  // Copilot Chat may cache model info without configurationSchema.
  // Activate it first so refresh reaches a live listener.
  void vscode.extensions.getExtension('github.copilot-chat')?.activate().then(() => {
    provider.refreshModelPicker();
  });
}

export function deactivate(): void {}

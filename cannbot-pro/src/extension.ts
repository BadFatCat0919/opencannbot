import * as vscode from 'vscode';
import { CannbotProChatProvider } from './provider';

export function activate(context: vscode.ExtensionContext): void {
  const provider = new CannbotProChatProvider(context);

  context.subscriptions.push(
    vscode.commands.registerCommand('cannbotpro.setVk', () => provider.configureApiKey()),
    vscode.commands.registerCommand('cannbotpro.clearVk', () => provider.clearApiKey()),
    vscode.commands.registerCommand('cannbotpro.status', () => provider.showStatus()),
    vscode.lm.registerLanguageModelChatProvider('cannbotpro', provider),
  );

  void vscode.extensions.getExtension('github.copilot-chat')?.activate().then(() => {
    provider.refreshModelPicker();
  });
}

export function deactivate(): void {}

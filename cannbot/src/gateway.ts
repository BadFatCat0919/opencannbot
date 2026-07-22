/**
 * CANNBOT OpenAI-compatible gateway client.
 *
 * Handles:
 *   - Model listing
 *   - Streaming chat completions (SSE)
 *   - Request header injection (x-api-vkey + Authorization: Bearer <JWT>)
 */

import { getJwt } from './auth';

// ── Constants ───────────────────────────────────────────────────────────
const GATEWAY_URL = 'https://cannbot.hicann.cn/gateway/compatible-mode/v1';
const FETCH_TIMEOUT_MS = 300_000; // 5 min for long generations

// ── Types ───────────────────────────────────────────────────────────────
export interface ChatMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface ModelInfo {
  id: string;
  title: string;
  contextLength: number;
  maxTokens: number;
}

export interface StreamChunk {
  type: 'delta' | 'done' | 'error';
  content?: string;
  error?: string;
}

// ── Internal helpers ────────────────────────────────────────────────────
async function authHeaders(vk: string): Promise<Record<string, string>> {
  const jwt = await getJwt(vk);
  return {
    'x-api-vkey': vk,
    'Authorization': `Bearer ${jwt}`,
    'Content-Type': 'application/json',
  };
}

// ── Public API ──────────────────────────────────────────────────────────
/**
 * Fetch available models from CANNBOT gateway.
 */
export async function fetchModels(vk: string): Promise<ModelInfo[]> {
  const headers = await authHeaders(vk);
  const response = await fetch(`${GATEWAY_URL}/models`, {
    method: 'GET',
    headers,
    signal: AbortSignal.timeout(10_000),
  });

  if (!response.ok) {
    const errText = await response.text().catch(() => 'unknown error');
    throw new Error(`Failed to fetch models (HTTP ${response.status}): ${errText}`);
  }

  const data: any = await response.json();
  const models: ModelInfo[] = (data.data || data || []).map((m: any) => ({
    id: m.id || m.model,
    title: m.title || m.id || m.model,
    contextLength: m.context_length || m.contextLength || 131072,
    maxTokens: m.max_tokens || m.maxTokens || 8192,
  }));

  return models;
}

/**
 * Send a streaming chat completion request.
 *
 * Yields `StreamChunk` events as SSE data arrives.
 */
export async function* streamChat(
  vk: string,
  model: string,
  messages: ChatMessage[],
  temperature: number = 0.3,
  signal?: AbortSignal,
): AsyncGenerator<StreamChunk> {
  const headers = await authHeaders(vk);

  const controller = new AbortController();
  const combinedSignal = signal
    ? combineAbortSignals(signal, AbortSignal.timeout(FETCH_TIMEOUT_MS))
    : AbortSignal.timeout(FETCH_TIMEOUT_MS);

  const response = await fetch(`${GATEWAY_URL}/chat/completions`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      model,
      messages,
      temperature,
      stream: true,
    }),
    signal: combinedSignal,
  });

  if (!response.ok) {
    const errText = await response.text().catch(() => 'unknown error');
    yield { type: 'error', error: `Gateway error (HTTP ${response.status}): ${errText}` };
    return;
  }

  const reader = response.body?.getReader();
  if (!reader) {
    yield { type: 'error', error: 'Response body is not readable' };
    return;
  }

  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // keep incomplete line in buffer

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith('data: ')) continue;

        const payload = trimmed.slice(6).trim();
        if (payload === '[DONE]') {
          yield { type: 'done' };
          return;
        }

        try {
          const parsed = JSON.parse(payload);
          const delta = parsed.choices?.[0]?.delta?.content;
          if (delta) {
            yield { type: 'delta', content: delta };
          }

          // Check for finish reason
          const finishReason = parsed.choices?.[0]?.finish_reason;
          if (finishReason === 'stop') {
            yield { type: 'done' };
            return;
          }
        } catch {
          // Skip malformed JSON lines
        }
      }
    }
  } finally {
    reader.releaseLock();
  }

  // If we exhaust the stream without a [DONE] marker
  yield { type: 'done' };
}

// ── Utility ─────────────────────────────────────────────────────────────
function combineAbortSignals(...signals: AbortSignal[]): AbortSignal {
  const controller = new AbortController();
  for (const signal of signals) {
    if (signal.aborted) {
      controller.abort(signal.reason);
      return controller.signal;
    }
    signal.addEventListener('abort', () => controller.abort(signal.reason), { once: true });
  }
  return controller.signal;
}

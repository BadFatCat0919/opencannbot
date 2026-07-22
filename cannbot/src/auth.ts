/**
 * CANNBOT VK → JWT authentication module.
 *
 * Exchanges a Virtual Key (vk-xxxx) for a short-lived JWT access token,
 * caches it in-memory, and refreshes it 60 seconds before expiry.
 */

import * as vscode from 'vscode';
import * as crypto from 'crypto';
import * as os from 'os';

// ── Constants ───────────────────────────────────────────────────────────
const AUTH_URL = 'https://cannbot.hicann.cn/cannbot/api/auth/authenticate';
const REFRESH_BEFORE_SEC = 60; // refresh JWT 60s before expiry
const DEFAULT_EXPIRES_IN = 3600; // 1h fallback

// ── JWT Cache ───────────────────────────────────────────────────────────
interface JwtEntry {
  accessToken: string;
  expiresAt: number; // unix seconds
}

let _jwtCache: JwtEntry | undefined;

// ── Helpers ─────────────────────────────────────────────────────────────
function getMac(): string {
  try {
    const interfaces = os.networkInterfaces();
    for (const iface of Object.values(interfaces)) {
      if (!iface) continue;
      for (const addr of iface) {
        if (addr.mac && addr.mac !== '00:00:00:00:00:00') {
          return addr.mac;
        }
      }
    }
  } catch {
    // fall through
  }
  return '00:00:00:00:00:00';
}

function jwtIsValid(): boolean {
  return !!(
    _jwtCache &&
    _jwtCache.accessToken &&
    _jwtCache.expiresAt > Math.floor(Date.now() / 1000) + REFRESH_BEFORE_SEC
  );
}

// ── Public API ──────────────────────────────────────────────────────────
/**
 * Get a valid JWT for the given Virtual Key.
 * Returns cached JWT if still valid, otherwise exchanges the VK.
 */
export async function getJwt(vk: string): Promise<string> {
  if (!vk || !vk.startsWith('vk-')) {
    throw new Error('Invalid Virtual Key. Must start with "vk-".');
  }

  if (jwtIsValid()) {
    return _jwtCache!.accessToken;
  }

  return exchangeVkForJwt(vk);
}

/**
 * Force a fresh VK→JWT exchange, ignoring cache.
 */
export async function exchangeVkForJwt(vk: string): Promise<string> {
  const body = JSON.stringify({ type: 'cli', mac: getMac() });

  const response = await fetch(AUTH_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-vkey': vk,
    },
    body,
  });

  if (!response.ok) {
    const errText = await response.text().catch(() => 'unknown error');
    throw new Error(`VK→JWT exchange failed (HTTP ${response.status}): ${errText}`);
  }

  const payload: any = await response.json();
  const accessToken = payload.accessToken || payload.access_token;
  const expiresIn = payload.expiresIn || payload.expires_in || DEFAULT_EXPIRES_IN;

  if (!accessToken) {
    throw new Error(`Auth response missing accessToken: ${JSON.stringify(payload)}`);
  }

  _jwtCache = {
    accessToken,
    expiresAt: Math.floor(Date.now() / 1000) + expiresIn - REFRESH_BEFORE_SEC,
  };

  return accessToken;
}

/**
 * Clear the cached JWT (e.g. when VK changes).
 */
export function clearJwtCache(): void {
  _jwtCache = undefined;
}

/**
 * Check if a cached JWT exists and its remaining lifetime.
 */
export function jwtStatus(): { valid: boolean; expiresInSec: number } {
  if (!_jwtCache) {
    return { valid: false, expiresInSec: 0 };
  }
  const remaining = _jwtCache.expiresAt - Math.floor(Date.now() / 1000);
  return { valid: remaining > 0, expiresInSec: Math.max(0, remaining) };
}

/**
 * CANNBot Pro — VK → JWT authentication module.
 *
 * Separate from cannbot auth so that the two extensions
 * maintain independent JWT caches, even when running side-by-side.
 */

import * as vscode from 'vscode';
import * as crypto from 'crypto';
import * as os from 'os';

// ── Constants ───────────────────────────────────────────────────────────
const AUTH_URL = 'https://cannbot.hicann.cn/cannbot/api/auth/authenticate';
const REFRESH_BEFORE_SEC = 60;
const DEFAULT_EXPIRES_IN = 3600;

// ── JWT Cache (keyed by VK prefix so multiple VKs coexist cleanly) ───────
interface JwtEntry {
  vkPrefix: string;
  accessToken: string;
  expiresAt: number;
}

let _jwtCache: JwtEntry | undefined;

// ── Helpers ─────────────────────────────────────────────────────────────
function vkPrefix(vk: string): string {
  return vk.slice(0, Math.min(vk.length, 12));
}

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

function jwtIsValid(vk: string): boolean {
  return !!(
    _jwtCache &&
    _jwtCache.vkPrefix === vkPrefix(vk) &&
    _jwtCache.accessToken &&
    _jwtCache.expiresAt > Math.floor(Date.now() / 1000) + REFRESH_BEFORE_SEC
  );
}

// ── Public API ──────────────────────────────────────────────────────────
export async function getJwt(vk: string): Promise<string> {
  if (!vk || !vk.startsWith('vk-')) {
    throw new Error('Invalid Virtual Key. Must start with "vk-".');
  }

  if (jwtIsValid(vk)) {
    return _jwtCache!.accessToken;
  }

  return exchangeVkForJwt(vk);
}

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
    vkPrefix: vkPrefix(vk),
    accessToken,
    expiresAt: Math.floor(Date.now() / 1000) + expiresIn - REFRESH_BEFORE_SEC,
  };

  return accessToken;
}

export function clearJwtCache(): void {
  _jwtCache = undefined;
}

export function jwtStatus(): { valid: boolean; expiresInSec: number } {
  if (!_jwtCache) {
    return { valid: false, expiresInSec: 0 };
  }
  const remaining = _jwtCache.expiresAt - Math.floor(Date.now() / 1000);
  return { valid: remaining > 0, expiresInSec: Math.max(0, remaining) };
}

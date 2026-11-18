/**
 * CANNBOT Gateway plugin for OpenCode (V2 plugin API)
 *
 * Registers providers: CANNBOT (standard) and CANNBOT-Pro.
 * Connect each provider with its own Virtual Key (VK) through /connect.
 *
 * Requires OpenCode 2.x (`export default { id, setup }`).
 * OpenCode 1.18.x needs the V1 plugin (cannbot-auth.js / cannbot-pro-auth.js).
 */

import { homedir, networkInterfaces } from "node:os";
import { join } from "node:path";
import { readFileSync, writeFileSync, mkdirSync, existsSync, appendFileSync } from "node:fs";

const CANNBOT_DIR = join(homedir(), ".cannbot");
const DATA_DIR = join(process.env.XDG_DATA_HOME || join(homedir(), ".local", "share"), "opencode");
const AUTH_JSON_PATH = join(DATA_DIR, "auth.json");
const LOG_DIR = join(DATA_DIR, "log");
const LOG_PATH = join(LOG_DIR, "cannbot-auth-plugin.log");

const HOST = process.env.CANNBOT_HOST || "https://cannbot.hicann.cn";
const GATEWAY_URL = process.env.CANNBOT_GATEWAY_URL || `${HOST}/gateway/compatible-mode/v1`;
const MODELS_API_URL = process.env.CANNBOT_MODELS_URL || `${HOST}/cannbot/api/models/list`;
const AUTHENTICATE_URL = process.env.CANNBOT_AUTH_URL || `${HOST}/cannbot/api/auth/authenticate`;

const PACKAGE = "@opencode/ai/providers/openai-compatible";
const MODELS_FETCH_TIMEOUT_MS = 2000;
const MAX_DURATION_SECONDS = 30 * 24 * 3600;
const MAX_REASONABLE_DURATION_S = 365 * 24 * 3600;
const nowSeconds = () => Math.floor(Date.now() / 1000);

const PROVIDERS = [
  {
    id: "cannbot",
    name: "CANNBOT",
    label: "CANNBOT Virtual Key (VK)",
    jwtFile: "jwt.json",
    modelsCacheFile: "models-cache.json",
    envKeys: ["CANNBOT_VK"],
    authJsonKeys: ["cannbot", "cannbot-vk", "cannbot-cli"],
    models: {
      "deepseek-v4-pro": { name: "DeepSeek V4 Pro", family: "deepseek", context: 1048576, output: 393216 },
      "glm-5.2": { name: "GLM 5.2", family: "glm", context: 1048576, output: 131072 },
      "glm-5.1": { name: "GLM 5.1", family: "glm", context: 206848, output: 131072 },
      "glm-5": { name: "GLM 5", family: "glm", context: 169984, output: 131072 },
      "qwen3.7-max": { name: "Qwen 3.7 Max", family: "qwen", context: 1048576, output: 65535 },
      "qwen3.6-plus": { name: "Qwen 3.6 Plus", family: "qwen", context: 1048576, output: 65535 },
    },
  },
  {
    id: "cannbot-pro",
    name: "CANNBOT-Pro",
    label: "CANNBOT-Pro Virtual Key (VK)",
    jwtFile: "jwt-pro.json",
    modelsCacheFile: "models-cache-pro.json",
    envKeys: ["CANNBOT_PRO_VK", "CANNBOT_VK"],
    authJsonKeys: ["cannbot-pro", "cannbot-pro-cli", "cannbot-cli", "cannbot", "cannbot-vk"],
    models: {
      "claude-opus-4-8": { name: "Claude Opus 4.8", family: "claude", context: 1048576, output: 131072 },
      "gpt-5.4": { name: "GPT 5.4", family: "gpt", context: 1048576, output: 128000 },
    },
    knownOnly: true,
  },
];

let logReady = false;
function log(msg) {
  try {
    if (!logReady) {
      if (!existsSync(LOG_DIR)) mkdirSync(LOG_DIR, { recursive: true });
      logReady = true;
    }
    appendFileSync(LOG_PATH, `[${new Date().toISOString()}] ${msg}\n`);
  } catch {}
}

function ensureCannbotDir() {
  if (!existsSync(CANNBOT_DIR)) mkdirSync(CANNBOT_DIR, { recursive: true });
}

function toUnixExpires(expiresIn) {
  const now = nowSeconds();
  if (typeof expiresIn !== "number" || !Number.isFinite(expiresIn)) return now + 3600;
  let duration;
  if (expiresIn > 1e12) duration = Math.floor(expiresIn / 1000) - now;
  else if (expiresIn > MAX_DURATION_SECONDS) duration = Math.floor(expiresIn / 1000);
  else duration = expiresIn;
  if (duration <= 0 || duration > MAX_REASONABLE_DURATION_S) return now + 3600;
  return now + duration;
}

function getMac() {
  try {
    for (const ifaces of Object.values(networkInterfaces())) {
      for (const iface of ifaces || []) {
        if (iface.mac && iface.mac !== "00:00:00:00:00:00" && !iface.internal) return iface.mac;
      }
    }
  } catch {}
  return "00:00:00:00:00:00";
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return null;
  }
}

function writeJson(path, value) {
  try {
    ensureCannbotDir();
    writeFileSync(path, JSON.stringify(value, null, 2));
  } catch (e) {
    log(`write ${path} failed: ${e?.message}`);
  }
}

function jwtIsValid(entry) {
  if (!entry?.access || typeof entry.expires !== "number") return false;
  const now = nowSeconds();
  return entry.expires > now + 60 && entry.expires < now + MAX_REASONABLE_DURATION_S;
}

function legacyVk(keys) {
  const auth = readJson(AUTH_JSON_PATH);
  if (!auth) return null;
  for (const key of keys) {
    const entry = auth[key];
    if (entry?.type === "api" && entry.key) return entry.key;
  }
  return null;
}

function legacyJwt(keys) {
  const auth = readJson(AUTH_JSON_PATH);
  if (!auth) return null;
  for (const key of keys) {
    const entry = auth[key];
    if (entry?.type === "oauth" && entry.access && jwtIsValid({ access: entry.access, expires: entry.expires })) {
      return { access: entry.access, expires: entry.expires };
    }
  }
  return null;
}

function toModelInfo(providerId, id, name, family, context, output) {
  return {
    id,
    modelID: id,
    providerID: providerId,
    type: "chat",
    family,
    name,
    capabilities: {
      tools: true,
      input: ["text", "image"],
      output: ["text"],
      temperature: true,
    },
    variants: [],
    time: { released: 0 },
    cost: [],
    status: "active",
    enabled: true,
    limit: { context, output },
  };
}

function buildModels(provider, source) {
  return Object.entries(source)
    .filter(([, info]) => info && info.context)
    .map(([id, info]) =>
      toModelInfo(provider.id, id, info.name || id, info.family || "cannbot", info.context, info.output),
    );
}

function loadModelsCached(provider) {
  const cache = readJson(join(CANNBOT_DIR, provider.modelsCacheFile));
  if (cache?.models && typeof cache.models === "object" && Object.keys(cache.models).length > 0) {
    return buildModels(provider, cache.models);
  }
  return buildModels(provider, provider.models);
}

function modelsFromApi(provider, apiModels) {
  const known = provider.models;
  const source = {};
  for (const m of apiModels) {
    if (!m?.model) continue;
    if (provider.knownOnly && !known[m.model]) continue;
    source[m.model] = {
      name: m.title || known[m.model]?.name || m.model,
      family: known[m.model]?.family || "cannbot",
      context: m.contextLength || known[m.model]?.context || 131072,
      output: m.maxTokens || known[m.model]?.output || 8192,
    };
  }
  return source;
}

async function fetchApiModels(provider, jwt) {
  if (!jwt) return null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), MODELS_FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(`${MODELS_API_URL}?page=1&size=100`, {
      headers: { Authorization: `Bearer ${jwt}` },
      signal: controller.signal,
    });
    if (!res.ok) return null;
    const json = await res.json();
    const active = (json.models || []).filter((m) => m.status === 1);
    return active.length > 0 ? active : null;
  } catch (e) {
    log(`[${provider.id}] models fetch failed: ${e?.message}`);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function exchangeVkForJwt(provider, vk) {
  try {
    const res = await fetch(AUTHENTICATE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-api-vkey": vk },
      body: JSON.stringify({ type: "cli", mac: getMac() }),
    });
    if (!res.ok) {
      log(`[${provider.id}] VK->JWT exchange failed: HTTP ${res.status}`);
      return null;
    }
    const json = await res.json();
    if (!json?.accessToken) {
      log(`[${provider.id}] VK->JWT exchange returned no accessToken`);
      return null;
    }
    return json;
  } catch (e) {
    log(`[${provider.id}] VK->JWT exchange error: ${e?.message}`);
    return null;
  }
}

async function activeConnection(ctx, provider) {
  try {
    return await ctx.integration.connection.active(provider.id);
  } catch {
    return undefined;
  }
}

async function resolveVk(ctx, provider) {
  const connection = await activeConnection(ctx, provider);
  if (connection) {
    try {
      const credential = await ctx.integration.connection.resolve(connection);
      if (credential?.type === "key" && credential.key) return credential.key;
    } catch (e) {
      log(`[${provider.id}] credential lookup failed: ${e?.message}`);
    }
  }
  for (const name of provider.envKeys) {
    if (process.env[name]) return process.env[name];
  }
  return legacyVk(provider.authJsonKeys);
}

async function ensureAuth(ctx, provider, state) {
  const vk = await resolveVk(ctx, provider);
  if (!vk) return null;

  if (state.vk !== vk) {
    state.vk = vk;
    state.jwt = null;
  }
  if (state.jwt) return state;

  const jwtPath = join(CANNBOT_DIR, provider.jwtFile);
  const cached = readJson(jwtPath);
  if (jwtIsValid(cached) && cached.refresh === vk) {
    state.jwt = cached.access;
    return state;
  }

  const legacy = legacyJwt(provider.authJsonKeys);
  if (legacy) {
    state.jwt = legacy.access;
    writeJson(jwtPath, { access: legacy.access, refresh: vk, expires: legacy.expires });
    return state;
  }

  const result = await exchangeVkForJwt(provider, vk);
  if (!result) return state;
  state.jwt = result.accessToken;
  writeJson(jwtPath, {
    access: result.accessToken,
    refresh: vk,
    expires: toUnixExpires(result.expiresIn),
  });
  return state;
}

async function refreshModels(ctx, provider, state, jwt) {
  const apiModels = await fetchApiModels(provider, jwt);
  if (!apiModels) return;
  const source = modelsFromApi(provider, apiModels);
  if (Object.keys(source).length === 0) return;
  writeJson(join(CANNBOT_DIR, provider.modelsCacheFile), { ts: Date.now(), models: source });
  state.models = buildModels(provider, source);
  try {
    await ctx.provider.reload();
  } catch (e) {
    log(`[${provider.id}] provider reload failed: ${e?.message}`);
  }
}

function setHeader(headers, name, value) {
  if (!headers) return;
  if (typeof headers.set === "function") headers.set(name, value);
  else headers[name] = value;
}

async function seedCredential(ctx, provider) {
  if (await activeConnection(ctx, provider)) return;
  let vk = null;
  for (const name of provider.envKeys) {
    if (process.env[name]) {
      vk = process.env[name];
      break;
    }
  }
  vk = vk || legacyVk(provider.authJsonKeys);
  if (!vk) return;
  try {
    await ctx.integration.connect.key({ integrationID: provider.id, key: vk });
    log(`[${provider.id}] seeded credential from environment or legacy auth.json`);
  } catch (e) {
    log(`[${provider.id}] credential seeding failed: ${e?.message}`);
  }
}

export default {
  id: "cannbot",
  async setup(ctx) {
    for (const provider of PROVIDERS) {
      const state = { vk: null, jwt: null, models: loadModelsCached(provider), refreshed: false };

      await ctx.provider.transform((editor) => {
        editor.add({
          info: {
            id: provider.id,
            name: provider.name,
            activation: "auto",
            package: PACKAGE,
            settings: { baseURL: GATEWAY_URL },
          },
          models: state.models,
        });
      });

      await ctx.integration.transform((editor) => {
        editor.update(provider.id, (integration) => {
          integration.name = provider.name;
        });
        editor.method.update({
          integrationID: provider.id,
          method: { type: "key", label: provider.label },
        });
      });

      await seedCredential(ctx, provider);

      await ctx.session.hook(
        "model.request",
        async (event) => {
          const auth = await ensureAuth(ctx, provider, state);
          if (!auth) {
            log(`[${provider.id}] no VK available for this request`);
            return;
          }
          if (auth.vk) setHeader(event.headers, "x-api-vkey", auth.vk);
          if (auth.jwt) setHeader(event.headers, "authorization", `Bearer ${auth.jwt}`);
          if (!state.refreshed) {
            state.refreshed = true;
            await refreshModels(ctx, provider, state, auth.jwt);
          }
        },
        { providerID: provider.id },
      );

      await ctx.session.hook(
        "http.request",
        async (event) => {
          const auth = await ensureAuth(ctx, provider, state);
          if (!auth?.jwt) return;
          setHeader(event.request?.headers, "authorization", `Bearer ${auth.jwt}`);
          if (auth.vk) setHeader(event.request?.headers, "x-api-vkey", auth.vk);
        },
        { providerID: provider.id },
      );

      log(`[${provider.id}] registered (models=${state.models.length}, baseURL=${GATEWAY_URL})`);
    }
  },
};

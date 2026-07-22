/**
 * CANNBOT-Pro Gateway Auth Plugin for OpenCode
 *
 * Registers provider: CANNBOT-Pro
 * Models: Claude Opus 4.8, GPT 5.4 (beta access only)
 *
 * Install alongside cannbot-auth.js (standard models).
 * Each provider has its own VK: run `/connect` and select CANNBOT-Pro.
 */

import { homedir, networkInterfaces } from "os";
import { join } from "path";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";

const PROVIDER_ID = "cannbot-pro";
const PROVIDER_NAME = "CANNBOT-Pro";
const GATEWAY_URL = "https://cannbot.hicann.cn/gateway/compatible-mode/v1";
const MODELS_API_URL = "https://cannbot.hicann.cn/cannbot/api/models/list";
const AUTHENTICATE_URL = "https://cannbot.hicann.cn/cannbot/api/auth/authenticate";
const CANNBOT_DIR = join(homedir(), ".cannbot");
const JWT_CACHE_PATH = join(CANNBOT_DIR, "jwt-pro.json");
const MODELS_CACHE_PATH = join(CANNBOT_DIR, "models-cache-pro.json");
const MODELS_FETCH_TIMEOUT_MS = 1500;

const DATA_DIR = join(process.env.XDG_DATA_HOME || join(homedir(), ".local", "share"), "opencode");
const AUTH_JSON_PATH = join(DATA_DIR, "auth.json");
const LOG_DIR = join(DATA_DIR, "log");
const DEBUG_LOG_PATH = join(LOG_DIR, "cannbot-auth-plugin.log");

let logDirReady = false;
function debugLog(msg) {
  try {
    if (!logDirReady) {
      if (!existsSync(LOG_DIR)) mkdirSync(LOG_DIR, { recursive: true });
      logDirReady = true;
    }
    writeFileSync(DEBUG_LOG_PATH, `[${new Date().toISOString()}] ${msg}\n`, { flag: "a" });
  } catch {}
}

const MAX_DURATION_SECONDS = 30 * 24 * 3600;
const MAX_REASONABLE_DURATION_S = 365 * 24 * 3600;
const CURRENT_EPOCH_SECONDS = () => Math.floor(Date.now() / 1000);

function toUnixExpires(expiresIn) {
  const now = CURRENT_EPOCH_SECONDS();
  if (typeof expiresIn !== "number" || !Number.isFinite(expiresIn)) return now + 3600;
  let durationSeconds;
  if (expiresIn > 1e12) durationSeconds = Math.floor(expiresIn / 1000) - now;
  else if (expiresIn > MAX_DURATION_SECONDS) durationSeconds = Math.floor(expiresIn / 1000);
  else durationSeconds = expiresIn;
  if (durationSeconds <= 0 || durationSeconds > MAX_REASONABLE_DURATION_S) return now + 3600;
  return now + durationSeconds;
}

function ensureCannbotDir() {
  if (!existsSync(CANNBOT_DIR)) mkdirSync(CANNBOT_DIR, { recursive: true });
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

function readAccessTokenFromAuthJson(keys) {
  try {
    const authJson = JSON.parse(readFileSync(AUTH_JSON_PATH, "utf-8"));
    for (const key of keys) {
      const entry = authJson[key];
      if (entry?.type === "oauth" && entry.access) return entry.access;
    }
  } catch {}
  return null;
}

function readJwtCache() {
  try {
    const j = JSON.parse(readFileSync(JWT_CACHE_PATH, "utf-8"));
    if (j?.access) return j;
  } catch {}
  return null;
}

function writeJwtCache(obj) {
  try {
    ensureCannbotDir();
    writeFileSync(JWT_CACHE_PATH, JSON.stringify(obj, null, 2));
  } catch (e) { debugLog("[cannbot-pro] writeJwtCache failed: " + e?.message); }
}

function jwtIsValid(entry) {
  if (!entry?.access || typeof entry.expires !== "number") return false;
  const now = CURRENT_EPOCH_SECONDS();
  return entry.expires > now + 60 && entry.expires < now + MAX_REASONABLE_DURATION_S;
}

async function exchangeVkJwt(vk) {
  try {
    const mac = getMac();
    const res = await fetch(AUTHENTICATE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-api-vkey": vk },
      body: JSON.stringify({ type: "cli", mac }),
    });
    if (!res.ok) { debugLog("[cannbot-pro] VK->JWT exchange failed: " + res.status); return null; }
    const json = await res.json();
    debugLog("[cannbot-pro] VK->JWT exchange OK, expiresIn=" + json.expiresIn);
    return json;
  } catch (e) { debugLog("[cannbot-pro] VK->JWT exchange error: " + e?.message); return null; }
}

const CAPABILITIES = {
  temperature: true, reasoning: true, attachment: true, toolcall: true,
  input: { text: true, audio: false, image: true, video: false, pdf: false },
  output: { text: true, audio: false, image: false, video: false, pdf: false },
  interleaved: false,
};
const LIMIT = { context: 131072, output: 8192 };
const COST = { input: 0, output: 0, cache: { read: 0, write: 0 } };

const KNOWN_MODELS = {
  "claude-opus-4-8": { name: "Claude Opus 4.8", family: "claude", context: 1048576, output: 131072 },
  "gpt-5.4":         { name: "GPT 5.4",         family: "gpt",    context: 1048576, output: 128000 },
};

function buildModels() {
  return Object.fromEntries(
    Object.entries(KNOWN_MODELS).map(([id, info]) => [
      id,
      {
        id, name: info.name, family: info.family,
        api: { id, url: GATEWAY_URL, npm: "@ai-sdk/openai-compatible" },
        capabilities: { ...CAPABILITIES },
        limit: { context: info.context ?? LIMIT.context, output: info.output ?? LIMIT.output },
        cost: { input: COST.input, output: COST.output, cache: { ...COST.cache } },
        status: "active", options: {}, headers: {}, release_date: "",
      },
    ]),
  );
}

function modelsFromApiList(apiModels) {
  const known = new Set(Object.keys(KNOWN_MODELS));
  return Object.fromEntries(
    apiModels
      .filter((m) => known.has(m.model))
      .map((m) => [
        m.model,
        {
          id: m.model, name: m.title, family: "cannbot",
          api: { id: m.model, url: GATEWAY_URL, npm: "@ai-sdk/openai-compatible" },
          capabilities: { ...CAPABILITIES },
          limit: { context: m.contextLength, output: m.maxTokens },
          cost: { input: COST.input, output: COST.output, cache: { ...COST.cache } },
          status: "active", options: {}, headers: {}, release_date: "",
        },
      ]),
  );
}

async function fetchModelsFromAPI() {
  const token = cachedJwt || readJwtCache()?.access || readAccessTokenFromAuthJson(["cannbot-pro", "cannbot-pro-cli", "cannbot-cli", "cannbot-vk", "cannbot"]);
  if (!token) return null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), MODELS_FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(`${MODELS_API_URL}?page=1&size=100`, {
      headers: { Authorization: `Bearer ${token}` }, signal: controller.signal,
    });
    if (!res.ok) return null;
    const json = await res.json();
    const active = json.models?.filter((m) => m.status === 1) ?? [];
    return active.length > 0 ? active : null;
  } catch { return null; } finally { clearTimeout(timer); }
}

function readModelsCache() {
  try {
    const cache = JSON.parse(readFileSync(MODELS_CACHE_PATH, "utf-8"));
    if (cache && cache.models && Object.keys(cache.models).length > 0) return cache;
  } catch {}
  return null;
}

function writeModelsCache(models) {
  try {
    ensureCannbotDir();
    writeFileSync(MODELS_CACHE_PATH, JSON.stringify({ ts: Date.now(), models }, null, 2));
  } catch (e) { debugLog("[cannbot-pro] writeModelsCache failed: " + e?.message); }
}

function loadModelsFast() {
  const cache = readModelsCache();
  if (cache) return cache.models;
  return buildModels();
}

async function refreshModelsCache() {
  try {
    const apiModels = await fetchModelsFromAPI();
    if (apiModels && apiModels.length > 0) {
      writeModelsCache(modelsFromApiList(apiModels));
      debugLog("[cannbot-pro] models cache refreshed (" + apiModels.length + " pro models)");
    }
  } catch (e) { debugLog("[cannbot-pro] refreshModelsCache failed: " + e?.message); }
}

let cachedVKey = null;
let cachedJwt = null;

export default async function () {
  return {
    config: async function (cfg) {
      cfg.provider = cfg.provider ?? {};
      cfg.provider[PROVIDER_ID] = {
        name: PROVIDER_NAME,
        npm: "@ai-sdk/openai-compatible",
        options: { baseURL: GATEWAY_URL },
        models: loadModelsFast(),
      };
      refreshModelsCache();
    },

    auth: {
      provider: PROVIDER_ID,
      methods: [
        {
          type: "api",
          label: "CANNBOT-Pro Virtual Key (VK)",
          async authorize(inputs) { return { type: "success", key: inputs?.key ?? "" }; },
        },
      ],
      async loader(getAuth) {
        debugLog("[cannbot-pro] === auth.loader start ===");
        const info = await getAuth();
        debugLog(`[cannbot-pro] getAuth(): type=${info?.type}, hasKey=${!!info?.key}`);

        let vk = null;
        if (info?.type === "api" && info.key) {
          vk = info.key;
          debugLog("[cannbot-pro] Scenario A: got VK from getAuth()");
        }

        if (!vk) {
          try {
            const authJson = JSON.parse(readFileSync(AUTH_JSON_PATH, "utf-8"));
            const entry = authJson["cannbot-pro"];
            if (entry?.type === "api" && entry.key) {
              vk = entry.key;
              debugLog("[cannbot-pro] Scenario B: got VK from auth.json[cannbot-pro]");
            }
          } catch (e) { debugLog("[cannbot-pro] read auth.json failed: " + e?.message); }
        }

        cachedVKey = vk || null;

        if (vk) {
          let entry = readJwtCache();
          if (!jwtIsValid(entry)) {
            try {
              const authJson = JSON.parse(readFileSync(AUTH_JSON_PATH, "utf-8"));
              const cli = authJson["cannbot-pro-cli"];
              if (cli?.type === "oauth" && jwtIsValid({ access: cli.access, expires: cli.expires })) {
                entry = { access: cli.access, expires: cli.expires, refresh: vk };
                writeJwtCache(entry);
                debugLog("[cannbot-pro] Migrated JWT from auth.json into jwt cache");
              }
            } catch {}
          }

          if (jwtIsValid(entry)) {
            cachedJwt = entry.access;
            debugLog("[cannbot-pro] Valid JWT from cache");
          } else {
            debugLog("[cannbot-pro] No valid JWT, exchanging VK...");
            const result = await exchangeVkJwt(vk);
            if (result?.accessToken) {
              cachedJwt = result.accessToken;
              writeJwtCache({
                access: result.accessToken, refresh: vk,
                expires: toUnixExpires(result.expiresIn),
              });
              debugLog("[cannbot-pro] JWT stored to jwt cache");
            } else {
              cachedJwt = null;
              debugLog("[cannbot-pro] VK->JWT exchange failed");
            }
          }
        }

        debugLog(`[cannbot-pro] cachedVKey: ${cachedVKey ? cachedVKey.substring(0, 8) + "..." : "null"}`);
        debugLog(`[cannbot-pro] cachedJwt: ${cachedJwt ? cachedJwt.substring(0, 20) + "..." : "null"}`);
        debugLog("[cannbot-pro] === auth.loader done ===");
        return {};
      },
    },

    "chat.headers": async function (input, output) {
      if (input.model.providerID !== PROVIDER_ID) return;
      if (cachedVKey) output.headers["x-api-vkey"] = cachedVKey;
      const bearerToken = cachedJwt || readJwtCache()?.access || readAccessTokenFromAuthJson(["cannbot-pro", "cannbot-pro-cli", "cannbot-cli"]);
      if (bearerToken) {
        output.headers["Authorization"] = `Bearer ${bearerToken}`;
        debugLog("[cannbot-pro] chat.headers: injected Authorization: Bearer");
      }
    },
  };
}

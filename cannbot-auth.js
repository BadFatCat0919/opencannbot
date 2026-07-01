/**
 * CANNBOT Gateway Auth Plugin for OpenCode
 */

import { homedir, networkInterfaces } from "os";
import { join } from "path";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";

const PROVIDER_ID = "cannbot";
const GATEWAY_URL = "https://cannbot.hicann.cn/gateway/compatible-mode/v1";
const MODELS_API_URL = "https://cannbot.hicann.cn/cannbot/api/models/list";
const AUTHENTICATE_URL = "https://cannbot.hicann.cn/cannbot/api/auth/authenticate";
const CANNBOT_DIR = join(homedir(), ".cannbot");
const JWT_CACHE_PATH = join(CANNBOT_DIR, "jwt.json");
const MODELS_CACHE_PATH = join(CANNBOT_DIR, "models-cache.json");
const MODELS_FETCH_TIMEOUT_MS = 1500; // never block startup on a slow network

// opencode's data dir; respects XDG_DATA_HOME on every platform (incl. Windows).
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

const MAX_DURATION_SECONDS = 30 * 24 * 3600; // 30 days — beyond this, assume milliseconds
const MAX_REASONABLE_DURATION_S = 365 * 24 * 3600; // 1 year — cap for sanity
const CURRENT_EPOCH_SECONDS = () => Math.floor(Date.now() / 1000);

function toUnixExpires(expiresIn) {
  const now = CURRENT_EPOCH_SECONDS();
  if (typeof expiresIn !== "number" || !Number.isFinite(expiresIn)) return now + 3600;

  let durationSeconds;
  if (expiresIn > 1e12) {
    durationSeconds = Math.floor(expiresIn / 1000) - now; // ms epoch → s relative
  } else if (expiresIn > MAX_DURATION_SECONDS) {
    durationSeconds = Math.floor(expiresIn / 1000); // duration in ms, not seconds
  } else {
    durationSeconds = expiresIn;
  }

  if (durationSeconds <= 0 || durationSeconds > MAX_REASONABLE_DURATION_S) return now + 3600;
  return now + durationSeconds;
}

// JWT cache lives in ~/.cannbot/ (our own dir) so opencode rewriting auth.json
// can never clobber it and force a needless re-exchange. Shape: { access, expires, refresh }.
function readJwtCache() {
  try {
    const j = JSON.parse(readFileSync(JWT_CACHE_PATH, "utf-8"));
    if (j?.access) return j;
  } catch {}
  return null;
}

function ensureCannbotDir() {
  if (!existsSync(CANNBOT_DIR)) mkdirSync(CANNBOT_DIR, { recursive: true });
}

function writeJwtCache(obj) {
  try {
    ensureCannbotDir();
    writeFileSync(JWT_CACHE_PATH, JSON.stringify(obj, null, 2));
  } catch (e) {
    debugLog("writeJwtCache failed: " + e?.message);
  }
}

// A JWT is usable if it exists and won't expire within the next minute.
function jwtIsValid(entry) {
  if (!entry?.access || typeof entry.expires !== "number") return false;
  const now = CURRENT_EPOCH_SECONDS();
  return entry.expires > now + 60 && entry.expires < now + MAX_REASONABLE_DURATION_S;
}

function readAccessTokenFromAuthJson() {
  try {
    const authJson = JSON.parse(readFileSync(AUTH_JSON_PATH, "utf-8"));
    const entry = authJson["cannbot-cli"];
    if (entry?.type === "oauth" && entry.access) {
      return entry.access;
    }
  } catch {}
  return null;
}

const CAPABILITIES = {
  temperature: true,
  reasoning: true,
  attachment: true,
  toolcall: true,
  input: { text: true, audio: false, image: true, video: false, pdf: false },
  output: { text: true, audio: false, image: false, video: false, pdf: false },
  interleaved: false,
};

const LIMIT = { context: 131072, output: 8192 };
const COST = { input: 0, output: 0, cache: { read: 0, write: 0 } };

const KNOWN_MODELS = {
  "claude-opus-4-8": { name: "Claude Opus 4.8", family: "claude", context: 1048576, output: 131072 },
  "gpt-5.4": { name: "GPT 5.4", family: "gpt", context: 1048576, output: 128000 },
  "deepseek-v4-pro": { name: "DeepSeek V4 Pro", family: "deepseek", context: 1048576, output: 393216 },
  "glm-5.2": { name: "GLM 5.2", family: "glm", context: 1048576, output: 131072 },
  "glm-5.1": { name: "GLM 5.1", family: "glm", context: 206848, output: 131072 },
  "glm-5": { name: "GLM 5", family: "glm", context: 169984, output: 131072 },
  "qwen3.7-max": { name: "Qwen 3.7 Max", family: "qwen", context: 1048576, output: 65535 },
  "qwen3.6-plus": { name: "Qwen 3.6 Plus", family: "qwen", context: 1048576, output: 65535 },
};

function buildModels() {
  return Object.fromEntries(
    Object.entries(KNOWN_MODELS).map(([id, info]) => [
      id,
      {
        id,
        name: info.name,
        family: info.family,
        api: { id, url: GATEWAY_URL, npm: "@ai-sdk/openai-compatible" },
        capabilities: { ...CAPABILITIES },
        limit: { context: info.context ?? LIMIT.context, output: info.output ?? LIMIT.output },
        cost: { input: COST.input, output: COST.output, cache: { ...COST.cache } },
        status: "active",
        options: {},
        headers: {},
        release_date: "",
      },
    ]),
  );
}

async function fetchModelsFromAPI() {
  const token = cachedJwt || readJwtCache()?.access || readAccessTokenFromAuthJson();
  if (!token) return null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), MODELS_FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(`${MODELS_API_URL}?page=1&size=100`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    });
    if (!res.ok) return null;
    const json = await res.json();
    const active = json.models?.filter((m) => m.status === 1) ?? [];
    return active.length > 0 ? active : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function modelsFromApiList(apiModels) {
  return Object.fromEntries(
    apiModels.map((m) => {
      const id = m.model;
      return [
        id,
        {
          id,
          name: m.title,
          family: "cannbot",
          api: { id, url: GATEWAY_URL, npm: "@ai-sdk/openai-compatible" },
          capabilities: { ...CAPABILITIES },
          limit: { context: m.contextLength, output: m.maxTokens },
          cost: { input: COST.input, output: COST.output, cache: { ...COST.cache } },
          status: "active",
          options: {},
          headers: {},
          release_date: "",
        },
      ];
    }),
  );
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
  } catch (e) {
    debugLog("writeModelsCache failed: " + e?.message);
  }
}

// Synchronous, never blocks startup: prefer disk cache, fall back to the static list.
function loadModelsFast() {
  const cache = readModelsCache();
  if (cache) return cache.models;
  return buildModels();
}

// Fire-and-forget: refresh the on-disk cache on every startup so the next launch sees fresh models.
async function refreshModelsCache() {
  try {
    const apiModels = await fetchModelsFromAPI();
    if (apiModels && apiModels.length > 0) {
      writeModelsCache(modelsFromApiList(apiModels));
      debugLog("models cache refreshed (" + apiModels.length + " models)");
    }
  } catch (e) {
    debugLog("refreshModelsCache failed: " + e?.message);
  }
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

let cachedVKey = null;
let cachedJwt = null;

async function exchangeVkJwt(vk) {
  try {
    const mac = getMac();
    const res = await fetch(AUTHENTICATE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-api-vkey": vk },
      body: JSON.stringify({ type: "cli", mac }),
    });
    if (!res.ok) {
      debugLog("VK->JWT exchange failed: " + res.status);
      return null;
    }
    const json = await res.json();
    debugLog("VK->JWT exchange OK, expiresIn=" + json.expiresIn);
    return json;
  } catch (e) {
    debugLog("VK->JWT exchange error: " + e?.message);
    return null;
  }
}

export default async function () {
  return {
    config: async function (cfg) {
      cfg.provider = cfg.provider ?? {};
      cfg.provider[PROVIDER_ID] = {
        name: "CANNBOT",
        npm: "@ai-sdk/openai-compatible",
        options: { baseURL: GATEWAY_URL },
        models: loadModelsFast(), // synchronous: cache or static list, no network
      };
      // Refresh the cache in the background; next startup picks up any changes.
      refreshModelsCache();
    },

    auth: {
      provider: PROVIDER_ID,
      methods: [
        {
          type: "api",
          label: "CANNBOT Virtual Key (VK)",
          async authorize(inputs) {
            return { type: "success", key: inputs?.key ?? "" };
          },
        },
      ],
      async loader(getAuth) {
        debugLog("=== auth.loader start ===");
        const info = await getAuth();
        debugLog(`getAuth(): type=${info?.type}, hasKey=${!!info?.key}, keyPreview=${info?.key?.substring(0, 8) || "none"}`);

        let vk = null;
        if (info?.type === "api" && info.key) {
          vk = info.key;
          debugLog("Scenario A: got VK from getAuth()");
        }

        if (!vk) {
          try {
            const authJson = JSON.parse(readFileSync(AUTH_JSON_PATH, "utf-8"));
            const entry = authJson["cannbot-vk"] || authJson["cannbot"];
            if (entry?.type === "api" && entry.key) {
              vk = entry.key;
              debugLog("Scenario B: got VK from auth.json");
            }
          } catch (e) {
            debugLog("read auth.json failed: " + e?.message);
          }
        }

        cachedVKey = vk || null;

        if (vk) {
          // Source of truth: ~/.cannbot/jwt.json. Fall back to a one-time migration
          // from auth.json for users upgrading from the old behaviour.
          let entry = readJwtCache();
          if (!jwtIsValid(entry)) {
            try {
              const authJson = JSON.parse(readFileSync(AUTH_JSON_PATH, "utf-8"));
              const cli = authJson["cannbot-cli"];
              if (cli?.type === "oauth" && jwtIsValid({ access: cli.access, expires: cli.expires })) {
                entry = { access: cli.access, expires: cli.expires, refresh: vk };
                writeJwtCache(entry);
                debugLog("Migrated JWT from auth.json into jwt cache");
              }
            } catch {}
          }

          if (jwtIsValid(entry)) {
            cachedJwt = entry.access;
            debugLog("Valid JWT from cache (expires_in=" + (entry.expires - CURRENT_EPOCH_SECONDS()) + "s)");
          } else {
            debugLog("No valid JWT, exchanging VK...");
            const result = await exchangeVkJwt(vk);
            if (result?.accessToken) {
              cachedJwt = result.accessToken;
              writeJwtCache({
                access: result.accessToken,
                refresh: vk,
                expires: toUnixExpires(result.expiresIn),
              });
              debugLog("JWT stored to jwt cache");
            } else {
              cachedJwt = null;
              debugLog("VK->JWT exchange failed, no Bearer token available");
            }
          }
        }

        debugLog(`cachedVKey: ${cachedVKey ? cachedVKey.substring(0, 8) + "..." : "null"}`);
        debugLog(`cachedJwt: ${cachedJwt ? cachedJwt.substring(0, 20) + "..." : "null"}`);
        debugLog("=== auth.loader done ===");
        return {};
      },
    },

    "chat.headers": async function (input, output) {
      if (input.model.providerID !== PROVIDER_ID) return;

      if (cachedVKey) {
        output.headers["x-api-vkey"] = cachedVKey;
      }

      // Prefer the in-memory JWT resolved by the loader; only touch disk if it's
      // somehow absent (e.g. chat.headers fired before loader on a cold path).
      const bearerToken = cachedJwt || readJwtCache()?.access || readAccessTokenFromAuthJson();
      if (bearerToken) {
        output.headers["Authorization"] = `Bearer ${bearerToken}`;
        debugLog("chat.headers: injected Authorization: Bearer");
      }
    },
  };
};

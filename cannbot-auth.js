/**
 * CANNBOT Gateway Auth Plugin for OpenCode
 */

import { homedir } from "os";
import { join } from "path";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";

const PLUGIN_ID = "cannbot-gateway-auth";
const PROVIDER_ID = "cannbot";
const GATEWAY_URL = "https://cannbot.hicann.cn/gateway/compatible-mode/v1";
const SESSION_PATH = join(homedir(), ".cannbot", "session.json");
const MODELS_API_URL = "https://cannbot.hicann.cn/cannbot/api/models/list";

const DEBUG_LOG_PATH = join(homedir(), ".local", "share", "opencode", "log", "cannbot-auth-plugin.log");

function debugLog(msg) {
  try {
    const dir = join(homedir(), ".local", "share", "opencode", "log");
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    const ts = new Date().toISOString();
    writeFileSync(DEBUG_LOG_PATH, `[${ts}] ${msg}\n`, { flag: "a" });
  } catch {}
}

function readSession() {
  try {
    return JSON.parse(readFileSync(SESSION_PATH, "utf-8"));
  } catch {
    return null;
  }
}

function readAccessTokenFromAuthJson() {
  try {
    const XDG = process.env.XDG_DATA_HOME || join(homedir(), ".local", "share");
    const authJsonPath = join(XDG, "opencode", "auth.json");
    const authJson = JSON.parse(readFileSync(authJsonPath, "utf-8"));
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
  "qwen-plus": { name: "Qwen Plus", family: "qwen" },
  "qwen-max": { name: "Qwen Max", family: "qwen" },
  "qwen-turbo": { name: "Qwen Turbo", family: "qwen" },
  "qwen-plus-latest": { name: "Qwen Plus Latest", family: "qwen" },
  "qwen-max-latest": { name: "Qwen Max Latest", family: "qwen" },
  "qwen-turbo-latest": { name: "Qwen Turbo Latest", family: "qwen" },
  "deepseek-v3": { name: "DeepSeek V3", family: "deepseek" },
  "deepseek-r1": { name: "DeepSeek R1", family: "deepseek" },
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
        limit: { ...LIMIT },
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
  const session = readSession();
  const token = session?.accessToken || readAccessTokenFromAuthJson();
  if (!token) return null;
  try {
    const res = await fetch(`${MODELS_API_URL}?page=1&size=100`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return null;
    const json = await res.json();
    const active = json.models?.filter((m) => m.status === 1) ?? [];
    return active.length > 0 ? active : null;
  } catch {
    return null;
  }
}

async function buildModelsDynamic() {
  const apiModels = await fetchModelsFromAPI();
  if (apiModels && apiModels.length > 0) {
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
  return buildModels();
}

let cachedVKey = null;

export default async function (input) {
  return {
    config: async function (cfg) {
      cfg.provider = cfg.provider ?? {};
      cfg.provider[PROVIDER_ID] = {
        name: "CANNBOT",
        npm: "@ai-sdk/openai-compatible",
        options: { baseURL: GATEWAY_URL },
        models: await buildModelsDynamic(),
      };
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
        const info = await getAuth();
        let vk = null;
        if (info?.type === "api" && info.key) {
          vk = info.key;
        }
        if (!vk) {
          try {
            const XDG = process.env.XDG_DATA_HOME || join(homedir(), ".local", "share");
            const authJsonPath = join(XDG, "opencode", "auth.json");
            const authJson = JSON.parse(readFileSync(authJsonPath, "utf-8"));
            const entry = authJson["cannbot-vk"] || authJson["cannbot"];
            if (entry?.type === "api" && entry.key) vk = entry.key;
          } catch {}
        }
        cachedVKey = vk || null;
        return {};
      },
    },

    "chat.headers": async function (input, output) {
      if (input.model.providerID !== PROVIDER_ID) return;

      let vk = cachedVKey;
      if (!vk) {
        try {
          const XDG = process.env.XDG_DATA_HOME || join(homedir(), ".local", "share");
          const authPath = join(XDG, "opencode", "auth.json");
          const authJson = JSON.parse(readFileSync(authPath, "utf-8"));
          const entry = authJson["cannbot-vk"] || authJson["cannbot"];
          vk = (entry?.type === "api" && entry.key) ? entry.key : null;
        } catch {}
      }
      if (vk) output.headers["x-api-vkey"] = vk;

      const session = readSession();
      const bearerToken = session?.accessToken || readAccessTokenFromAuthJson();
      if (bearerToken) output.headers["Authorization"] = `Bearer ${bearerToken}`;
    },
  };
};

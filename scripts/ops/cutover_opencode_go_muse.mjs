import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";

import { EXTENSION_REVISION as CANARY_REVISION } from "./omp-model-routing-observability.js";

const CHANNEL_ID = 48;
const CHANNEL_BASE_URL = "https://opencode.ai/zen/go";
const LUNA_ID = "gpt-5.6-luna";
const STANDARD_MUSE_ID = "muse-spark-1.2";
const MUSE_ID = "muse-spark-1.2-contributor";
const CHANNEL_PRIORITY = 51;
const MUSE_WEIGHT = 12;
const LUNA_WEIGHT = 20;
const PROVIDER = "zg-newapi";
const NEWAPI_BASE = "http://127.0.0.1:3002";
const CANARY_MAX_AGE_MS = 10 * 60 * 1000;
const LUNA_LINES = [
  `    - id: ${LUNA_ID}`,
  "      compactionModel: zg-newapi/deepseek-v4-flash",
  "      name: GPT 5.6 Luna (opencode-go ch48)",
  "      reasoning: true",
  "      contextWindow: 272000",
  "      maxTokens: 128000",
];
const MUSE_LINES = [
  `    - id: ${MUSE_ID}`,
  "      api: openai-responses",
  "      compactionModel: zg-newapi/deepseek-v4-flash",
  "      name: Muse Spark 1.2 Contributor (opencode-go ch48)",
  "      reasoning: true",
  "      input:",
  "      - text",
  "      - image",
  "      contextWindow: 1048576",
  "      maxTokens: 131072",
];

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function timestamp() {
  return new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "");
}

function readJsonFile(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8").replace(/^\uFEFF/, ""));
}

export function validateCanaryState(state, now = Date.now()) {
  const selector = `${PROVIDER}/${MUSE_ID}:max`;
  if (!state || state.revision !== CANARY_REVISION) {
    throw new Error(`Muse finalize requires Canary revision ${CANARY_REVISION}`);
  }
  const proof = state.selectors?.[selector];
  if (!proof || proof.result !== "success") {
    throw new Error(`Muse finalize requires a successful ${selector} Canary`);
  }
  const checkedAt = Number(proof.checkedAt);
  const age = now - checkedAt;
  if (!Number.isFinite(checkedAt) || age < 0 || age > CANARY_MAX_AGE_MS) {
    throw new Error("Muse Canary proof is stale or invalid");
  }
  return { selector, checkedAt, revision: state.revision };
}

export function requireContributorConsent(phase, accepted) {
  if (phase !== "rollback" && accepted !== true) {
    throw new Error("stage/finalize requires --accept-contributor-data-policy");
  }
}

function providerBounds(lines) {
  const start = lines.findIndex((line) => line === `  ${PROVIDER}:`);
  if (start < 0) throw new Error(`provider not found: ${PROVIDER}`);
  let end = lines.findIndex(
    (line, index) => index > start && /^  [^ ].*:$/.test(line),
  );
  if (end < 0) end = lines.length;
  return { start, end };
}

function modelBounds(lines, providerStart, providerEnd, modelId) {
  const marker = `    - id: ${modelId}`;
  const starts = [];
  for (let index = providerStart; index < providerEnd; index += 1) {
    if (lines[index] === marker) starts.push(index);
  }
  if (starts.length > 1) throw new Error(`duplicate model id: ${PROVIDER}/${modelId}`);
  if (starts.length === 0) return null;
  let end = starts[0] + 1;
  while (end < providerEnd && !lines[end].startsWith("    - id: ")) end += 1;
  return { start: starts[0], end };
}

export function transformModelsYaml(source, phase) {
  if (!new Set(["stage", "finalize", "rollback"]).has(phase)) {
    throw new Error(`invalid phase: ${phase}`);
  }
  const newline = source.includes("\r\n") ? "\r\n" : "\n";
  const finalNewline = source.endsWith(newline);
  const lines = source.split(/\r?\n/);
  if (finalNewline) lines.pop();

  let bounds = providerBounds(lines);
  let luna = modelBounds(lines, bounds.start, bounds.end, LUNA_ID);
  let muse = modelBounds(lines, bounds.start, bounds.end, MUSE_ID);
  const standardMuse = modelBounds(
    lines,
    bounds.start,
    bounds.end,
    STANDARD_MUSE_ID,
  );
  if (!luna && !muse && !standardMuse) {
    throw new Error("neither Luna nor Muse exists in zg-newapi");
  }
  if (standardMuse) {
    lines.splice(standardMuse.start, standardMuse.end - standardMuse.start);
    bounds = providerBounds(lines);
    luna = modelBounds(lines, bounds.start, bounds.end, LUNA_ID);
    muse = modelBounds(lines, bounds.start, bounds.end, MUSE_ID);
  }

  if (phase === "rollback") {
    if (!luna) {
      if (!muse) throw new Error("cannot place the Luna rollback model");
      lines.splice(muse.start, 0, ...LUNA_LINES);
      bounds = providerBounds(lines);
      muse = modelBounds(lines, bounds.start, bounds.end, MUSE_ID);
    }
    if (muse) lines.splice(muse.start, muse.end - muse.start);
    const output = lines.join(newline) + (finalNewline ? newline : "");
    return { changed: output !== source, output };
  }

  if (!muse) {
    if (!luna) throw new Error("cannot place Muse without the Luna migration anchor");
    lines.splice(luna.end, 0, ...MUSE_LINES);
  } else {
    const block = lines.slice(muse.start, muse.end).join("\n");
    for (const required of ["api: openai-responses", "contextWindow: 1048576"]) {
      if (!block.includes(required)) throw new Error(`Muse model has incompatible ${required}`);
    }
  }

  if (phase === "finalize") {
    bounds = providerBounds(lines);
    luna = modelBounds(lines, bounds.start, bounds.end, LUNA_ID);
    if (luna) lines.splice(luna.start, luna.end - luna.start);
  }

  const output = lines.join(newline) + (finalNewline ? newline : "");
  return { changed: output !== source, output };
}

export function transformConfigYaml(source, phase) {
  if (phase === "stage") return { changed: false, output: source };
  if (!new Set(["finalize", "rollback"]).has(phase)) {
    throw new Error(`invalid phase: ${phase}`);
  }
  const lunaSelector = `${PROVIDER}/${LUNA_ID}`;
  const museSelector = `${PROVIDER}/${MUSE_ID}`;
  const from = phase === "finalize" ? lunaSelector : museSelector;
  const to = phase === "finalize" ? museSelector : lunaSelector;
  const output = source.replaceAll(from, to);
  if (output.includes(from)) throw new Error(`${phase} selector replacement incomplete`);
  if (phase === "finalize") {
    if (!output.includes(`task: ${museSelector}:max`)) {
      throw new Error("task role did not resolve to Muse max");
    }
    if (!output.includes(`tiny: ${museSelector}`)) {
      throw new Error("tiny role did not resolve to Muse");
    }
  }
  return { changed: output !== source, output };
}

export function planChannel(channel, phase) {
  if (!new Set(["stage", "finalize", "rollback"]).has(phase)) {
    throw new Error(`invalid phase: ${phase}`);
  }
  if (Number(channel.id) !== CHANNEL_ID) throw new Error("refused: unexpected channel id");
  if (channel.base_url !== CHANNEL_BASE_URL) throw new Error("refused: unexpected channel base URL");
  if (Number(channel.type) !== 1) throw new Error("refused: ch48 is not OpenAI-compatible");
  if (Number(channel.status) !== 1) throw new Error("refused: ch48 is not enabled");
  const current = new Set(String(channel.models || "").split(",").filter(Boolean));
  for (const model of current) {
    if (![LUNA_ID, STANDARD_MUSE_ID, MUSE_ID].includes(model)) {
      throw new Error(`refused: ch48 contains unrelated model ${model}`);
    }
  }
  const models =
    phase === "stage"
      ? `${LUNA_ID},${MUSE_ID}`
      : phase === "finalize"
        ? MUSE_ID
        : LUNA_ID;
  return {
    ...channel,
    name:
      phase === "stage"
        ? "opencode-go-muse-canary"
        : phase === "finalize"
          ? "opencode-go-muse"
          : "opencode-go-luna",
    models,
    model_mapping: "{}",
    test_model: phase === "rollback" ? LUNA_ID : MUSE_ID,
    priority: CHANNEL_PRIORITY,
    weight: phase === "rollback" ? LUNA_WEIGHT : MUSE_WEIGHT,
  };
}

function readSecretKey(databasePath) {
  const database = new DatabaseSync(databasePath, { readOnly: true });
  try {
    const row = database
      .prepare("SELECT key, base_url, models FROM channels WHERE id = ?")
      .get(CHANNEL_ID);
    if (!row || typeof row.key !== "string" || row.key.length < 8) {
      throw new Error("ch48 key unavailable in the supplied database backup");
    }
    if (row.base_url !== CHANNEL_BASE_URL) {
      throw new Error("database backup ch48 base URL mismatch");
    }
    return row.key;
  } finally {
    database.close();
  }
}

async function requestJson(url, token, userId, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      Authorization: `Bearer ${token}`,
      "New-Api-User": String(userId),
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    signal: AbortSignal.timeout(30_000),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(`NewAPI request failed HTTP ${response.status}`);
  return body;
}

async function resolveAuth(authCache, adminCredentials) {
  let cached = null;
  try {
    cached = readJsonFile(authCache);
    const response = await fetch(`${NEWAPI_BASE}/api/channel/?p=0&page_size=1`, {
      headers: {
        Authorization: `Bearer ${cached.token}`,
        "New-Api-User": String(cached.user_id || 1),
      },
      signal: AbortSignal.timeout(10_000),
    });
    if (response.ok) return cached;
    if (response.status !== 401) {
      throw new Error(`cached admin token check failed HTTP ${response.status}`);
    }
  } catch (error) {
    if (error instanceof SyntaxError) throw new Error("admin token cache is invalid JSON");
    if (error?.message?.startsWith("cached admin token check failed")) throw error;
  }

  const credentials = readJsonFile(adminCredentials);
  const response = await fetch(`${NEWAPI_BASE}/api/user/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: credentials.username,
      password: credentials.password,
    }),
    signal: AbortSignal.timeout(10_000),
  });
  const body = await response.json().catch(() => null);
  const token = body?.data?.access_token;
  if (!response.ok || typeof token !== "string" || token.length < 8) {
    throw new Error(`NewAPI admin login failed HTTP ${response.status}`);
  }
  const auth = { token, user_id: String(body?.data?.id || 1) };
  const temporary = `${authCache}.tmp-muse-${process.pid}`;
  fs.writeFileSync(temporary, JSON.stringify(auth), { encoding: "utf8", flag: "wx" });
  fs.renameSync(temporary, authCache);
  return auth;
}

function apiPayload(channel, key) {
  const { status: _status, ...payload } = channel;
  return { ...payload, key };
}

async function putChannel(channel, key, token, userId) {
  const body = await requestJson(`${NEWAPI_BASE}/api/channel/`, token, userId, {
    method: "PUT",
    body: JSON.stringify(apiPayload(channel, key)),
  });
  if (!body || body.success !== true) throw new Error("NewAPI rejected channel update");
}

function atomicWritePair(modelsPath, configPath, modelOutput, configOutput, phase) {
  const originalModels = fs.readFileSync(modelsPath);
  const originalConfig = fs.readFileSync(configPath);
  const suffix = `${timestamp()}-before-opencode-go-muse-${phase}.bak`;
  const modelsBackup = `${modelsPath}.${suffix}`;
  const configBackup = `${configPath}.${suffix}`;
  fs.copyFileSync(modelsPath, modelsBackup, fs.constants.COPYFILE_EXCL);
  fs.copyFileSync(configPath, configBackup, fs.constants.COPYFILE_EXCL);
  if (sha256(fs.readFileSync(modelsBackup)) !== sha256(originalModels)) {
    throw new Error("models backup hash mismatch");
  }
  if (sha256(fs.readFileSync(configBackup)) !== sha256(originalConfig)) {
    throw new Error("config backup hash mismatch");
  }

  const targets = [
    [modelsPath, modelOutput, originalModels],
    [configPath, configOutput, originalConfig],
  ];
  try {
    for (const [target, output] of targets) {
      const temporary = `${target}.tmp-muse-${process.pid}`;
      fs.writeFileSync(temporary, output, { encoding: "utf8", flag: "wx" });
      fs.renameSync(temporary, target);
      if (sha256(fs.readFileSync(target)) !== sha256(Buffer.from(output))) {
        throw new Error(`${path.basename(target)} readback hash mismatch`);
      }
    }
  } catch (error) {
    for (const [target, _output, original] of targets) fs.writeFileSync(target, original);
    throw error;
  }
  return {
    modelsBackup: path.basename(modelsBackup),
    configBackup: path.basename(configBackup),
    modelsHash: sha256(Buffer.from(modelOutput)),
    configHash: sha256(Buffer.from(configOutput)),
  };
}

export async function deploy({
  phase,
  apply,
  agentDir,
  databaseBackup,
  authCache,
  adminCredentials,
  canaryState,
  acceptContributorDataPolicy,
}) {
  requireContributorConsent(phase, acceptContributorDataPolicy);
  const modelsPath = path.join(agentDir, "models.yml");
  const configPath = path.join(agentDir, "config.yml");
  const modelsSource = fs.readFileSync(modelsPath, "utf8");
  const configSource = fs.readFileSync(configPath, "utf8");
  if (phase === "finalize") validateCanaryState(readJsonFile(canaryState));
  const models = transformModelsYaml(modelsSource, phase);
  const config = transformConfigYaml(configSource, phase);
  const auth = await resolveAuth(authCache, adminCredentials);
  const originalResponse = await requestJson(
    `${NEWAPI_BASE}/api/channel/${CHANNEL_ID}`,
    auth.token,
    auth.user_id || 1,
  );
  const original = originalResponse?.data;
  if (!original || typeof original !== "object") throw new Error("ch48 readback missing");
  const planned = planChannel(original, phase);
  const changedChannel =
    planned.models !== original.models ||
    planned.name !== original.name ||
    planned.model_mapping !== original.model_mapping ||
    planned.test_model !== original.test_model ||
    Number(planned.priority) !== Number(original.priority) ||
    Number(planned.weight) !== Number(original.weight);
  const summary = {
    phase,
    apply,
    channelChanged: changedChannel,
    modelsChanged: models.changed,
    configChanged: config.changed,
  };
  if (!apply) return summary;

  const key = readSecretKey(databaseBackup);
  let channelUpdated = false;
  let filesApplied = false;
  try {
    if (changedChannel) {
      await putChannel(planned, key, auth.token, auth.user_id || 1);
      channelUpdated = true;
    }
    const files = atomicWritePair(
      modelsPath,
      configPath,
      models.output,
      config.output,
      phase,
    );
    filesApplied = true;
    const readback = await requestJson(
      `${NEWAPI_BASE}/api/channel/${CHANNEL_ID}`,
      auth.token,
      auth.user_id || 1,
    );
    const actual = readback?.data;
    if (
      actual?.models !== planned.models ||
      actual?.name !== planned.name ||
      actual?.test_model !== planned.test_model ||
      Number(actual?.priority) !== Number(planned.priority) ||
      Number(actual?.weight) !== Number(planned.weight)
    ) {
      throw new Error("ch48 readback does not match the planned projection");
    }
    return { ...summary, applied: true, ...files };
  } catch (error) {
    if (filesApplied) {
      fs.writeFileSync(modelsPath, modelsSource, "utf8");
      fs.writeFileSync(configPath, configSource, "utf8");
    }
    if (channelUpdated) {
      await putChannel(original, key, auth.token, auth.user_id || 1).catch(() => {});
    }
    throw error;
  }
}

function parseArgs(argv) {
  const home = os.homedir();
  const args = {
    apply: false,
    acceptContributorDataPolicy: false,
    phase: null,
    agentDir: path.join(home, ".omp", "agent"),
    databaseBackup: null,
    authCache: path.join(home, ".new-api-local", ".admin-token-cache.json"),
    adminCredentials: path.join(
      home,
      ".new-api-local",
      "admin-credentials.json",
    ),
    canaryState: path.join(
      home,
      ".omp",
      "agent",
      "model-tool-canary",
      "model-tool-canary-state.json",
    ),
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--apply") args.apply = true;
    else if (value === "--accept-contributor-data-policy") {
      args.acceptContributorDataPolicy = true;
    }
    else if (
      [
        "--phase",
        "--agent-dir",
        "--database-backup",
        "--auth-cache",
        "--admin-credentials",
        "--canary-state",
      ].includes(value)
    ) {
      const next = argv[index + 1];
      if (!next) throw new Error(`${value} requires a value`);
      const key = {
        "--phase": "phase",
        "--agent-dir": "agentDir",
        "--database-backup": "databaseBackup",
        "--auth-cache": "authCache",
        "--admin-credentials": "adminCredentials",
        "--canary-state": "canaryState",
      }[value];
      args[key] = key === "phase" ? next : path.resolve(next);
      index += 1;
    } else throw new Error(`unknown argument: ${value}`);
  }
  if (!["stage", "finalize", "rollback"].includes(args.phase)) {
    throw new Error("--phase must be stage, finalize, or rollback");
  }
  if (args.apply && !args.databaseBackup) {
    throw new Error("--database-backup is required with --apply");
  }
  return args;
}

const isMain =
  process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1]);
if (isMain) {
  deploy(parseArgs(process.argv.slice(2)))
    .then((result) => process.stdout.write(`${JSON.stringify(result)}\n`))
    .catch((error) => {
      process.stderr.write(`${error.message}\n`);
      process.exitCode = 1;
    });
}

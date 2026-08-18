import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_MODELS_PATH = path.join(os.homedir(), ".omp", "agent", "models.yml");
const TARGET_PROVIDER = "zg-newapi";
const DEFAULT_SOURCE_PROVIDER = "zg-newapi-anthropic";
const ALIAS_PREFIX = "omp-sota-";
const COMPACTION_SELECTOR = "zg-newapi/deepseek-v4-flash";
const DEFAULT_SOTA_MAX_TOKENS = 16_384;

function sha256(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

function timestamp() {
  return new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
}

function providerBounds(lines, provider) {
  const start = lines.findIndex((line) => line === `  ${provider}:`);
  if (start < 0) throw new Error(`provider not found: ${provider}`);
  let end = lines.findIndex((line, index) => index > start && /^  [^ ].*:$/.test(line));
  if (end < 0) end = lines.length;
  return { start, end };
}

function modelIndexes(lines, start, end, id) {
  const marker = `    - id: ${id}`;
  const matches = [];
  for (let index = start; index < end; index += 1) {
    if (lines[index] === marker) matches.push(index);
  }
  return matches;
}

function modelEnd(lines, start, providerEnd) {
  const next = lines.findIndex(
    (line, index) => index > start && index < providerEnd && line.startsWith("    - id: "),
  );
  return next < 0 ? providerEnd : next;
}

function yamlScalar(value, name) {
  if (!/^[A-Za-z0-9._-]+$/.test(value)) throw new Error(`invalid ${name}: ${value}`);
  return value;
}

function cloneModelBlock(lines, start, end, modelId, baseModel, maxTokens) {
  const block = lines.slice(start, end);
  block[0] = `    - id: ${modelId}`;
  const hasContext = block.some((line) => line.startsWith("      contextWindow:"));
  const hasMaxTokens = block.some((line) => line.startsWith("      maxTokens:"));
  if (!hasContext || !hasMaxTokens) {
    throw new Error(`source model lacks required capability metadata: ${baseModel}`);
  }

  const compactionIndex = block.findIndex((line) => line.startsWith("      compactionModel:"));
  if (compactionIndex >= 0) block[compactionIndex] = `      compactionModel: ${COMPACTION_SELECTOR}`;
  else block.splice(1, 0, `      compactionModel: ${COMPACTION_SELECTOR}`);

  const nameIndex = block.findIndex((line) => line.startsWith("      name:"));
  if (nameIndex >= 0) block[nameIndex] = `      name: OMP SOTA escalation (${baseModel}, bounded review)`;
  else block.splice(2, 0, `      name: OMP SOTA escalation (${baseModel}, bounded review)`);
  const maxTokensIndex = block.findIndex((line) => line.startsWith("      maxTokens:"));
  block[maxTokensIndex] = `      maxTokens: ${maxTokens}`;
  return block;
}

export function buildSotaModelId(baseModel) {
  return `${ALIAS_PREFIX}${yamlScalar(baseModel, "base model")}`;
}

export function transformModelsYaml(source, options = {}) {
  const baseModel = yamlScalar(options.baseModel ?? "claude-opus-5", "base model");
  const modelId = yamlScalar(options.modelId ?? buildSotaModelId(baseModel), "model id");
  const sourceProvider = yamlScalar(
    options.sourceProvider ?? DEFAULT_SOURCE_PROVIDER,
    "source provider",
  );
  const maxTokens = Number(options.maxTokens ?? DEFAULT_SOTA_MAX_TOKENS);
  if (!Number.isSafeInteger(maxTokens) || maxTokens <= 0) {
    throw new Error("max tokens must be a positive integer");
  }
  if (!modelId.startsWith(ALIAS_PREFIX)) throw new Error("model id must use omp-sota- prefix");

  const newline = source.includes("\r\n") ? "\r\n" : "\n";
  const hadFinalNewline = source.endsWith(newline);
  const lines = source.split(/\r?\n/);
  if (hadFinalNewline) lines.pop();

  const targetBounds = providerBounds(lines, TARGET_PROVIDER);
  const existing = modelIndexes(lines, targetBounds.start, targetBounds.end, modelId);
  if (existing.length > 1) throw new Error(`duplicate model id: ${TARGET_PROVIDER}/${modelId}`);

  const sourceBounds = providerBounds(lines, sourceProvider);
  const anchors = modelIndexes(lines, sourceBounds.start, sourceBounds.end, baseModel);
  if (anchors.length !== 1) {
    throw new Error(
      `expected one source model: ${sourceProvider}/${baseModel}; found ${anchors.length}`,
    );
  }
  const block = cloneModelBlock(
    lines,
    anchors[0],
    modelEnd(lines, anchors[0], sourceBounds.end),
    modelId,
    baseModel,
    maxTokens,
  );
  if (existing.length === 1) {
    const end = modelEnd(lines, existing[0], targetBounds.end);
    const current = lines.slice(existing[0], end);
    if (current.join("\n") === block.join("\n")) {
      return { changed: false, modelId, output: source };
    }
    lines.splice(existing[0], end - existing[0], ...block);
  } else {
    lines.splice(targetBounds.end, 0, ...block);
  }
  return { changed: true, modelId, output: lines.join(newline) + (hadFinalNewline ? newline : "") };
}

export function updateModelsFile(modelsPath, options = {}) {
  const original = fs.readFileSync(modelsPath);
  const result = transformModelsYaml(original.toString("utf8"), options);
  const expected = Buffer.from(result.output, "utf8");
  if (!result.changed || options.apply !== true) {
    return {
      changed: result.changed,
      applied: false,
      backup: null,
      modelId: result.modelId,
      beforeHash: sha256(original),
      afterHash: sha256(expected),
    };
  }

  const backup = `${modelsPath}.${timestamp()}-omp-sota.bak`;
  fs.copyFileSync(modelsPath, backup, fs.constants.COPYFILE_EXCL);
  if (sha256(fs.readFileSync(backup)) !== sha256(original)) throw new Error("backup hash mismatch");
  const temporary = `${modelsPath}.tmp-omp-sota-${process.pid}`;
  try {
    fs.writeFileSync(temporary, result.output, { encoding: "utf8", flag: "wx" });
    fs.renameSync(temporary, modelsPath);
    const afterHash = sha256(fs.readFileSync(modelsPath));
    if (afterHash !== sha256(expected)) {
      fs.copyFileSync(backup, modelsPath);
      throw new Error("written file hash mismatch; original restored");
    }
    return {
      changed: true,
      applied: true,
      backup: path.basename(backup),
      modelId: result.modelId,
      beforeHash: sha256(original),
      afterHash,
    };
  } finally {
    fs.rmSync(temporary, { force: true });
  }
}

function parseArgs(argv) {
  const options = {
    apply: false,
    modelsPath: DEFAULT_MODELS_PATH,
    baseModel: "claude-opus-5",
    sourceProvider: DEFAULT_SOURCE_PROVIDER,
    maxTokens: DEFAULT_SOTA_MAX_TOKENS,
  };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === "--apply") options.apply = true;
    else if (argv[index] === "--path" && argv[index + 1]) options.modelsPath = path.resolve(argv[++index]);
    else if (argv[index] === "--base-model" && argv[index + 1]) options.baseModel = argv[++index];
    else if (argv[index] === "--model-id" && argv[index + 1]) options.modelId = argv[++index];
    else if (argv[index] === "--source-provider" && argv[index + 1]) options.sourceProvider = argv[++index];
    else if (argv[index] === "--max-tokens" && argv[index + 1]) options.maxTokens = Number(argv[++index]);
    else throw new Error(`unknown argument: ${argv[index]}`);
  }
  return options;
}

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1]);
if (isMain) {
  try {
    const options = parseArgs(process.argv.slice(2));
    process.stdout.write(`${JSON.stringify(updateModelsFile(options.modelsPath, options))}\n`);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}

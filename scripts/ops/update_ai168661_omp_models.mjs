import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_MODELS_PATH = path.join(os.homedir(), ".omp", "agent", "models.yml");

const MODEL_BLOCKS = [
  {
    after: "deepseek-v4-flash",
    id: "deepseek-v4-flash-0731",
    lines: [
      "    - id: deepseek-v4-flash-0731",
      "      name: DeepSeek V4 Flash 0731 Vision (ai.168661 ch78)",
      "      reasoning: true",
      "      input:",
      "      - text",
      "      - image",
      "      contextWindow: 380000",
      "      maxTokens: 128000",
    ],
  },
  {
    after: "grok-4.5",
    id: "hy3",
    lines: [
      "    - id: hy3",
      "      name: Hunyuan HY3 (ai.168661 ch79)",
      "      reasoning: true",
      "      contextWindow: 196608",
      "      maxTokens: 32768",
    ],
  },
];

function providerBounds(lines, provider) {
  const start = lines.findIndex((line) => line === `  ${provider}:`);
  if (start < 0) {
    throw new Error(`provider not found: ${provider}`);
  }
  let end = lines.findIndex(
    (line, index) => index > start && /^  [^ ].*:$/.test(line),
  );
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

function insertModel(lines, provider, model) {
  let { start, end } = providerBounds(lines, provider);
  const existing = modelIndexes(lines, start, end, model.id);
  if (existing.length > 1) {
    throw new Error(`duplicate model id: ${provider}/${model.id}`);
  }
  if (existing.length === 1) return false;

  const anchors = modelIndexes(lines, start, end, model.after);
  if (anchors.length !== 1) {
    throw new Error(
      `expected one anchor model: ${provider}/${model.after}; found ${anchors.length}`,
    );
  }
  let insertAt = anchors[0] + 1;
  while (insertAt < end && !lines[insertAt].startsWith("    - id: ")) {
    insertAt += 1;
  }
  lines.splice(insertAt, 0, ...model.lines);
  return true;
}

export function transformModelsYaml(source) {
  const newline = source.includes("\r\n") ? "\r\n" : "\n";
  const hadFinalNewline = source.endsWith(newline);
  const lines = source.split(/\r?\n/);
  if (hadFinalNewline) lines.pop();

  let changed = false;
  for (const model of MODEL_BLOCKS) {
    changed = insertModel(lines, "zg-newapi", model) || changed;
  }
  const output = lines.join(newline) + (hadFinalNewline ? newline : "");
  return { changed, output };
}

function sha256(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

function timestamp() {
  const now = new Date();
  const parts = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
    "-",
    String(now.getHours()).padStart(2, "0"),
    String(now.getMinutes()).padStart(2, "0"),
    String(now.getSeconds()).padStart(2, "0"),
  ];
  return parts.join("");
}

function parseArgs(argv) {
  const args = { apply: false, modelsPath: DEFAULT_MODELS_PATH };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === "--apply") {
      args.apply = true;
    } else if (argv[index] === "--path" && argv[index + 1]) {
      args.modelsPath = path.resolve(argv[index + 1]);
      index += 1;
    } else {
      throw new Error(`unknown argument: ${argv[index]}`);
    }
  }
  return args;
}

export function updateModelsFile(modelsPath, apply) {
  const original = fs.readFileSync(modelsPath);
  const source = original.toString("utf8");
  const result = transformModelsYaml(source);
  if (!result.changed || !apply) {
    return {
      changed: result.changed,
      applied: false,
      backup: null,
      beforeHash: sha256(original),
      afterHash: sha256(Buffer.from(result.output, "utf8")),
    };
  }

  const backup = `${modelsPath}.${timestamp()}-ai168661.bak`;
  fs.copyFileSync(modelsPath, backup, fs.constants.COPYFILE_EXCL);
  const backupBytes = fs.readFileSync(backup);
  if (sha256(backupBytes) !== sha256(original)) {
    throw new Error("backup hash mismatch");
  }

  const temporary = `${modelsPath}.tmp-ai168661-${process.pid}`;
  try {
    fs.writeFileSync(temporary, result.output, { encoding: "utf8", flag: "wx" });
    fs.renameSync(temporary, modelsPath);
  } finally {
    fs.rmSync(temporary, { force: true });
  }
  const written = fs.readFileSync(modelsPath);
  if (sha256(written) !== sha256(Buffer.from(result.output, "utf8"))) {
    throw new Error("written file hash mismatch");
  }
  return {
    changed: true,
    applied: true,
    backup: path.basename(backup),
    beforeHash: sha256(original),
    afterHash: sha256(written),
  };
}

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1]);
if (isMain) {
  try {
    const args = parseArgs(process.argv.slice(2));
    const result = updateModelsFile(args.modelsPath, args.apply);
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}

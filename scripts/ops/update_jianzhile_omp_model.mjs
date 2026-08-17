import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_MODELS_PATH = path.join(os.homedir(), ".omp", "agent", "models.yml");
const PROVIDER = "zg-newapi";
const MODEL_ID = "jianzhile-codex-gpt-5.6-sol";
const ANCHOR_ID = "gpt-5.6-sol";
const MODEL_LINES = [
  `    - id: ${MODEL_ID}`,
  "      name: Jianzhile GPT 5.6 Sol (ch91 canary)",
  "      reasoning: true",
  "      contextWindow: 400000",
  "      maxTokens: 128000",
];

function sha256(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

function timestamp() {
  const now = new Date();
  return [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
    "-",
    String(now.getHours()).padStart(2, "0"),
    String(now.getMinutes()).padStart(2, "0"),
    String(now.getSeconds()).padStart(2, "0"),
  ].join("");
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

function modelIndexes(lines, start, end, id) {
  const marker = `    - id: ${id}`;
  const matches = [];
  for (let index = start; index < end; index += 1) {
    if (lines[index] === marker) matches.push(index);
  }
  return matches;
}

export function transformModelsYaml(source) {
  const newline = source.includes("\r\n") ? "\r\n" : "\n";
  const hadFinalNewline = source.endsWith(newline);
  const lines = source.split(/\r?\n/);
  if (hadFinalNewline) lines.pop();

  const { start, end } = providerBounds(lines);
  const existing = modelIndexes(lines, start, end, MODEL_ID);
  if (existing.length > 1) {
    throw new Error(`duplicate model id: ${PROVIDER}/${MODEL_ID}`);
  }
  if (existing.length === 1) {
    return { changed: false, output: source };
  }

  const anchors = modelIndexes(lines, start, end, ANCHOR_ID);
  if (anchors.length !== 1) {
    throw new Error(
      `expected one anchor model: ${PROVIDER}/${ANCHOR_ID}; found ${anchors.length}`,
    );
  }
  let insertAt = anchors[0] + 1;
  while (insertAt < end && !lines[insertAt].startsWith("    - id: ")) {
    insertAt += 1;
  }
  lines.splice(insertAt, 0, ...MODEL_LINES);
  return {
    changed: true,
    output: lines.join(newline) + (hadFinalNewline ? newline : ""),
  };
}

export function updateModelsFile(modelsPath, apply) {
  const original = fs.readFileSync(modelsPath);
  const result = transformModelsYaml(original.toString("utf8"));
  if (!result.changed || !apply) {
    return {
      changed: result.changed,
      applied: false,
      backup: null,
      beforeHash: sha256(original),
      afterHash: sha256(Buffer.from(result.output, "utf8")),
    };
  }

  const backup = `${modelsPath}.${timestamp()}-jianzhile-ch91.bak`;
  fs.copyFileSync(modelsPath, backup, fs.constants.COPYFILE_EXCL);
  if (sha256(fs.readFileSync(backup)) !== sha256(original)) {
    throw new Error("backup hash mismatch");
  }

  const temporary = `${modelsPath}.tmp-jianzhile-${process.pid}`;
  try {
    fs.writeFileSync(temporary, result.output, { encoding: "utf8", flag: "wx" });
    fs.renameSync(temporary, modelsPath);
  } finally {
    fs.rmSync(temporary, { force: true });
  }
  const written = fs.readFileSync(modelsPath);
  const expectedHash = sha256(Buffer.from(result.output, "utf8"));
  if (sha256(written) !== expectedHash) throw new Error("written file hash mismatch");
  return {
    changed: true,
    applied: true,
    backup: path.basename(backup),
    beforeHash: sha256(original),
    afterHash: expectedHash,
  };
}

function parseArgs(argv) {
  const args = { apply: false, modelsPath: DEFAULT_MODELS_PATH };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === "--apply") args.apply = true;
    else if (argv[index] === "--path" && argv[index + 1]) {
      args.modelsPath = path.resolve(argv[index + 1]);
      index += 1;
    } else throw new Error(`unknown argument: ${argv[index]}`);
  }
  return args;
}

const isMain =
  process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1]);
if (isMain) {
  try {
    const args = parseArgs(process.argv.slice(2));
    process.stdout.write(`${JSON.stringify(updateModelsFile(args.modelsPath, args.apply))}\n`);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}

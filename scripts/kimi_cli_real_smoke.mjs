import { mkdir, rm, writeFile } from "node:fs/promises";
import { execFile, spawn } from "node:child_process";
import { createServer } from "node:http";
import { once } from "node:events";
import { join } from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const cli = process.env.KIMI_CLI_BIN || "kimi";
let version = "unknown";
try {
  const result = await execFileAsync(cli, ["--version"], { encoding: "utf8" });
  version = result.stdout.trim() || result.stderr.trim() || version;
} catch {
  // The actual smoke result below still reports the process failure.
}

const home = join("D:/tmp", `kimi-code-real-smoke-${process.pid}`);
await rm(home, { recursive: true, force: true });
await mkdir(home, { recursive: true });

const server = createServer((request, response) => {
  if (request.method !== "POST" || !request.url?.endsWith("/responses")) {
    response.writeHead(404).end();
    return;
  }
  response.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-cache",
    connection: "keep-alive",
  });
  const id = "resp_kimi_real_smoke";
  const emit = (event, data) => {
    response.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  };
  emit("response.created", {
    type: "response.created",
    response: { id, object: "response", status: "in_progress", model: "smoke-model", output: [] },
  });
  emit("response.output_text.delta", {
    type: "response.output_text.delta",
    item_id: "msg_smoke",
    output_index: 0,
    content_index: 0,
    delta: "kimi-smoke-ok",
  });
  emit("response.output_text.done", {
    type: "response.output_text.done",
    item_id: "msg_smoke",
    output_index: 0,
    content_index: 0,
    text: "kimi-smoke-ok",
  });
  emit("response.completed", {
    type: "response.completed",
    response: {
      id,
      object: "response",
      status: "completed",
      model: "smoke-model",
      output: [
        {
          type: "message",
          id: "msg_smoke",
          role: "assistant",
          content: [{ type: "output_text", text: "kimi-smoke-ok", annotations: [] }],
        },
      ],
      usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2 },
    },
  });
  response.end();
});
server.listen(0, "127.0.0.1");
await once(server, "listening");
const port = server.address().port;
await writeFile(
  join(home, "config.toml"),
  `default_model = "smoke/default"\n\n[providers.smoke]\ntype = "openai_responses"\nbase_url = "http://127.0.0.1:${port}/v1"\napi_key = "smoke-key"\n\n[models."smoke/default"]\nprovider = "smoke"\nmodel = "smoke-model"\nmax_context_size = 262144\n`,
);

const child = spawn(cli, ["-p", "Return the test response", "-m", "smoke/default"], {
  cwd: home,
  env: { ...process.env, KIMI_CODE_HOME: home, NO_COLOR: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});
let stdout = "";
let stderr = "";
child.stdout.on("data", (chunk) => (stdout += chunk));
child.stderr.on("data", (chunk) => (stderr += chunk));
const exit = await Promise.race([
  once(child, "exit").then(([code]) => code ?? 1),
  new Promise((resolve) => setTimeout(() => {
    child.kill();
    resolve(124);
  }, 60_000)),
]);
server.close();
await rm(home, { recursive: true, force: true });
process.stdout.write(JSON.stringify({ version, cli, exit, stdout, stderr }, null, 2));
if (exit !== 0 || !stdout.includes("kimi-smoke-ok")) process.exit(1);

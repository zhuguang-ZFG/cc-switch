"use strict";

const http = require("http");

const DEFAULTS = Object.freeze({
  listenHost: "127.0.0.1",
  listenPort: 3003,
  upstreamHost: "127.0.0.1",
  upstreamPort: 3002,
  semanticTimeoutMs: 60000,
  maxBufferBytes: 1024 * 1024,
});

function isSemanticEvent(frame) {
  const data = frame
    .split(/\r?\n/)
    .find((line) => line.startsWith("data:"))
    ?.slice(5)
    .trim();
  if (!data || data === "[DONE]") return false;
  try {
    const event = JSON.parse(data);
    if (event.type === "content_block_delta") {
      const delta = event.delta || {};
      return Boolean(delta.text || delta.partial_json);
    }
    if (event.type === "content_block_start") {
      const block = event.content_block || {};
      return Boolean(block.text || block.name);
    }
    return false;
  } catch {
    return false;
  }
}

function writeGatewayTimeout(res, semanticTimeoutMs) {
  if (res.headersSent || res.writableEnded) return;
  const body = JSON.stringify({
    type: "error",
    error: {
      type: "overloaded_error",
      message: `No semantic model output within ${semanticTimeoutMs}ms`,
    },
  });
  res.writeHead(504, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(body),
    connection: "close",
  });
  res.end(body);
}

function createGatewayServer(options = {}) {
  const config = { ...DEFAULTS, ...options };
  const server = http.createServer((req, res) => {
    const upstreamReq = http.request({
      hostname: config.upstreamHost,
      port: config.upstreamPort,
      path: req.url,
      method: req.method,
      headers: {
        ...req.headers,
        host: `${config.upstreamHost}:${config.upstreamPort}`,
      },
    });

    req.on("aborted", () => upstreamReq.destroy());
    req.on("error", () => upstreamReq.destroy());
    req.pipe(upstreamReq);

    upstreamReq.on("response", (upstreamRes) => {
      const contentType = String(upstreamRes.headers["content-type"] || "");
      const isSse = contentType.includes("text/event-stream");
      if (
        !isSse ||
        upstreamRes.statusCode < 200 ||
        upstreamRes.statusCode >= 300
      ) {
        res.writeHead(upstreamRes.statusCode || 502, upstreamRes.headers);
        upstreamRes.pipe(res);
        return;
      }

      let committed = false;
      let buffered = Buffer.alloc(0);
      let text = "";
      const timer = setTimeout(() => {
        if (committed) return;
        upstreamReq.destroy();
        upstreamRes.destroy();
        writeGatewayTimeout(res, config.semanticTimeoutMs);
      }, config.semanticTimeoutMs);

      const commit = () => {
        if (committed || res.writableEnded) return;
        committed = true;
        clearTimeout(timer);
        res.writeHead(upstreamRes.statusCode || 200, upstreamRes.headers);
        res.write(buffered);
        buffered = Buffer.alloc(0);
      };

      upstreamRes.on("data", (chunk) => {
        if (committed) {
          res.write(chunk);
          return;
        }
        buffered = Buffer.concat([buffered, chunk]);
        if (buffered.length > config.maxBufferBytes) {
          upstreamReq.destroy();
          upstreamRes.destroy();
          clearTimeout(timer);
          writeGatewayTimeout(res, config.semanticTimeoutMs);
          return;
        }
        text += chunk.toString("utf8");
        const frames = text.split(/\r?\n\r?\n/);
        text = frames.pop() || "";
        if (frames.some(isSemanticEvent)) commit();
      });
      upstreamRes.on("end", () => {
        clearTimeout(timer);
        if (!committed && !res.writableEnded) commit();
        if (!res.writableEnded) res.end();
      });
      upstreamRes.on("error", (error) => {
        clearTimeout(timer);
        if (!res.headersSent) {
          res.writeHead(502, {
            "content-type": "text/plain",
            connection: "close",
          });
        }
        if (!res.writableEnded)
          res.end(`upstream stream error: ${error.message}`);
      });
      res.on("close", () => {
        clearTimeout(timer);
        if (!upstreamRes.complete) upstreamRes.destroy();
      });
    });

    upstreamReq.on("error", (error) => {
      if (res.headersSent || res.writableEnded) return;
      res.writeHead(502, {
        "content-type": "application/json",
        connection: "close",
      });
      res.end(
        JSON.stringify({
          error: { type: "upstream_error", message: error.message },
        }),
      );
    });
  });

  server.on("clientError", (_error, socket) => {
    socket.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n");
  });
  return server;
}

if (require.main === module) {
  const options = {
    listenHost: process.env.OMP_TTFT_HOST || DEFAULTS.listenHost,
    listenPort: Number(process.env.OMP_TTFT_PORT || DEFAULTS.listenPort),
    upstreamHost: process.env.OMP_TTFT_UPSTREAM_HOST || DEFAULTS.upstreamHost,
    upstreamPort: Number(
      process.env.OMP_TTFT_UPSTREAM_PORT || DEFAULTS.upstreamPort,
    ),
    semanticTimeoutMs: Number(
      process.env.OMP_TTFT_TIMEOUT_MS || DEFAULTS.semanticTimeoutMs,
    ),
    maxBufferBytes: Number(
      process.env.OMP_TTFT_MAX_BUFFER_BYTES || DEFAULTS.maxBufferBytes,
    ),
  };
  const server = createGatewayServer(options);
  server.on("error", (error) => {
    console.error(`OMP TTFT gateway failed: ${error.message}`);
    process.exitCode = 1;
  });
  server.listen(options.listenPort, options.listenHost, () => {
    console.log(
      `OMP TTFT gateway listening on http://${options.listenHost}:${options.listenPort} -> ` +
        `http://${options.upstreamHost}:${options.upstreamPort} (${options.semanticTimeoutMs}ms)`,
    );
  });
}

module.exports = { createGatewayServer, isSemanticEvent };

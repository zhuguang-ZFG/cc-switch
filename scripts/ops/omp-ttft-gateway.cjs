"use strict";

const http = require("http");
const { StringDecoder } = require("string_decoder");

const DEFAULTS = Object.freeze({
  listenHost: "127.0.0.1",
  listenPort: 3003,
  upstreamHost: "127.0.0.1",
  upstreamPort: 3002,
  semanticTimeoutMs: 60000,
  upstreamHeaderTimeoutMs: 60000,
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

function writeGatewayError(res, status, type, message) {
  if (res.headersSent || res.writableEnded) return;
  const body = JSON.stringify({ type: "error", error: { type, message } });
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(body),
    connection: "close",
  });
  res.end(body);
}

function createGatewayServer(options = {}) {
  const config = { ...DEFAULTS, ...options };
  const server = http.createServer((req, res) => {
    let terminal = false;
    let upstreamRes = null;
    let semanticTimer = null;
    let headerTimer = null;

    const destroyUpstream = () => {
      upstreamReq.destroy();
      upstreamRes?.destroy();
    };
    const fail = (status, type, message) => {
      if (terminal || res.writableEnded) return;
      terminal = true;
      clearTimeout(headerTimer);
      clearTimeout(semanticTimer);
      writeGatewayError(res, status, type, message);
      destroyUpstream();
    };

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

    headerTimer = setTimeout(() => {
      fail(
        504,
        "overloaded_error",
        `Upstream response timeout after ${config.upstreamHeaderTimeoutMs}ms`,
      );
    }, config.upstreamHeaderTimeoutMs);

    req.on("aborted", destroyUpstream);
    req.on("error", destroyUpstream);
    req.pipe(upstreamReq);

    upstreamReq.on("response", (response) => {
      if (terminal) {
        response.destroy();
        return;
      }
      clearTimeout(headerTimer);
      upstreamRes = response;
      const contentType = String(upstreamRes.headers["content-type"] || "");
      const isSse = contentType.includes("text/event-stream");
      if (
        !isSse ||
        upstreamRes.statusCode < 200 ||
        upstreamRes.statusCode >= 300
      ) {
        terminal = true;
        res.writeHead(upstreamRes.statusCode || 502, upstreamRes.headers);
        upstreamRes.pipe(res);
        return;
      }

      let committed = false;
      let buffered = Buffer.alloc(0);
      let text = "";
      const decoder = new StringDecoder("utf8");
      semanticTimer = setTimeout(() => {
        fail(
          504,
          "overloaded_error",
          `No semantic model output within ${config.semanticTimeoutMs}ms`,
        );
      }, config.semanticTimeoutMs);

      const writeChunk = (chunk) => {
        if (res.write(chunk)) return;
        upstreamRes.pause();
        res.once("drain", () => upstreamRes.resume());
      };
      const commit = () => {
        if (terminal || committed || res.writableEnded) return;
        committed = true;
        clearTimeout(semanticTimer);
        res.writeHead(upstreamRes.statusCode || 200, upstreamRes.headers);
        writeChunk(buffered);
        buffered = Buffer.alloc(0);
      };

      upstreamRes.on("data", (chunk) => {
        if (terminal) return;
        if (committed) {
          writeChunk(chunk);
          return;
        }
        buffered = Buffer.concat([buffered, chunk]);
        if (buffered.length > config.maxBufferBytes) {
          fail(
            504,
            "overloaded_error",
            `No semantic model output within ${config.semanticTimeoutMs}ms`,
          );
          return;
        }
        text += decoder.write(chunk);
        const frames = text.split(/\r?\n\r?\n/);
        text = frames.pop() || "";
        if (frames.some(isSemanticEvent)) commit();
      });
      upstreamRes.on("end", () => {
        clearTimeout(semanticTimer);
        if (terminal) return;
        text += decoder.end();
        if (!committed && !res.writableEnded) commit();
        if (!res.writableEnded) res.end();
      });
      upstreamRes.on("error", (error) => {
        fail(502, "upstream_error", error.message);
      });
      res.on("close", () => {
        clearTimeout(semanticTimer);
        if (!upstreamRes.complete) destroyUpstream();
      });
    });

    upstreamReq.on("error", (error) => {
      fail(502, "upstream_error", error.message);
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
    upstreamHeaderTimeoutMs: Number(
      process.env.OMP_TTFT_HEADER_TIMEOUT_MS ||
        DEFAULTS.upstreamHeaderTimeoutMs,
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

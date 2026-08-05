"use strict";

const assert = require("assert/strict");
const http = require("http");
const {
  createGatewayServer,
  isSemanticEvent,
} = require("./omp-ttft-gateway.cjs");

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

function request(port, path = "/") {
  return new Promise((resolve, reject) => {
    const req = http.get({ hostname: "127.0.0.1", port, path }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () =>
        resolve({
          status: res.statusCode,
          body: Buffer.concat(chunks).toString(),
        }),
      );
    });
    req.on("error", reject);
  });
}

async function withPair(handler, options, test) {
  const upstream = http.createServer(handler);
  const upstreamPort = await listen(upstream);
  const gateway = createGatewayServer({
    upstreamHost: "127.0.0.1",
    upstreamPort,
    semanticTimeoutMs: 40,
    ...options,
  });
  const gatewayPort = await listen(gateway);
  try {
    await test(gatewayPort);
  } finally {
    await close(gateway);
    await close(upstream);
  }
}

async function main() {
  assert.equal(isSemanticEvent('event: ping\ndata: {"type":"ping"}'), false);
  assert.equal(
    isSemanticEvent(
      'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ok"}}',
    ),
    true,
  );
  assert.equal(
    isSemanticEvent(
      'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"reasoning"}}',
    ),
    false,
  );

  await withPair(
    (_req, res) => {
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.write('event: ping\ndata: {"type":"ping"}\n\n');
      setTimeout(() => res.end(), 200);
    },
    {},
    async (port) => {
      const result = await request(port);
      assert.equal(result.status, 504);
      assert.match(result.body, /No semantic model output/);
    },
  );

  await withPair(
    (_req, res) => {
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.write('event: ping\ndata: {"type":"ping"}\n\n');
      setTimeout(() => {
        res.write(
          'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"HELLO"}}\n\n',
        );
        res.end('event: message_stop\ndata: {"type":"message_stop"}\n\n');
      }, 10);
    },
    {},
    async (port) => {
      const result = await request(port);
      assert.equal(result.status, 200);
      assert.match(result.body, /"type":"ping"/);
      assert.match(result.body, /HELLO/);
      assert.match(result.body, /message_stop/);
    },
  );

  await withPair(
    (_req, res) => {
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.write(
        `event: content_block_delta\ndata: ${JSON.stringify({
          type: "content_block_delta",
          delta: { type: "thinking_delta", thinking: "x".repeat(5000) },
        })}\n\n`,
      );
      setTimeout(() => {}, 200);
    },
    { maxBufferBytes: 100 },
    async (port) => {
      const result = await request(port);
      assert.equal(result.status, 504);
      assert.match(result.body, /No semantic model output/);
    },
  );

  await withPair(
    (_req, _res) => {},
    { upstreamHeaderTimeoutMs: 40 },
    async (port) => {
      const result = await request(port);
      assert.equal(result.status, 504);
      assert.match(result.body, /Upstream response timeout/);
    },
  );

  await withPair(
    (_req, res) => {
      res.writeHead(200, { "content-type": "application/json" });
      res.end('{"ok":true}');
    },
    {},
    async (port) => {
      const result = await request(port);
      assert.deepEqual(result, { status: 200, body: '{"ok":true}' });
    },
  );

  console.log("OMP TTFT gateway tests: 5 passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

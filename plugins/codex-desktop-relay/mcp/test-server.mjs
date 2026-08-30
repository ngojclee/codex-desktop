#!/usr/bin/env node

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const pluginServer = path.join(here, "server.mjs");
const tempRoot = await mkdtemp(path.join(tmpdir(), "codex-relay-test-"));
const calls = [];
let toolCallCount = 0;

function json(response, status, payload, headers = {}) {
  response.writeHead(status, {
    "Content-Type": "application/json",
    ...headers
  });
  response.end(JSON.stringify(payload));
}

const business = createServer(async (request, response) => {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  const session = request.headers["mcp-session-id"] || null;
  calls.push({
    method: payload.method,
    session,
    protocol: request.headers["mcp-protocol-version"],
    authorization: request.headers.authorization,
    tool: payload.params?.name || null
  });

  assert.equal(request.method, "POST");
  assert.equal(request.url, "/mcp");
  assert.equal(request.headers.authorization, "Bearer relay-test-token");
  assert.equal(request.headers["mcp-protocol-version"], "2025-06-18");

  if (payload.method === "initialize") {
    const sessionId = calls.filter((entry) => entry.method === "initialize").length === 1
      ? "relay-test-session-1"
      : "relay-test-session-2";
    json(response, 200, {
      jsonrpc: "2.0",
      id: payload.id,
      result: {
        protocolVersion: "2025-06-18",
        capabilities: {},
        serverInfo: { name: "test-business-mcp", version: "1.0.0" }
      }
    }, { "Mcp-Session-Id": sessionId });
    return;
  }

  if (payload.method === "notifications/initialized") {
    assert.ok(session);
    response.writeHead(202);
    response.end();
    return;
  }

  if (payload.method === "tools/call") {
    assert.ok(session);
    assert.equal(payload.params.name, "codex_relay_register");
    toolCallCount += 1;
    if (toolCallCount === 2) {
      json(response, 404, {
        jsonrpc: "2.0",
        id: payload.id,
        error: { code: -32001, message: "session not found" }
      });
      return;
    }
    json(response, 200, {
      jsonrpc: "2.0",
      id: payload.id,
      result: {
        content: [{
          type: "text",
          text: JSON.stringify({ device_id: payload.params.arguments.device_id })
        }]
      }
    });
    return;
  }

  json(response, 400, {
    jsonrpc: "2.0",
    id: payload.id,
    error: { code: -32601, message: "unexpected method" }
  });
});

function waitForJson(child, id, timeoutMs = 10_000) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error(`Timed out waiting for JSON-RPC response ${id}`));
    }, timeoutMs);
    const listener = (line) => {
      try {
        const payload = JSON.parse(line);
        if (payload.id !== id) return;
        child.stdout.off("data", onData);
        clearTimeout(timeout);
        resolve(payload);
      } catch {
        // stderr and non-JSON stdout are not part of the relay protocol.
      }
    };
    let buffer = "";
    const onData = (chunk) => {
      buffer += chunk.toString("utf8");
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop();
      for (const line of lines) listener(line);
    };
    child.stdout.on("data", onData);
  });
}

try {
  await new Promise((resolve) => business.listen(0, "127.0.0.1", resolve));
  const address = business.address();
  assert.ok(address && typeof address === "object");

  const configPath = path.join(tempRoot, "codex-relay.json");
  const receiptPath = path.join(tempRoot, "codex-relay-state.json");
  await writeFile(configPath, `${JSON.stringify({
    device_id: "TEST-CODEX-RELAY",
    business_mcp_url: `http://127.0.0.1:${address.port}/mcp`,
    business_token_env_var: "BUSINESS_MCP_CLIENT_TOKEN",
    auto_poll: false,
    allow_dispatch: false
  }, null, 2)}\n`, "utf8");

  const child = spawn(process.execPath, [pluginServer], {
    cwd: here,
    env: {
      ...process.env,
      BUSINESS_MCP_CLIENT_TOKEN: "relay-test-token",
      CODEX_RELAY_CONFIG: configPath,
      CODEX_RELAY_STATE: receiptPath
    },
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true
  });

  const stderr = [];
  child.stderr.on("data", (chunk) => stderr.push(chunk.toString("utf8")));
  child.stdin.write(`${JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: {} }
  })}\n`);
  const initialized = await waitForJson(child, 1);
  assert.ok(initialized.result);

  child.stdin.write(`${JSON.stringify({
    jsonrpc: "2.0",
    id: 2,
    method: "tools/call",
    params: { name: "relay_register", arguments: {} }
  })}\n`);
  const registerResult = await waitForJson(child, 2);
  assert.equal(registerResult.result.structuredContent.device_id, "TEST-CODEX-RELAY");

  assert.equal(calls.filter((entry) => entry.method === "initialize").length, 2);
  assert.equal(calls.filter((entry) => entry.method === "notifications/initialized").length, 2);
  assert.equal(calls.filter((entry) => entry.method === "tools/call").length, 3);
  assert.equal(calls.at(-1).session, "relay-test-session-2");
  assert.match(stderr.join(""), /codex-desktop-relay MCP server ready/);

  child.kill();
  await new Promise((resolve) => child.once("close", resolve));
  console.log("Codex Desktop Relay MCP session test passed");
} finally {
  await new Promise((resolve) => business.close(resolve));
  await rm(tempRoot, { recursive: true, force: true });
}

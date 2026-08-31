#!/usr/bin/env node

import { createInterface } from "node:readline";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { homedir, hostname } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const dispatchScript = path.join(here, "..", "scripts", "dispatch-thread.ps1");
const businessProtocolVersion = "2025-06-18";
const receiptRetentionMs = 7 * 24 * 60 * 60 * 1000;
const maxReceiptCount = 500;
const codexHome = path.join(homedir(), ".codex");

function write(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function result(id, value) {
  write({ jsonrpc: "2.0", id, result: value });
}

function error(id, code, message) {
  write({ jsonrpc: "2.0", id, error: { code, message } });
}

function toolDefinition(name, description, inputSchema) {
  return { name, description, inputSchema };
}

function firstString(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

async function readJsonFile(file) {
  try {
    return JSON.parse(await readFile(file, "utf8"));
  } catch (caught) {
    if (caught.code === "ENOENT") return null;
    throw caught;
  }
}

async function readCodexBusinessMcpConfig() {
  let text;
  try {
    text = await readFile(path.join(codexHome, "config.toml"), "utf8");
  } catch (caught) {
    if (caught.code === "ENOENT") return null;
    throw caught;
  }

  const lines = text.split(/\r?\n/);
  let section = null;
  let block = null;

  for (const rawLine of lines) {
    const line = rawLine.trim();
    const header = line.match(/^\[mcp_servers\.(.+)\]$/);
    if (header) {
      section = header[1].trim().replace(/^['"]|['"]$/g, "");
      block = null;
      continue;
    }
    if (!section || !/^mcp_servers\./i.test(section) || !line || line.startsWith("#")) {
      continue;
    }
    const entry = line.match(/^\s*([A-Za-z0-9_-]+)\s*=\s*(.+?)\s*$/);
    if (!entry) continue;
    const [, key, rawValue] = entry;
    const value = rawValue.trim().replace(/^['"]|['"]$/g, "");
    block ??= {};
    block[key] = value;
  }

  if (!section || !block) return null;
  const name = section.replace(/^mcp_servers\./i, "");
  if (!/business/i.test(name)) return null;
  if (block.enabled && block.enabled.toLowerCase() === "false") return null;

  const url = firstString(block.url);
  const tokenEnv = firstString(
    block.bearer_token_env_var,
    block.bearer_token_env,
    "BUSINESS_MCP_TOKEN"
  );
  if (!url || !tokenEnv) return null;

  return { url, tokenEnv, serverName: name };
}

async function readConfig() {
  const relayConfigPath = process.env.CODEX_RELAY_CONFIG
    || path.join(codexHome, "codex-relay.json");
  const relayConfig = await readJsonFile(relayConfigPath) ?? {};
  const codexMcp = await readCodexBusinessMcpConfig() ?? {};

  const url = firstString(
    relayConfig.business_mcp_url,
    codexMcp.url,
    process.env.BUSINESS_MCP_URL
  );
  if (!url) {
    throw new Error(
      "No relay business_mcp_url and no enabled [mcp_servers.business] in Codex config"
    );
  }

  const tokenEnv = firstString(
    relayConfig.business_token_env_var,
    relayConfig.business_token_env,
    codexMcp.tokenEnv,
    process.env.BUSINESS_MCP_TOKEN ? "BUSINESS_MCP_TOKEN" : null,
    process.env.BUSINESS_MCP_CLIENT_TOKEN ? "BUSINESS_MCP_CLIENT_TOKEN" : null
  );
  if (!tokenEnv) {
    throw new Error(
      "No relay business_token_env_var and no enabled [mcp_servers.business] in Codex config"
    );
  }

  return {
    device_id: firstString(relayConfig.device_id, hostname()),
    display_name: firstString(relayConfig.display_name),
    business_mcp_url: url,
    business_token_env_var: tokenEnv,
    auto_poll: relayConfig.auto_poll === true,
    poll_interval_sec: relayConfig.poll_interval_sec,
    allow_dispatch: relayConfig.allow_dispatch === true,
    allow_all_threads: relayConfig.allow_all_threads === true,
    allowed_thread_ids: relayConfig.allowed_thread_ids ?? [],
    dispatch_timeout_sec: relayConfig.dispatch_timeout_sec,
    business_request_timeout_sec: relayConfig.business_request_timeout_sec,
    config_path: relayConfigPath
  };
}

function businessToken(config) {
  const envName = config.business_token_env_var || config.business_token_env;
  const token = envName ? process.env[envName] : null;
  if (!token) {
    throw new Error(
      `Missing Business MCP token in environment variable ${envName || "(unconfigured)"}`
    );
  }
  return token;
}

function businessEndpoint(config) {
  const endpoint = String(config.business_mcp_url || "").trim();
  if (!endpoint) {
    throw new Error("Relay config must define business_mcp_url");
  }
  try {
    const url = new URL(endpoint);
    if (!["http:", "https:"].includes(url.protocol)) {
      throw new Error("must use http or https");
    }
  } catch (caught) {
    throw new Error(`Relay business_mcp_url is invalid: ${caught.message}`);
  }
  return endpoint;
}

function requestTimeoutSec(config) {
  return Math.max(5, Math.min(120, Number(config.business_request_timeout_sec ?? 45)));
}

function parseMcpResponse(text, contentType) {
  const body = text.trim();
  if (!body) {
    throw new Error("empty response body");
  }

  if (!contentType?.includes("text/event-stream")) {
    return JSON.parse(body);
  }

  const events = body.split(/\r?\n\r?\n/);
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const data = events[index]
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data || data === "[DONE]") continue;
    try {
      return JSON.parse(data);
    } catch {
      // A keepalive or a non-JSON event may precede the JSON-RPC response.
    }
  }
  throw new Error("no JSON-RPC payload found in SSE response");
}

function remoteError(payload) {
  const code = payload?.error?.code;
  const message = payload?.error?.message;
  if (message) {
    return code === undefined ? String(message) : `${code}: ${message}`;
  }
  return "unknown MCP error";
}

async function postBusinessMcp(config, body, session = null) {
  const token = businessToken(config);
  const endpoint = businessEndpoint(config);
  const timeoutSec = requestTimeoutSec(config);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutSec * 1000);
  let response;
  try {
    response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Accept": "application/json, text/event-stream",
        "Authorization": `Bearer ${token}`,
        "Content-Type": "application/json",
        "MCP-Protocol-Version": session?.protocolVersion || businessProtocolVersion,
        ...(session?.id ? { "Mcp-Session-Id": session.id } : {})
      },
      body: JSON.stringify(body),
      signal: controller.signal
    });
  } catch (caught) {
    const detail = caught.name === "AbortError"
      ? `timed out after ${timeoutSec}s`
      : caught.message;
    throw new Error(`Business MCP request failed: ${detail}`);
  } finally {
    clearTimeout(timer);
  }

  const text = await response.text();
  let payload = null;
  if (text.trim()) {
    try {
      payload = parseMcpResponse(text, response.headers.get("content-type"));
    } catch (caught) {
      throw new Error(`Business MCP returned an unreadable response: ${caught.message}`);
    }
  }
  return { response, payload };
}

let businessSession = null;

function sessionMatchesConfig(session, config) {
  if (!session) return false;
  const envName = config.business_token_env_var || config.business_token_env;
  return session.endpoint === businessEndpoint(config)
    && session.tokenEnv === envName;
}

async function createBusinessSession(config) {
  const initialize = await postBusinessMcp(config, {
    jsonrpc: "2.0",
    id: randomUUID(),
    method: "initialize",
    params: {
      protocolVersion: businessProtocolVersion,
      capabilities: {},
      clientInfo: {
        name: "codex-desktop-relay",
        version: "0.2.1"
      }
    }
  });
  if (!initialize.response.ok) {
    throw new Error(`Business MCP initialize failed: HTTP ${initialize.response.status}`);
  }
  if (initialize.payload?.error) {
    throw new Error(`Business MCP initialize error: ${remoteError(initialize.payload)}`);
  }
  if (!initialize.payload?.result) {
    throw new Error("Business MCP initialize did not return a result");
  }

  const session = {
    endpoint: businessEndpoint(config),
    tokenEnv: config.business_token_env_var || config.business_token_env,
    id: initialize.response.headers.get("mcp-session-id") || null,
    protocolVersion: initialize.payload.result.protocolVersion || businessProtocolVersion
  };
  const initialized = await postBusinessMcp(config, {
    jsonrpc: "2.0",
    method: "notifications/initialized",
    params: {}
  }, session);
  if (!initialized.response.ok) {
    throw new Error(
      `Business MCP initialized notification failed: HTTP ${initialized.response.status}`
    );
  }
  return session;
}

async function getBusinessSession(config, forceRenew = false) {
  if (!forceRenew && sessionMatchesConfig(businessSession, config)) {
    return businessSession;
  }
  businessSession = await createBusinessSession(config);
  return businessSession;
}

function isExpiredSession(result, session) {
  if (!session?.id) return false;
  if ([404, 410].includes(result.response.status)) return true;
  const message = String(result.payload?.error?.message || "").toLowerCase();
  return message.includes("session") && (
    message.includes("not found")
    || message.includes("invalid")
    || message.includes("expired")
  );
}

function unpackToolResult(payload) {
  if (payload?.error) {
    throw new Error(remoteError(payload));
  }
  const serverResult = payload?.result ?? {};
  const text = serverResult.content?.find((entry) => entry.type === "text")?.text;
  if (typeof text === "string" && text.trim()) {
    try {
      return JSON.parse(text);
    } catch {
      return { raw: text };
    }
  }
  return serverResult.structuredContent ?? serverResult;
}

async function callBusiness(config, toolName, args) {
  let session = await getBusinessSession(config);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const result = await postBusinessMcp(config, {
      jsonrpc: "2.0",
      id: randomUUID(),
      method: "tools/call",
      params: { name: toolName, arguments: args }
    }, session);
    if (isExpiredSession(result, session) && attempt === 0) {
      businessSession = null;
      session = await getBusinessSession(config, true);
      continue;
    }
    if (!result.response.ok) {
      throw new Error(`Business MCP ${toolName} failed: HTTP ${result.response.status}`);
    }
    try {
      return unpackToolResult(result.payload);
    } catch (caught) {
      throw new Error(`Business MCP ${toolName} error: ${caught.message}`);
    }
  }
  throw new Error(`Business MCP ${toolName} exhausted session recovery`);
}

async function sidecarHealth() {
  try {
    const statePath = path.join(homedir(), ".codex", "desktop-shared-app-server.json");
    const state = JSON.parse(await readFile(statePath, "utf8"));
    const response = await fetch(`http://127.0.0.1:${state.port}/healthz`);
    return {
      available: response.ok,
      port: state.port,
      sidecar_pid: state.sidecar_pid,
      started_at: state.startedAt
    };
  } catch {
    return { available: false };
  }
}

function canDispatch(config, threadId) {
  if (config.allow_dispatch !== true) return false;
  if (config.allow_all_threads === true) return true;
  return Array.isArray(config.allowed_thread_ids)
    && config.allowed_thread_ids.includes(threadId);
}

function runDispatch(threadId, prompt, timeoutSec) {
  const boundedTimeoutSec = Math.max(1, Math.min(3600, Math.floor(Number(timeoutSec) || 600)));
  return new Promise((resolve, reject) => {
    const child = spawn("powershell.exe", [
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      dispatchScript,
      "-ThreadId",
      threadId,
      "-Prompt",
      prompt,
      "-TimeoutSec",
      String(boundedTimeoutSec)
    ], {
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true
    });

    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`Dispatch to ${threadId} timed out after ${boundedTimeoutSec}s`));
    }, boundedTimeoutSec * 1000);

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (caught) => {
      clearTimeout(timer);
      reject(caught);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        reject(new Error(stderr.trim() || `Dispatch exited with code ${code}`));
        return;
      }
      resolve(stdout.trim());
    });
  });
}

function receiptStatePath() {
  return process.env.CODEX_RELAY_STATE
    || path.join(homedir(), ".codex", "codex-relay-state.json");
}

function newReceiptState() {
  return { version: 1, receipts: {} };
}

function normalizeReceiptState(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("relay receipt journal must be a JSON object");
  }
  if (value.version !== 1 || !value.receipts || typeof value.receipts !== "object") {
    throw new Error("relay receipt journal has an unsupported schema");
  }
  return value;
}

async function readReceiptState() {
  const file = receiptStatePath();
  try {
    return normalizeReceiptState(JSON.parse(await readFile(file, "utf8")));
  } catch (caught) {
    if (caught.code === "ENOENT") return newReceiptState();
    throw new Error(`Cannot read relay receipt journal at ${file}: ${caught.message}`);
  }
}

function pruneReceipts(state) {
  const cutoff = Date.now() - receiptRetentionMs;
  const receipts = Object.entries(state.receipts)
    .filter(([, receipt]) => {
      const updated = Date.parse(receipt.updated_at || receipt.started_at || "");
      return Number.isFinite(updated) && updated >= cutoff;
    })
    .sort(([, left], [, right]) => {
      const leftTime = Date.parse(left.updated_at || left.started_at || "") || 0;
      const rightTime = Date.parse(right.updated_at || right.started_at || "") || 0;
      return rightTime - leftTime;
    })
    .slice(0, maxReceiptCount);
  state.receipts = Object.fromEntries(receipts);
}

async function writeReceiptState(state) {
  const file = receiptStatePath();
  const directory = path.dirname(file);
  await mkdir(directory, { recursive: true });
  pruneReceipts(state);
  const temporary = `${file}.${process.pid}.${randomUUID()}.tmp`;
  await writeFile(temporary, `${JSON.stringify(state, null, 2)}\n`, "utf8");
  await rename(temporary, file);
}

async function getReceipt(messageId) {
  const state = await readReceiptState();
  return state.receipts[messageId] || null;
}

async function saveReceipt(messageId, patch) {
  const state = await readReceiptState();
  const previous = state.receipts[messageId] || {};
  const now = new Date().toISOString();
  state.receipts[messageId] = {
    ...previous,
    ...patch,
    message_id: messageId,
    updated_at: now
  };
  await writeReceiptState(state);
  return state.receipts[messageId];
}

async function acknowledgeReceipt(config, receipt) {
  const status = receipt.status;
  if (!["completed", "failed", "rejected"].includes(status)) {
    throw new Error(`Relay receipt ${receipt.message_id} is not terminal`);
  }
  await callBusiness(config, "codex_relay_ack", {
    device_id: config.device_id,
    message_id: receipt.message_id,
    status,
    response: status === "completed" ? receipt.response : undefined,
    error: status === "completed" ? undefined : receipt.error,
    idempotency_key: `ack:${receipt.message_id}:${status}`
  });
  return await saveReceipt(receipt.message_id, {
    acknowledged_at: new Date().toISOString()
  });
}

function registrationPayload(config) {
  if (!config.device_id) {
    throw new Error("Relay config must define device_id");
  }
  return {
    device_id: config.device_id,
    hostname: config.hostname || hostname(),
    protocol_version: "v1",
    capabilities: ["dispatch_thread", "report_back"],
    display_name: config.display_name || config.device_id
  };
}

let registrationKey;
let registeredAt = 0;

async function ensureRegistered(config, force = false) {
  const payload = registrationPayload(config);
  const key = `${config.business_mcp_url}|${payload.device_id}`;
  const stale = Date.now() - registeredAt > 60_000;
  if (!force && key === registrationKey && !stale) return null;

  const record = await callBusiness(config, "codex_relay_register", payload);
  registrationKey = key;
  registeredAt = Date.now();
  return record;
}

let pollInFlight = false;

async function acknowledgeTerminalReceipt(config, receipt, results) {
  try {
    const acknowledged = await acknowledgeReceipt(config, receipt);
    results.push({
      message_id: receipt.message_id,
      status: receipt.status,
      acknowledgement: "confirmed",
      acknowledged_at: acknowledged.acknowledged_at
    });
  } catch (caught) {
    results.push({
      message_id: receipt.message_id,
      status: receipt.status,
      acknowledgement: "pending",
      error: caught.message
    });
  }
}

async function processClaimedMessage(config, message, results) {
  const messageId = message.message_id;
  if (!messageId) {
    results.push({
      message_id: null,
      status: "failed",
      error: "Business MCP returned a message without message_id"
    });
    return;
  }

  let existing = await getReceipt(messageId);
  if (existing) {
    if (existing.status === "dispatching") {
      existing = await saveReceipt(messageId, {
        status: "rejected",
        error: "Receiver restarted while this message was dispatching; refusing to run it twice"
      });
    }
    await acknowledgeTerminalReceipt(config, existing, results);
    return;
  }

  if (!canDispatch(config, message.thread_id)) {
    const rejected = await saveReceipt(messageId, {
      status: "rejected",
      thread_id: message.thread_id,
      error: `Dispatch to thread ${message.thread_id} is not allowed by local policy`
    });
    await acknowledgeTerminalReceipt(config, rejected, results);
    return;
  }

  await saveReceipt(messageId, {
    status: "dispatching",
    thread_id: message.thread_id,
    started_at: new Date().toISOString()
  });

  let terminal;
  try {
    const response = await runDispatch(
      message.thread_id,
      message.message ?? message.prompt ?? "",
      message.timeout_sec ?? config.dispatch_timeout_sec ?? 600
    );
    terminal = await saveReceipt(messageId, {
      status: "completed",
      response,
      completed_at: new Date().toISOString()
    });
  } catch (caught) {
    terminal = await saveReceipt(messageId, {
      status: "failed",
      error: caught.message,
      completed_at: new Date().toISOString()
    });
  }

  await acknowledgeTerminalReceipt(config, terminal, results);
}

async function pollOnce(config) {
  if (pollInFlight) {
    return { skipped: true, reason: "poll_in_progress", claimed: 0, results: [] };
  }
  pollInFlight = true;
  try {
    await ensureRegistered(config);
    const claim = await callBusiness(config, "codex_relay_claim", {
      device_id: config.device_id,
      protocol_version: "v1",
      limit: Math.max(1, Math.min(10, Number(config.poll_batch_size ?? 1)))
    });

    const messages = Array.isArray(claim.messages) ? claim.messages : [];
    const results = [];
    for (const message of messages) {
      await processClaimedMessage(config, message, results);
    }
    return { claimed: messages.length, results };
  } finally {
    pollInFlight = false;
  }
}

let pollTimer;

async function startPolling(config) {
  if (config.auto_poll !== true || pollTimer) return;
  const intervalSec = Math.max(1, config.poll_interval_sec ?? 5) * 1000;
  void pollOnce(config).catch((caught) => {
    process.stderr.write(`Codex relay initial poll failed: ${caught.message}\n`);
  });
  pollTimer = setInterval(async () => {
    try {
      await pollOnce(config);
    } catch (caught) {
      process.stderr.write(`Codex relay poll failed: ${caught.message}\n`);
    }
  }, intervalSec);
  pollTimer.unref?.();
}

async function callTool(name, args) {
  const config = await readConfig();
  switch (name) {
    case "relay_status": {
      const sidecar = await sidecarHealth();
      return {
        device_id: config.device_id ?? null,
        business_mcp_url: config.business_mcp_url ?? null,
        sidecar,
        auto_poll: config.auto_poll === true,
        allow_dispatch: config.allow_dispatch === true,
        allow_all_threads: config.allow_all_threads === true,
        allowed_thread_count: Array.isArray(config.allowed_thread_ids)
          ? config.allowed_thread_ids.length
          : 0
      };
    }
    case "relay_register":
      return await ensureRegistered(config, true);
    case "relay_dispatch_local": {
      if (!canDispatch(config, args.thread_id)) {
        throw new Error(`Dispatch to thread ${args.thread_id} is not allowed by local policy`);
      }
      const response = await runDispatch(
        args.thread_id,
        args.message,
        args.timeout_sec ?? config.dispatch_timeout_sec ?? 600
      );
      return { thread_id: args.thread_id, status: "completed", response };
    }
    case "relay_send_remote": {
      return await callBusiness(config, "codex_relay_send", {
        source_device_id: config.device_id,
        target_device_id: args.target_device_id,
        thread_id: args.thread_id,
        message: args.message,
        reply_to_message_id: args.reply_to_message_id,
        timeout_sec: args.timeout_sec,
        idempotency_key: args.idempotency_key || randomUUID()
      });
    }
    case "relay_poll_once":
      return await pollOnce(config);
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

const tools = [
  toolDefinition(
    "relay_status",
    "Show local Codex relay, shared sidecar, and Business MCP queue policy status.",
    { type: "object", properties: {}, additionalProperties: false }
  ),
  toolDefinition(
    "relay_register",
    "Register or refresh this Codex Desktop receiver with the Business MCP control plane.",
    { type: "object", properties: {}, additionalProperties: false }
  ),
  toolDefinition(
    "relay_dispatch_local",
    "Dispatch a prompt into an existing local Codex thread through the shared sidecar.",
    {
      type: "object",
      required: ["thread_id", "message"],
      properties: {
        thread_id: { type: "string" },
        message: { type: "string", minLength: 1 },
        timeout_sec: { type: "number", minimum: 1, maximum: 3600 }
      },
      additionalProperties: false
    }
  ),
  toolDefinition(
    "relay_send_remote",
    "Queue a prompt for another registered Codex Desktop machine through Business MCP.",
    {
      type: "object",
      required: ["target_device_id", "thread_id", "message"],
      properties: {
        target_device_id: { type: "string", minLength: 1 },
        thread_id: { type: "string", minLength: 1 },
        message: { type: "string", minLength: 1 },
        reply_to_message_id: { type: "string" },
        timeout_sec: { type: "number", minimum: 1, maximum: 3600 },
        idempotency_key: { type: "string", minLength: 1, maxLength: 128 }
      },
      additionalProperties: false
    }
  ),
  toolDefinition(
    "relay_poll_once",
    "Claim queued Business MCP messages immediately, dispatch them locally, and acknowledge results.",
    { type: "object", properties: {}, additionalProperties: false }
  )
];

const lineReader = createInterface({ input: process.stdin });

lineReader.on("line", async (line) => {
  if (!line.trim()) return;
  let request;
  try {
    request = JSON.parse(line);
  } catch {
    return;
  }
  if (request.id === undefined) return;

  try {
    if (request.method === "initialize") {
      try {
        const config = await readConfig();
        await ensureRegistered(config);
        await startPolling(config);
      } catch (caught) {
        process.stderr.write(`Codex relay control-plane setup failed: ${caught.message}\n`);
      }
      result(request.id, {
        protocolVersion: request.params?.protocolVersion ?? "2025-06-18",
        capabilities: { tools: {} },
        serverInfo: {
          name: "codex-desktop-relay",
          version: "0.2.1"
        }
      });
      return;
    }
    if (request.method === "notifications/initialized") return;
    if (request.method === "tools/list") {
      result(request.id, { tools });
      return;
    }
    if (request.method === "tools/call") {
      const value = await callTool(request.params.name, request.params.arguments ?? {});
      result(request.id, {
        content: [{ type: "text", text: JSON.stringify(value, null, 2) }],
        structuredContent: value
      });
      return;
    }
    error(request.id, -32601, `Method not found: ${request.method}`);
  } catch (caught) {
    error(request.id, -32000, caught.message);
  }
});

process.stderr.write("codex-desktop-relay MCP server ready\n");

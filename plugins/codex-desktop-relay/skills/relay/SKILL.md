---
name: relay
description: Use when sending or receiving Codex thread messages between registered machines through Business MCP.
---

# Relay

Use this receiver to queue, dispatch, and acknowledge Codex thread messages without exposing the local sidecar to the network.

## Local Policy

Read machine policy from `%USERPROFILE%\.codex\codex-relay.json`:

```json
{
  "device_id": "DESKTOP-CM4T8SV",
  "business_mcp_url": "https://business-mcp.example/mcp",
  "business_token_env_var": "BUSINESS_MCP_CLIENT_TOKEN",
  "auto_poll": true,
  "poll_interval_sec": 5,
  "allow_dispatch": true,
  "allow_all_threads": false,
  "allowed_thread_ids": [
    "019d8850-c233-7751-bd95-602f083ea179"
  ],
  "dispatch_timeout_sec": 900
}
```

Rules:

- Never put the Business MCP bearer token in this file. Set it as an environment variable and reference its name with `business_token_env_var`.
- If `%USERPROFILE%\.codex\config.toml` already has an enabled Business MCP server, the relay reuses its URL and token env automatically. The bundled manifest passes `BUSINESS_MCP_TOKEN`, `BUSINESS_MCP_CLIENT_TOKEN`, and `CODEX_RELAY_CONFIG`.
- Keep `allow_dispatch = false` by default on new machines.
- Do not set `allow_all_threads = true` unless the machine is explicitly trusted for all Codex threads.
- Add only thread IDs that may receive remote prompts to `allowed_thread_ids`.
- The receiver connects outbound to Business MCP. It never listens on a public port and never proxies the shared sidecar.

## Tools

Use `relay_status` before troubleshooting to confirm device ID, Business URL, shared sidecar health, and local dispatch policy.

Use `relay_dispatch_local` to submit a prompt into an existing local thread through the shared sidecar. The target Desktop UI shows realtime streaming.

Use `relay_send_remote` to queue a prompt for another registered machine. Business MCP resolves the target device; the receiver on that machine claims, dispatches, and acknowledges the message.

Use `relay_poll_once` for an immediate queue drain. With `auto_poll = true`, the receiver polls in the background while the MCP server is alive.

## Server Contract

The Business MCP control plane exposes these tools:

- `codex_relay_register`
- `codex_relay_claim`
- `codex_relay_ack`
- `codex_relay_send`

Messages must include `message_id`, `source_device_id`, `target_device_id`, `thread_id`, `message`, `created_at`, and `expires_at`. A message is dispatched only when its target device claims it and the local allowlist permits its `thread_id`.

The relay plugin registers the machine at startup and refreshes registration
while it polls. Use `relay_register` to force a registration refresh when
diagnosing a new machine.

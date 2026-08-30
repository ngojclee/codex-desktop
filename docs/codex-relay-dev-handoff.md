# Business MCP Relay Dev Handoff

Please implement the Business MCP control-plane API described in:

```text
D:/Python/projects/codex-desktop/docs/codex-relay-api-contract.md
```

The receiver plugin source is already scaffolded at:

```text
D:/Python/projects/codex-desktop/plugins/codex-desktop-relay/
```

The receiver already has these MCP tools:

- `relay_status`
- `relay_dispatch_local`
- `relay_send_remote`
- `relay_poll_once`

It expects Business MCP to expose:

- `codex_relay_register`
- `codex_relay_claim`
- `codex_relay_ack`
- `codex_relay_send`

Implementation requirements:

1. Authenticate each device with a per-device bearer token. The local config stores only the token environment variable name, never the token.
2. Bind an authenticated token to its enrolled device identity. Do not trust a
   client-supplied `source_device_id` as authentication.
3. Provide durable queueing with `dispatching`, `completed`, `failed`, and `rejected` states.
4. Make claim idempotent. A message must not be dispatched twice by concurrent receivers.
5. Enforce message `expires_at` and return stale/expired messages as `rejected`, not silently delete them.
6. Return queue records and final response/error text through `codex_relay_ack`.
7. Allow `codex_relay_send` to target a specific `target_device_id` and `thread_id`.
8. Preserve enough source context (`source_device_id`, `reply_to_message_id`) for report-back.
9. Add admin/read tools if needed, but do not make them part of the receiver contract yet.
10. Use a dedicated `codex:relay` capability/grant. Existing VeilBrowser Fleet
    grants are not authorization for Codex thread dispatch.
11. Support standard stateful Streamable HTTP MCP clients. `codex_relay_send`
    and `codex_relay_ack` accept idempotency keys so a session renewal or
    acknowledgement retry cannot duplicate a queued prompt or report-back.

Suggested receiver registration payload is included in the contract. The receiver connects outbound to Business MCP and dispatches queued work only when its local policy explicitly permits the target thread.

# Codex Relay Business MCP Contract

The receiver plugin `codex-desktop-relay` treats Business MCP as the control plane and each Codex Desktop machine as an outbound-connected worker.

## Goals

- Route prompts to a specific machine and thread.
- Preserve realtime streaming in the target Codex Desktop UI.
- Avoid exposing local shared sidecar ports.
- Keep each machine's dispatch policy local and explicit.
- Support report-back messages to the originating machine/thread.

## Scope

This is a Codex Desktop relay queue, not a replacement for the existing
VeilBrowser Fleet service. A receiver makes one outbound authenticated MCP
connection and never exposes a sidecar port or accepts a remote shell session.

## Transport

The Hub uses stateful Streamable HTTP MCP by default. A receiver must perform
`initialize`, retain the returned `Mcp-Session-Id`, send
`notifications/initialized`, and then issue `tools/call` requests using that
session header. It may renew a session after an explicit session-not-found
response, but it must not retry a non-idempotent queue operation after an
ambiguous transport failure.

## Server Tools

Business MCP must expose these MCP tools at the endpoint configured on each
receiver. The public plugin never embeds the control-plane URL or a token.

### `codex_relay_register`

Arguments:

```json
{
  "device_id": "DESKTOP-CM4T8SV",
  "hostname": "DESKTOP-CM4T8SV",
  "protocol_version": "v1",
  "capabilities": ["dispatch_thread", "report_back"],
  "display_name": "PCFR DES-01"
}
```

Returns the registered device record.

### `codex_relay_claim`

Arguments:

```json
{
  "device_id": "DESKTOP-CM4T8SV",
  "protocol_version": "v1",
  "limit": 1
}
```

Returns:

```json
{
  "messages": [
    {
      "message_id": "uuid",
      "source_device_id": "OSHAK63",
      "target_device_id": "DESKTOP-CM4T8SV",
      "thread_id": "019d8850-c233-7751-bd95-602f083ea179",
      "message": "Do the assigned work.",
      "reply_to_message_id": null,
      "timeout_sec": 900,
      "created_at": "2026-08-30T19:00:00Z",
      "expires_at": "2026-08-30T21:00:00Z"
    }
  ]
}
```

Claiming marks the message as `dispatching`. Expired messages are not returned.

### `codex_relay_ack`

Arguments:

```json
{
  "device_id": "DESKTOP-CM4T8SV",
  "message_id": "uuid",
  "status": "completed",
  "response": "Final assistant text or a compact report path.",
  "error": null,
  "idempotency_key": "ack:uuid:completed"
}
```

Status values are `completed`, `failed`, or `rejected`.

### `codex_relay_send`

Arguments:

```json
{
  "source_device_id": "OSHAK63",
  "target_device_id": "DESKTOP-CM4T8SV",
  "thread_id": "019d8850-c233-7751-bd95-602f083ea179",
  "message": "Run the assigned scope and report back.",
  "reply_to_message_id": null,
  "timeout_sec": 900,
  "idempotency_key": "uuid"
}
```

Returns the queued message. The server assigns `message_id`, `created_at`, and `expires_at`.

## Security Rules

- Authenticate every server call with a per-device bearer token.
- Derive the authenticated client/device identity from the bearer token. Treat
  `source_device_id` as an auditable request field, not proof of identity.
- Store only the token environment variable name in the local receiver config.
- Reject dispatch when the local `allow_dispatch` policy is false.
- Reject dispatch unless the thread ID is explicitly allowlisted or `allow_all_threads` is true.
- Require claim-ack accounting so failed dispatches never disappear silently.
- Queue a rejected message back to the source device as a report-back rather than deleting it silently.
- Scope the four relay tools to a dedicated `codex:relay` grant; do not attach
  them to the generic Fleet or browser-control grants.
- Treat `codex_relay_ack` and `codex_relay_send` idempotency keys as durable
  uniqueness keys. A retried acknowledgement must not produce a second report
  or state transition.

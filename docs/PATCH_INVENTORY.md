# Codex Desktop Patch Inventory

This is the release-maintenance inventory for `ngojclee/codex-desktop`.
Every upstream rebuild must pass the applicable checks below. A patch marked
`compatibility/no-op` is intentionally retained so older upstream layouts stay
repairable; it must not be deleted merely because the current bundle already
contains the equivalent behavior.

## Release Pipeline

Renderer patches run in this order:

`A -> B -> C -> D -> G -> M -> H -> J -> K -> L -> O -> P -> Q -> R -> S -> T -> U -> Y -> Z -> B2`

The source-built `resources/codex.exe` lane runs in this order:

`I -> V -> X -> W1 -> N`

`B2` must remain last among ASAR changes because it updates the Owl executable's
embedded ASAR header hash after the final `app.asar` bytes are known.

## Renderer Patches

| Patch | Script | Current role | Keep? |
| --- | --- | --- | --- |
| A | `patch_codex_asar_recent_window.py` | Expands recent-thread discovery/history fallback. | Keep for older layouts. |
| B | `patch_codex_electron_fuse.py` | Disables Electron embedded-ASAR validation when the fuse exists. Owl builds safely no-op. | Keep for Electron compatibility. |
| C | `patch_codex_asar_autopaginate_v3.py` | Ensures sidebar/recent-thread pagination is complete. Current native cursor layouts may be verify-only. | Keep for older layouts. |
| D | `patch_codex_asar_reconnect_clear.py` | Clears stale renderer conversation state after sidecar reconnect. | Keep; auto-skip only for known `v26.513.x`. |
| G | `patch_codex_asar_ws_socks_bypass.py` | Keeps loopback shared-sidecar WebSockets out of the SOCKS proxy. Newer bundles may already contain the guard. | Keep as compatibility/no-op. |
| M | `patch_codex_asar_ws_max_payload.py` | Raises the shared-sidecar WebSocket payload ceiling. | Keep for shared-sidecar deployments. |
| H | `patch_codex_asar_directive_windows_path.py` | Sanitizes Windows paths in markdown directives. | Keep. |
| J | `patch_codex_asar_computer_use_gate.py` | Exposes Computer Use renderer gates for the patched build. | Keep only while this feature is intentionally enabled. |
| K | `patch_codex_asar_codex_mobile_gate.py` | Exposes the Codex mobile/remote entrypoint gates. | Keep while the entrypoint is intentionally exposed. |
| L | `patch_codex_plugin_scoped_node_modules.py` | Decodes escaped plugin package folders such as `%40`. | Keep for bundles that still ship escaped folders. |
| O | `patch_codex_asar_model_availability_filter.py` | Preserves non-hidden local/catalog models through the renderer allowlist. | Keep for custom catalog providers. |
| P | `patch_codex_asar_sol_max_effort.py` | Adds catalog-supported Sol Max to the compact Power control. | Keep while the custom Sol Max control is desired. |
| Q | `patch_codex_asar_gpt_model_labels.py` | Preserves visible GPT prefixes in model labels. | Keep. |
| R | `patch_codex_asar_custom_provider_fast_mode.py` | Allows catalog-declared Fast controls for API-key providers. | Keep while catalog Fast is desired. |
| S | `patch_codex_asar_custom_provider_ultra.py` | Allows catalog-declared Ultra for API-key providers without changing ChatGPT entitlement behavior. | Keep while catalog Ultra is desired. |
| T | `patch_codex_asar_voice_paste_shortcut.py` | Removes the conflicting Windows `Ctrl+Shift+V` Voice Mode binding. Current Windows layouts may be upstream-safe. | Keep as compatibility/no-op. |
| U | `patch_codex_asar_composer_input_safety.py` | Makes technical Markdown literal and suppresses duplicate paste delivery. | Keep; current bundle still requires it unless verifier says upstream-safe. |
| Y | `patch_codex_asar_automation_mode_union.py` | Replaces the two fragile outer nested `mode` discriminated unions with plain unions so valid Automation `create`/`update` calls reach the MCP tool. | Keep; current fix for the embedded-Zod discriminator bug. |
| Z | `patch_codex_asar_legacy_dynamic_app_tools.py` | Relaxes the renderer guard only for the five legacy dynamic thread/automation tools, so old conversations can keep calling the old dynamic path while new conversations use the `codex_app` MCP lane. | Keep while old chats must remain compatible. |
| B2 | `patch_codex_exe_asar_integrity_hash.py` | Updates the Owl executable's embedded ASAR header hash after all renderer patches. | Keep and run last. |

## Source-Built Sidecar Patches

| Patch | Where | Current role | Keep? |
| --- | --- | --- | --- |
| I | Workflow inline source edit | Removes empty `items: []` before `send_input` mutual-exclusion validation. | Keep as idempotent source guard. |
| V | `patch_codex_sidecar_standalone_tool_output.py` | Stores new standalone app-server tool output as assistant commentary context and blocks unsafe legacy provider serialization. | Keep. |
| X | `patch_codex_sidecar_legacy_unpaired_output_recovery.py` | Converts safely representable inherited standalone output at request-build time so resume, forked history, and manual compaction can proceed. Unsafe rows still fail closed under V. | Keep; this is not replaced by Y or W1. |
| W1 | `patch_codex_sidecar_request_byte_budget.py` | Measures final serialized request bytes, applies provider-aware local preflight, and makes deterministic size rejection non-retryable. | Keep; this is not replaced by X or Y. |
| N | Workflow inline source edit | Reduces persistent SQLite log churn and verifies upstream logging fixes. | Keep as idempotent source guard. |

## Runtime/Operational Repairs

These are not ASAR patches, but they are part of a usable release:

- `Ensure-Codex-AppToolsMcp.ps1`: maintains the sidecar-readable `.mcp.json`
  mirror for the Desktop `codex_app` transport and quarantines the obsolete
  static `mcp_servers.codex_app` workaround once the installed Desktop bundle
  proves it supports the per-session `CODEX_APP_TOOLS_PIPE_PATH` capability.
  Patch Z keeps the old dynamic renderer path compatible for threads created
  before that MCP lane was introduced; it does not edit stored transcripts.
- `Ensure-Codex-WslNative.ps1`: keeps
  `runCodexInWindowsSubsystemForLinux = false` in the correct `[desktop]` scope.
- `Launch-Codex.ps1`: starts/joins the shared sidecar and writes BOM-free state.
- `Ensure-Codex-StreamResilience.ps1`: maintains the provider-local retry and
  idle-timeout settings.
- `Update-Codex.ps1`: compares release asset state, including digest metadata,
  so a corrected artifact is not hidden by a reused version string.
- `codex-desktop-relay`: remains a separate plugin distribution path from the
  Desktop release bundle.

## What Is No Longer a Separate Release Lane

- `-sendinput` is no longer a separate release lane. Patch I is in the default
  sidecar build.
- `-xw1` was a historical combined release tag. Future rebuilt artifacts must
  use a distinct tag such as `-automation` or another explicit suffix.
- `repack-existing-patched.yml` is the bounded renderer-only recovery lane.
  It reuses a verified artifact that already contains X/W1, applies Y through
  the normal patcher, refreshes B2, and publishes a distinct tag. The normal
  upstream lane still rebuilds I/V/X/W1/N from source for future upstream
  versions.
- A patch may report `already_patched` or `upstream_safe`; that is not a
  failure. Only an anchor-drift error, missing marker, syntax error, integrity
  mismatch, or non-zero build/test exit is a release blocker.

## Acceptance Checklist

Before treating a release as installable:

1. `apply-all-patches.ps1` completes through `Z` and `B2`.
2. `verify_markers.py` passes every applicable renderer check, including both
   Patch Y guards, Patch Z's relaxed legacy dynamic guard, and the ASAR
   integrity check.
3. Sidecar source tests pass for I, V, X, W1, and N.
4. The release tag is distinct when the upstream version is unchanged.
5. The ZIP asset digest is recorded in the release and installer state.
6. After installation and restart, confirm the bundle has Patch Y and call
   `automation_update` with `mode=create`; then verify `update` separately.
7. Confirm an old chat can invoke `automation_update` through the legacy
   dynamic path and a new chat can invoke the same tool through `codex_app`
   MCP. Patch Z must not re-enable dynamic calls for non-allowlisted tools.
8. Do not claim the live Automation call is fixed until steps 6 and 7 succeed on the
   installed artifact.
9. Confirm `resources/list` can start `codex_app` without a
   `CODEX_APP_TOOLS_PIPE_PATH` error. A static user-level `codex_app` command
   block must be absent on pipe-capable Desktop builds; the helper preserves a
   timestamped `config.toml` backup and leaves older non-pipe-aware bundles
   unchanged.

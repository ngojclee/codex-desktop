# Codex Desktop Patch Inventory

This is the release-maintenance inventory for `ngojclee/codex-desktop`.
Every upstream rebuild must pass the applicable checks below. A patch marked
`compatibility/no-op` is intentionally retained so older upstream layouts stay
repairable; it must not be deleted merely because the current bundle already
contains the equivalent behavior.

When upstream provides the equivalent behavior, keep its detection/regression
gate but do not apply the old behavioral rewrite. Such builds report
`upstream_safe`/a native-layout outcome. C may retain its verification comment
without rewriting native pagination. Older supported layouts still use the
compatibility branch; removing those branches is a separate retirement decision.

## Release Pipeline

Renderer patches run in this order:

`A -> B -> C -> D -> G -> M -> H -> J -> K -> L -> O -> P -> Q -> R -> S -> T -> U -> Y -> Z -> B2`

The source-built `resources/codex.exe` lane runs in this order:

`I -> V -> X -> W1 -> N`

`B2` must remain last among ASAR changes because it updates the Owl executable's
embedded ASAR header hash after the final `app.asar` bytes are known.

**Two independent integrity layers are mandatory (P0, 2026-10-11).**
`patches/asar_integrity.py` streams every packed file with integrity metadata and
checks its SHA256 plus every block hash, including the empty-file block. Missing
metadata and unpacked entries are explicitly counted; malformed metadata,
truncated extents, stale file hashes or stale blocks fail the gate. The measured
26.1007 archive has integrity metadata on all 20,986 packed files.
`apply-all-patches.ps1` runs this gate before B2, so failures stop before core
compilation. `verify_markers.py` repeats it before marker/syntax checks at the
final release gate. `full-check.ps1` runs both on a disposable extracted bundle.
Neither B2 nor marker/syntax checks can substitute for payload integrity.

J/K preserve archive-wide matching, but diff the packed entry extents and use
the shared integrity-aware repacker for changed files only. A same-length
content edit still requires new hashes. Raw `asar.write_bytes(...)` is prohibited
in production ASAR patchers; the regression suite guards this and exercises all
eight integrity writers. Untouched stale entries are never silently healed.

The earlier `v26.1007.30912-patched-compat` release and the old local full-check
PASS are affected: the old gate missed two stale J entries. J introduced three
stale entries and K introduced one more; later repackers refreshed two of the
four, leaving the two J entries stale. Use the distinct
`v26.1007.30912-patched-asar-integrity` replacement after CI/artifact acceptance.
Publication, installation and live dropdown/mobile-dialog acceptance are
separate gates. Do not replace an already-hotfixed installed bundle during tests.

On October 12 local time, run `38174156115` / source `0c76cba7...` completed
successfully and the distinct `asar-integrity` successor was published as stable.
The downloaded 924,799,033-byte asset matches GitHub SHA256
`ab5ce71b346eb4959cc1e7b569d5be1c20d801a463971b1e0e4900ec90973e85`.
Staged ASAR scan: 20,986 checked, zero mismatches. Staged exe/header and native
core version checks pass. Installed/live UI acceptance is pending owner update
and coordinated restart; see `docs/ASAR_INTEGRITY.md` and `HANDOFF_DESK.md`.

New release tags must point to the workflow's tested `GITHUB_SHA`, not the
default main branch at publication time. The publisher passes `--target`
explicitly, and the regression suite guards it. Current `asar-integrity` tag
ref `91cb1a8...` differs from artifact source `0c76cba7...` only in three
documentation files; CI and bundled `.sidecar-source` establish exact identity.
No tag rewrite or mutable-asset replacement is used to hide that distinction.

The shared release verifier must recognize every supported patcher layout.
The 26.1007 gates include O's V5 safe/residual filter shape and complete R/U
modern layouts, not marker presence alone. CI includes executable R/U fixtures
and O's packed-ASAR verifier regression. Bundle checks are distinct from live
Desktop/provider acceptance after installation.

## Renderer Patches

| Patch | Script | Current role | Keep? |
| --- | --- | --- | --- |
| A | `patch_codex_asar_recent_window.py` | Expands recent-thread discovery/history fallback. | Keep for older layouts. |
| B | `patch_codex_electron_fuse.py` | Disables Electron embedded-ASAR validation when the fuse exists. Owl builds safely no-op. | Keep for Electron compatibility. |
| C | `patch_codex_asar_autopaginate_v3.py` | Ensures sidebar/recent-thread pagination is complete. Current native cursor layouts may be verify-only. | Keep for older layouts. Upstream 26.930 already has the loop natively, so those bundles are marked `native_expanded_history` and only get a `__capV3` comment instead of a rewrite. |
| D | `patch_codex_asar_reconnect_clear.py` | Clears stale renderer conversation state after sidecar reconnect. | Keep; auto-skip only for known `v26.513.x`. Targets are chosen by content, not file name: `v26.1007` ships the same host-manager class in two chunks (`.vite/build/src-*.js` and `webview/assets/app-shared-*.js`) while a third chunk only calls the method, so every chunk that owns the body is patched and a caller-only chunk is never a target. |
| G | `patch_codex_asar_ws_socks_bypass.py` | Keeps loopback shared-sidecar WebSockets out of the SOCKS proxy. `v26.930` uses the equivalent `host !== localhost && host !== 127.0.0.1 && host !== [::1]` guard, so it is verified upstream-safe without changing remote-host proxy behavior. | Keep as compatibility/no-op. |
| M | `patch_codex_asar_ws_max_payload.py` | Raises the shared-sidecar WebSocket payload ceiling. | Keep for shared-sidecar deployments. |
| H | `patch_codex_asar_directive_windows_path.py` | Sanitizes Windows paths in markdown directives. | Keep. |
| J | `patch_codex_asar_computer_use_gate.py` | Exposes Computer Use renderer gates for the patched build; changed entries are integrity-aware repacked. | Keep only while this feature is intentionally enabled. |
| K | `patch_codex_asar_codex_mobile_gate.py` | Exposes the Codex mobile/remote entrypoint gates; changed entries are integrity-aware repacked. | Keep while the entrypoint is intentionally exposed. |
| L | `patch_codex_plugin_scoped_node_modules.py` | Decodes escaped plugin package folders such as `%40`. | Keep for bundles that still ship escaped folders. |
| O | `patch_codex_asar_model_availability_filter.py` | Preserves non-hidden local/catalog models through the renderer allowlist. | Keep for custom catalog providers. `v26.1007` appends an access-program clause inside the allowlist branch, which is why the V5 matcher anchors on the ternary head only and leaves any trailing clause untouched. |
| P | `patch_codex_asar_sol_max_effort.py` | Adds catalog-supported Sol Max to the compact Power control. | Keep while the custom Sol Max control is desired. |
| Q | `patch_codex_asar_gpt_model_labels.py` | Preserves visible GPT prefixes in model labels. | Keep. |
| R | `patch_codex_asar_custom_provider_fast_mode.py` | Allows catalog-declared Fast controls for API-key providers. | Keep while catalog Fast is desired. `v26.1007` now patches both the selector's speed-capability memo and its request-preparation caller, only for `apikey`. ChatGPT/personal-access-token requirements remain unchanged; catalog tier options are not synthesized. The memo tracks `authMethod`, not just a ChatGPT boolean, so auth transitions cannot reuse stale access. Both modern shapes/markers must be intact or verification fails. |
| S | `patch_codex_asar_custom_provider_ultra.py` | Allows catalog-declared Ultra for API-key providers without changing ChatGPT entitlement behavior. | Keep while catalog Ultra is desired. Verified `patched` with no residual matched gate and Node syntax check passing on the real `v26.1007` Windows ASAR. This reasoning-effort gate is distinct from the newer service-tier helper's `ultrafast` speed capability. |
| T | `patch_codex_asar_voice_paste_shortcut.py` | Removes the conflicting Windows `Ctrl+Shift+V` Voice Mode binding. Current Windows layouts may be upstream-safe. | Keep as compatibility/no-op. |
| U | `patch_codex_asar_composer_input_safety.py` | Makes technical Markdown literal and suppresses duplicate paste delivery. | Keep. Retains older composer layouts and adds structural `v26.1007` matching for plugin/input bindings and both normal/oversized HTML fallbacks. Only text paste/input-rule paths change; early file/image/page-reference handling, Codex mentions, code-block paste and explicit formatting remain. Partial/duplicated layouts or residual rich-paste paths fail loudly. |
| Y | `patch_codex_asar_automation_mode_union.py` | Replaces the two fragile outer nested `mode` discriminated unions with plain unions so valid Automation `create`/`update` calls reach the MCP tool. Anchors are structural (the schema's own `view`/`delete` enum literals) and never minified names, so an upstream identifier rename is followed automatically; a bundle that already ships both unions flat reports `upstream_safe`. | Keep; current fix for the embedded-Zod discriminator bug. |
| Z | `patch_codex_asar_legacy_dynamic_app_tools.py` | Relaxes the renderer guard only for the five legacy dynamic thread/automation tools, so old conversations can keep calling the old dynamic path while new conversations use the `codex_app` MCP lane. | **Deprecated on 26.930 and later.** Upstream dropped the `unsupported call` blocklist entirely: `callDynamicAppTool` now asks `dynamicAppTools.canCallTool(e)` instead, so the legacy tool path is governed by webview capabilities, not a hard-coded name list. Marked `upstream_safe` for 26.930+. |
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

- `Ensure-Codex-AppToolsMcp.ps1`: preserves existing static `codex_app` blocks
  containing `command`, `args`, and `cwd` (O1, commit `c901574`). This is a
  field-presence guard, not complete TOML, executable, or live-pipe validation.
  Incomplete static definitions retain the existing cleanup behavior.
  A preserved block is normalized to `enabled = false` and loses any stale
  `CODEX_APP_TOOLS_PIPE_PATH` assignment, so the sidecar can still resolve the
  legacy transport without spawning a server that has no per-session pipe. That
  spawn was what made every `codex_app` tool call return `unsupported call`.
  Desktop's `desktop-mcp.json` (`enabled: true` plus `env_vars`) owns the live
  server, and the rerun is a no-op once the block is already disabled.
  **Measured limit:** disabling is not the automation fix. With the block present
  and `enabled = false`, a freshly restarted sidecar still returns
  `unsupported call: mcp__codex_app__automation_update`. Any user-level
  `codex_app` entry, enabled or disabled, keeps the tools unregistered, so this
  normalization only prevents a doomed pipeless spawn and must not be treated as
  restoring the app-tools route. See `HANDOFF_DESK.md`, recovery note 2026-09-12.
  It maintains the disabled `.mcp.json` mirror for the Desktop `codex_app`
  transport. The ASAR capability literal gates cleanup; that literal alone
  does not prove a live per-session pipe exists.
  Patch Z keeps the old dynamic renderer path compatible for threads created
  before that MCP lane was introduced; it does not edit stored transcripts.
- `Ensure-Codex-WslNative.ps1`: keeps
  `runCodexInWindowsSubsystemForLinux = false` in the correct `[desktop]` scope.
- `Ensure-Codex-GoogleMcp.ps1`: is transport-aware. Existing Google MCP
  sections with `command` are stdio wrappers, so a top-level `url` key is
  invalid and is removed while `command`, `args` and timeout are preserved.
  Sections without `command` remain remote transports and receive the shared
  connector URL. `patches/test_google_mcp_runtime.ps1` covers both modes and
  idempotence without reading or writing the user's real config.
- `verify_runtime_bundle.ps1`: enforces the single source of truth for packaged
  helpers. Both release lanes remove inherited package `.ps1` snapshots, copy
  `runtime/` fresh, hash-check every source file, and reject any package-only
  PowerShell helper. This closes the stale `tools/Ensure-Codex-GoogleMcp.ps1`
  failure that repeatedly reinserted an invalid `url` into stdio sections.
- `Find-CodexAppToolsPipe.ps1`: identifies which `\\.\pipe\codex-browser-use-*`
  named pipe is actually the app-tools host by running the bundle's own
  `server.mjs` against each candidate and asking it for `tools/list`. Reusing the
  app's client keeps the probe correct across upstream protocol changes, and the
  naming is why earlier manual enumeration dismissed the real pipe. Refuses to run
  while an Electron-owned app-server is live unless `-Force` is passed, because
  attaching a second client can disturb an active app-tools lane. `-Apply` writes
  the discovered pipe plus `CODEX_MCP_NODE_PATH` into
  `[mcp_servers.codex_app]` and `[mcp_servers.codex_app.env]`, backing up
  `config.toml` first. Verified both branches: a wrong pipe reports a clean
  timeout, the live app-tools pipe reports 27 tools including
  `automation_update`. It is bundled into `tools/` by the release workflows because
  everything under `runtime/` is copied and hash-checked.
- `Repair-CodexPluginCacheSkills.ps1`: copies plugin skill files that Desktop failed to
  materialise into `~\.codex\plugins\cache\openai-bundled\<plugin>\<version>`. Desktop
  copies a bundled plugin once and then logs
  `bundled_plugin_install_skipped_current pluginName=<plugin>` on every later boot, so a
  single missing file never comes back on its own. Measured on 10.11.1.1: the cache copy
  of `chrome` matched the marketplace copy in every subdirectory except `skills` (0 vs
  1 file), the missing file being `skills/control-chrome/SKILL.md`, which is why
  `$Chrome` vanished from the plugin and skill list while chrome stayed
  `enabled = true`. The same run also found `browser` missing
  `skills/control-in-app-browser/SKILL.md` and its `agents/openai.yaml`. Copy-only, never
  deletes, skips plugins the app has not materialised yet, and is a no-op when the cache
  is healthy. Called from `Launch-Codex.ps1` before the sidecar starts, with its own CI
  regression test `patches/test_plugin_cache_skills.ps1` wired into both release lanes.
- `Repair-CodexSharedAppTools.ps1`: the shared-mode fix. Waits for Electron to create
  the app-tools pipe, registers it via `Find-CodexAppToolsPipe.ps1 -Apply`, then
  reloads user config over the running sidecar's own WebSocket with
  `config/batchWrite { edits: [], reloadUserConfig: true }`. No process restart, no
  app.asar edit. Verified end to end on 10.11.1.1 in shared `--listen` mode:
  `mcpServerStatus/list` reports `codex_app` with 27 tools including
  `automation_update` and `toolsError = null`, and a real
  `mcpServer/tool/call codex_app/automation_update` returns `isError: false`.
  Re-running it is a no-op that writes nothing and creates no backup.
- `Launch-Codex.ps1`: after starting Electron, launches
  `Repair-CodexSharedAppTools.ps1` hidden in the background so a shared-mode session
  self-heals app-tools. Set `CODEX_SKIP_APP_TOOLS_REPAIR=1` to disable.
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
6. `verify_runtime_bundle.ps1` passes after bundling and
   `test_google_mcp_runtime.ps1` proves stdio sections contain no top-level
   `url`; package-only `.ps1` files must fail the build.
7. After installation and restart, confirm the bundle has Patch Y and call
   `automation_update` with `mode=create`; then verify `update` separately.
8. Confirm an old chat can invoke `automation_update` through the legacy
   dynamic path and a new chat can invoke the same tool through `codex_app`
   MCP. Patch Z must not re-enable dynamic calls for non-allowlisted tools.
9. Confirm `tools\Repair-CodexSharedAppTools.ps1` exits with
   `OK: codex_app is present in the shared sidecar MCP registry` after a normal
   `Launch-Codex.vbs` start, and that the written block carries
   `omit_tools_from = ["deferred"]`. Without that field the server runs and answers
   `mcpServer/tool/call`, but no thread ever sees `mcp__codex_app__*` and the app
   reports `unsupported call`.
10. Re-run the repair after every app restart. The pipe name is regenerated per
   Electron session, so a persisted value from a previous session is dead and the
   sidecar will hang on it until the MCP startup timeout.
11. Accepted state, measured 2026-09-13 on `ee93e67` or later: create, view, update
    and delete all work natively in a new thread under shared mode. Artifacts built
    before `ee93e67` (including `...-z2-appshared`) do not satisfy item 8.
12. Do not claim the live Automation call is fixed until steps 7 and 8 succeed on the
   installed artifact.
13. Confirm `resources/list` can start `codex_app` without a
   `CODEX_APP_TOOLS_PIPE_PATH` error. A static user-level `codex_app` command
   block must be absent on pipe-capable Desktop builds; the helper preserves a
   timestamped `config.toml` backup and leaves older non-pipe-aware bundles
   unchanged.

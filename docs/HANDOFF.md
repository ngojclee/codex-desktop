# Codex Desktop Windows Recent Conversations Patch

This repo contains a small Windows patcher for Codex Desktop when project
conversation history disappears from the sidebar because the app only loads a
limited global recent-conversation window.

It does not include Codex binaries, ASAR files, SQLite databases, JSONL
transcripts, or any user-specific data.

## Problem

Codex Desktop project groups are built from a global recent-conversations cache.
When a project's threads fall outside that cache window, the project can show no
chats even though the sessions still exist in the local Codex data store.

Typical symptoms:

- Some projects show incomplete or empty chat history.
- Opening or updating one project makes another project's conversations vanish.
- The session files and local SQLite rows still exist.
- Pinging/resuming old sessions temporarily brings them back by moving them into
  the global recent window.

## Root Cause

There are three relevant layers:

1. Renderer recent-window request:
   `webview/assets/app-server-manager-signals-*.js` originally requests a small
   recent thread page.

2. App-server page cap:
   Codex app-server clamps `thread/list` page size to `THREAD_LIST_MAX_LIMIT =
   100`, so simply requesting a huge `limit` from the renderer still returns only
   one page.

3. Electron ASAR integrity fuse:
   Store builds enable `EnableEmbeddedAsarIntegrityValidation`, so changing
   `app.asar` requires flipping that fuse on the copied executable. Otherwise
   the app exits immediately at startup.

The practical fix is to patch a user-writable copy of the Store app so the
renderer auto-paginates `thread/list` until it has loaded enough recent
conversations.

## What The Patcher Does

The PowerShell entrypoint copies the Microsoft Store Codex app into:

```text
%LOCALAPPDATA%\OpenAI\CodexDesktopPatched
```

Then it applies the current patch set to the copied app only:

- `patch_codex_asar_recent_window.py`
  Bumps renderer `limit:50` patterns to `limit:1000`.

- `patch_codex_electron_fuse.py`
  Flips Electron fuse index 4, `EnableEmbeddedAsarIntegrityValidation`, from
  enabled to removed on the copied `Codex.exe`.

- `patch_codex_asar_autopaginate_v2.py`  *(v2 — guarded; supersedes v1)*
  Rewrites `refetchThreadList` to call `thread/list` in `limit:100` pages until
  `nextCursor` is exhausted or a safety cap of `2000` conversations is reached,
  **but guarded by `this.fetchedRecentConversations` so the loop only runs on
  the first invocation per session**. Subsequent refetches behave like the
  un-patched 1-page original. This prevents the regression in v1 where the
  unconditional loop would run mid-stream during delegation A→B and overwrite
  the streaming thread's per-thread state with stale snapshots.

- `patch_codex_asar_reconnect_clear.py`  *(Patch D — soft-refresh fix)*
  Injects cache-clear into `markAllConversationsNeedResumeAfterReconnect`.
  After the existing resume-state flag loop, it calls
  `applyConversationState(id, null)` for every cached conversation and resets
  `fetchedRecentConversations=false`. Effect: when the soft-refresh
  workflow kills the sidecar `codex.exe`, Electron supervisor (class `pu`)
  respawns it with fresh disk-read state, the renderer reconnects, this patch
  fires, the in-memory conversations Map is cleared, and the renderer
  re-fetches from the now-fresh sidecar. Without Patch D, the renderer keeps
  its stale Map across reconnect and `markAllConversationsNeedResume
  AfterReconnect` only flipped a flag — the UI never refreshed without a
  full app close+reopen. On upstream `26.513.x`, this reconnect clear now appears to regress thread-open hydration for some sessions, so the current release lane skips Patch D there until a safer variant is found.

- `patch_codex_asar_ws_socks_bypass.py` *(Patch G)*
  Removes the hardcoded SOCKS5 agent from local WebSocket app-server transport
  so Desktop can attach to the shared sidecar launcher.

- `patch_codex_asar_ws_max_payload.py` *(Patch M)*
  Adds `maxPayload:1024*1024*1024` to the shared WebSocket app-server client.
  This prevents heavy thread hydration from closing the UI connection with
  `Max payload size exceeded` / close code `1006`, which otherwise makes
  model, provider, MCP, and thread resume requests look unavailable until the
  renderer reconnects.

- `patch_codex_asar_directive_windows_path.py` *(Patch H)*
  Normalizes Windows paths inside one-line Codex app directives before markdown
  directive parsing, preventing renderer crashes from directive backslashes.

- `patch_codex_asar_computer_use_gate.py` *(Patch J)*
  Bypasses renderer Statsig gates for Computer Use (Any App + Chrome). The
  launcher also sets the dev-flavor/feature env vars required by the Windows
  plugin reconciliation path.

- `patch_codex_asar_codex_mobile_gate.py` *(Patch K)*
  Exposes the bundled `/codex-mobile` setup entrypoint by relaxing the local
  sidebar gate and enabling the related remote-control/onboarding Statsig
  checks. Pairing still depends on upstream ChatGPT account auth and server-side
  entitlement.

After patching, the script also deletes any older `OpenAI.Codex_*_x64*` sibling
directories under `CodexDesktopPatched` (each stale Store version leaves ~1.6 GB
behind). The currently-patched version and the source under
`C:\Program Files\WindowsApps` are never touched.

## Daily Workflow

Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\patch-codex-desktop-recent-window.ps1 -Launch
```

Or double-click:

```text
Repatch-Codex.cmd
```

The script refreshes a `Codex Patched` shortcut on the Desktop and Start Menu.
After Microsoft Store updates Codex Desktop, run the patcher again.

## Soft-Refresh Workaround (Stuck Thread)

Separate from the recent-window issue, a thread can get stuck "topped up" with
no content updates after a cross-session CLI dispatch (e.g. session A delegates
to session B via `codex exec --resume <B>`). This is **not** a renderer cache
bug — Patch C v2 already prevents per-thread state from being overwritten — but
the running app-server (`codex-command-runner.exe`) holds an in-memory cache
that doesn't tail JSONL writes from external CLI processes. The renderer
requests `thread/turns/list` and receives the stale cache back.

Workaround: kill just the sidecar process. Electron main has a supervisor
(class `pu`) that auto-respawns it via `scheduleRestart`, and the renderer
(`AppServerConnection`, class `Of`) auto-reconnects via `reconnectTimer`. The
UI window stays open; only the Rust sidecar restarts and re-reads thread
state from disk.

**The sidecar process is `codex.exe` (lowercase) under
`<patched-app>\resources\codex.exe`, not `codex-command-runner.exe`.** Older
docs may name it differently; the refresh script targets either.

**Patch D is the original soft-refresh fix for pre-26.513 builds.** Without Patch D,
the renderer keeps its `conversations` Map across reconnect — even after the
sidecar restarts with fresh disk state, the UI still shows the stale cached
data. Patch D clears the Map on reconnect so the renderer re-fetches. On
upstream `26.513.x`, that same clear appears unsafe for some thread-open paths,
so the current release lane skips D on that upstream version.

```powershell
powershell -ExecutionPolicy Bypass -File .\refresh-codex-app-server.ps1
```

Or double-click:

```text
Refresh-Codex.cmd
```

Or click the Desktop shortcut `Refresh-Codex.lnk`. Effect is a ~1-3 second
visible disconnect, then the stuck thread shows its full content.

### Auto-Refresh Watchdog (for `codex resume -all` workflows)

The manual Refresh-Codex.lnk catches up the UI but you have to click it
yourself. If you run multiple external sessions in parallel via
`codex resume -all` or `codex exec --resume <id>` from a terminal, those
sessions write JSONL outside Codex Desktop's sidecar entirely. The sidecar
never tails the file and there is no event push to the renderer. You'd be
clicking Refresh constantly.

`auto-refresh-watchdog.ps1` is a PowerShell daemon that polls every N
seconds. When it detects a JSONL write newer than the current sidecar's
start time (i.e. the sidecar's cache is definitely stale), it kills the
sidecar. Electron's `pu` supervisor auto-respawns it, and on pre-26.513 lanes Patch D clears the
renderer cache, and the UI catches up. Throttled with a cooldown so it
won't kill more often than every 60s by default.

Start (hidden background):
```powershell
.\Start-Codex-Auto-Refresh.cmd
```

Stop:
```powershell
.\Stop-Codex-Auto-Refresh.cmd
```

Desktop shortcuts: `Start-Codex-Auto-Refresh.lnk`,
`Stop-Codex-Auto-Refresh.lnk`. Logs under
`%LOCALAPPDATA%\OpenAI\CodexDesktopPatched\logs\auto-refresh-watchdog.log`.

Tunables (pass as flags to the PS1 directly):
- `-Interval 15` (poll seconds)
- `-Cooldown 60` (min seconds between kills)
- `-StaleThresholdSec 30` (only act on JSONL writes within this recency)

## Verify

Run the auto-pagination patcher against the copied app:

```powershell
python .\patch_codex_asar_autopaginate.py --app-dir "%LOCALAPPDATA%\OpenAI\CodexDesktopPatched\<OpenAI.Codex_package>\app"
```

Expected result after a successful patch:

```json
{
  "status": "already_patched",
  "marker": "__cap=2000"
}
```

## Rollback

To stop using the patched copy, open the normal Codex Desktop app from Start
Menu.

To remove the patched copy entirely:

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\OpenAI\CodexDesktopPatched"
```

The Store app remains untouched.

## Notes

- This is a packaged-app patch for users blocked by the recent conversation
  window. Prefer an upstream Codex fix when available.
- Do not run this against `C:\Program Files\WindowsApps`.
- Backups are created next to the patched copied files before mutation.

## Patch I / `send_input` sidecar fix

Patch I is now included in the default Desktop lane. It does not edit `app.asar`; it source-builds the bundled `resources\codex.exe` sidecar from `openai/codex` and patches `core/src/tools/handlers/multi_agents_common.rs` so `parse_collab_input` treats `items: []` as `None` before validating `message` vs `items`.

Use the default patched release. A separate `-sendinput` suffix is no longer needed for the main lane. Install with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\CodexFromGithub\tools\Update-Codex.ps1" -Force -Tag v26.519.41501-patched
```

Verification target: a `functions.send_input` call serialized as `message` plus empty `items: []` should return a `submission_id` instead of `Provide either message or items, but not both`. Shared-sidecar dispatch via `codex-exec-remote.ps1` must continue to work.

## Patch N / persistent log churn guard

Patch N is included in the source-built sidecar lane to cover OpenAI Codex sources before the upstream persistent logging fix. It applies/verifies the same behavioral markers as OpenAI PRs #29432 and #29457:

- `codex-rs/codex-api/src/endpoint/responses_websocket.rs` must not log every WebSocket payload with `trace!("websocket event: {text}")`.
- `codex-rs/state/src/log_db.rs` must expose `log_db::default_filter()` with `log`, `codex_otel.log_only`, and `codex_otel.trace_safe` set to `LevelFilter::OFF`.
- `codex-rs/app-server/src/lib.rs` and `codex-rs/tui/src/lib.rs` must attach the persistent SQLite log layer with `log_db::default_filter()` instead of `Targets::new().with_default(Level::TRACE)`.

This is a self-retiring guard. Newer source refs that already include the upstream fix pass verification without modification, so it can remain in the workflow until the sidecar lane is permanently pinned to `rust-v0.142.0` or newer.

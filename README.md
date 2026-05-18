# Codex Desktop (Patched)

A patched build of OpenAI Codex Desktop that fixes:

1. **Recent-conversation visibility cap** — sidebar shows up to ~2000 threads instead of the default 50.
2. **Stuck thread updates after cross-session CLI dispatch** — UI keeps streaming when one Codex Desktop session delegates to another via `codex exec --resume <id>`.
3. **Stale renderer cache on sidecar restart** — the soft-refresh workflow (kill sidecar, Electron auto-respawns) actually refreshes UI content.
4. **External CLI invisibility** *(workaround)* — a watchdog daemon periodically restarts the sidecar when JSONL writes from external `codex resume` are detected.
5. **Shared-sidecar realtime UI** *(new)* — Desktop and CLI clients share one app-server sidecar over `ws://127.0.0.1:<PORT>`. Any dispatch from CLI (via the bundled `codex-exec-remote.ps1`) streams into Desktop's UI in real time (spinner + token-by-token) — no more polling or sidecar restarts needed for the common Planner -> Worker flow.
6. **Renderer directive crash guard** — Windows paths inside app directives are normalized before markdown directive parsing so a single persisted directive cannot crash the whole thread view.

The patches are **derived patches** applied on top of upstream binary releases:

- Source binary: [Haleclipse/CodexDesktop-Rebuild](https://github.com/Haleclipse/CodexDesktop-Rebuild) — a cross-platform repackage of OpenAI's Codex Desktop.
- This repo holds **only the patcher scripts + automation**. The output is a `Codex-Patched-win-x64-*.zip` published as a Release.

## Install (end users)

1. Go to the [Releases page](https://github.com/ngojclee/codex-desktop/releases) and download the latest `CodexDesktop-Patched-win-x64-*.zip`.
2. Extract to `%LOCALAPPDATA%\CodexFromGithub\` (the launcher scripts assume this path; you can install elsewhere but you will have to edit them).
3. Launch via `tools\Launch-Codex.vbs` (or pin it to your desktop). The launcher:
   - Picks a free port in `24567..24600`
   - Starts a shared `codex.exe app-server --listen ws://127.0.0.1:<PORT>` in the background
   - Sets `CODEX_APP_SERVER_WS_URL` and launches `Codex.exe`
   - Cleans up the sidecar when the last `Codex.exe` process exits
   - Writes live state to `~/.codex/desktop-shared-app-server.json`
4. From any terminal, the bundled wrapper dispatches into the same sidecar so Desktop sees real-time updates:
   ```powershell
   & "$env:LOCALAPPDATA\CodexFromGithub\tools\codex-exec-remote.ps1" `
       -ThreadId <UUID> -Prompt "<text>"
   ```
   Replaces `codex exec resume <id> "<text>"` for the Planner -> Worker pattern when you want Desktop UI to show progress live.

Do not rely on Codex's internal `functions.send_input` tool as the primary cross-session dispatch path. Field evidence from 2026-05-18 showed that some Codex surfaces serialize `message` plus an empty `items: []`, and the backend rejects that shape with `Provide either message or items, but not both`. Other surfaces omit `items` and may work against the same target thread, so the behavior is surface-dependent. The supported path in this repo is the shared sidecar wrapper above.

The `Update-Codex.cmd` shortcut pulls the latest release and overlays it on the install dir, preserving `tools/`.

## Architecture

```
This repo (scripts only — no binaries)
├── patches/                 Python patchers, idempotent, pattern-based
│   ├── patch_codex_asar_recent_window.py     Patch A — limit:50 -> limit:1000
│   ├── patch_codex_electron_fuse.py          Patch B — disable asar integrity validation
│   ├── patch_codex_asar_autopaginate_v3.py   Patch C v3 — always-paginate to 2000
│   ├── patch_codex_asar_reconnect_clear.py   Patch D — clear conversations Map on reconnect
│   ├── patch_codex_asar_ws_socks_bypass.py   Patch G — bypass SOCKS5 in WS transport (shared sidecar)
│   └── patch_codex_asar_directive_windows_path.py Patch H — normalize directive Windows paths
├── runtime/                 Windows-side glue (.ps1, .cmd) for daily use
├── docs/HANDOFF.md          Long-form technical handoff
├── apply-all-patches.ps1    Orchestrator — runs all 4 patchers on a given app dir
└── .github/workflows/auto-repatch-release.yml   CI: detect upstream release, repatch, release
```

## How updates work

The patches identify functions in the renderer JS by **pattern match** on stable substrings (e.g. `` `thread/turns/list` ``, `markAllConversationsNeedResumeAfterReconnect(){...}`). When upstream releases a new minified bundle, these substrings tend to survive across versions because they are tied to RPC method names or class field names — not random minifier output.

GitHub Action `.github/workflows/auto-repatch-release.yml`:

1. Runs every 3h (or manually via `workflow_dispatch`).
2. Checks Haleclipse upstream for new release tag.
3. If our repo doesn't have that version yet -> downloads upstream Windows zip -> applies the compatible patch set via `apply-all-patches.ps1` -> verifies markers -> repackages -> publishes release.
4. If patterns no longer match (upstream refactored), the verification step fails loudly and the maintainer needs to update the patcher pattern strings.

This means: **upstream updates flow downstream automatically; our customizations re-apply themselves.**

## Add a new patch

1. Drop a new `patches/patch_codex_*.py` file. Use the existing patchers as templates.
2. Decide ordering: edit `apply-all-patches.ps1` to call your patcher in the right place.
3. Optionally extend the verification step in `.github/workflows/auto-repatch-release.yml`.
4. Commit + push. Trigger `workflow_dispatch` with `force=true` to rebuild the latest release with your new patch.

## Patches in detail

### Patch A — Recent-window limit bump

Renderer calls `listRecentThreads({limit:50})`. Patcher bumps the literal `50` to `1000`. Server clamps at 100 anyway, so Patch A on its own only gives 100 threads — but it sets up the substring `limit:1000*this.recentConversationPageCount` that Patch C v2 then finds.

### Patch B — Electron fuse flip

`Codex.exe` is built with the Electron fuse `EnableEmbeddedAsarIntegrityValidation` enabled (true for both Microsoft Store and Haleclipse rebuild). Without flipping it, any modification to `app.asar` causes the app to refuse to launch. Patcher locates fuse byte index 4 in the executable and flips it to `REMOVED`.

### Patch C v3 — Always-paginate

Rewrites `refetchThreadList` to loop `listRecentThreads({limit:100, cursor})` until `nextCursor` is exhausted or 2000 threads are loaded. Unlike v2 there is no `fetchedRecentConversations` guard — every refetch re-paginates. v2's guard caused the sidebar to shrink to a single page whenever an external `codex resume -all` triggered a refresh because the renderer kept the partial result. v3 trades the tiny extra cost of pagination for a stable sidebar.

### Patch D — Clear conversations Map on reconnect

When the renderer's `markAllConversationsNeedResumeAfterReconnect` runs (called when the sidecar reconnects), the existing logic only flipped a `resumeState` flag — the cached `conversations` Map was preserved with stale data. Patcher injects a clear: for every cached id, call `applyConversationState(id, null)`, and reset `fetchedRecentConversations=false`. Combined with the soft-refresh workflow (kill sidecar -> Electron respawns -> renderer reconnects -> Patch D fires -> UI re-fetches), the stuck thread gets a fresh snapshot from disk.

Note: upstream `26.513.x` changed renderer hydration behavior enough that Patch D now appears to trigger thread-open regressions for some sessions. `apply-all-patches.ps1` therefore auto-skips Patch D on `26.513.x` until a safer reconnect fix is found.

### Patch G — Bypass hardcoded SOCKS5 in WS transport

The WS app-server transport class hardcodes `agent: new SocksProxyAgent(\`socks5h://127.0.0.1:1080\`)` for every WebSocket connection. When `CODEX_APP_SERVER_WS_URL=ws://127.0.0.1:<PORT>` points Desktop at a local sidecar, the connection dials through a SOCKS proxy that doesn't exist and fails — and the renderer maps that failure to a login UI, which is misleading because the user is on apikey/cliproxy mode and the loopback `--ws-auth` is not even required. Patcher removes the `agent` option from the WS constructor (`th()` returns `{}` anyway, so no further tweak is needed) and the loopback connection succeeds. This unlocks the shared-sidecar pattern: Desktop and the bundled `codex-exec-remote.ps1` both attach to the same `app-server`, the sidecar broadcasts `item/agentMessage/delta` and `turn/completed` to every subscribed client, and any CLI dispatch shows up in Desktop's UI in real time.

### Patch H — Directive Windows path sanitizer

Renderer markdown parsing can throw on app directives that contain Windows paths, such as `::git-stage{cwd="D:\\Python\\projects\\codex-desktop"}`. The exception bubbles into the thread page error boundary even though the backend and JSONL are healthy. Patch H normalizes backslashes to forward slashes only on single-line Codex app directives before markdown parsing. It does not rewrite session files, normal prose, code blocks, or sidecar traffic.

## Runtime workflow

The release zip now bundles `tools/` next to `Codex.exe`. Day-to-day:

- **Launch** — double-click `tools\Launch-Codex.vbs` (or any shortcut pointing at it). Spawns a hidden shared sidecar, sets `CODEX_APP_SERVER_WS_URL`, runs `Codex.exe`, kills the sidecar when the last `Codex.exe` process exits.
- **Dispatch from terminal** — `tools\codex-exec-remote.ps1 -ThreadId <UUID> -Prompt "..."` round-trips a non-interactive turn through the shared sidecar via JSON-RPC. Streams `item/agentMessage/delta` to stdout and exits on `turn/completed`. Desktop UI shows the same spinner + tokens as if you typed in the UI. Prefer this over `functions.send_input` for cross-session work; `send_input` is an internal tool surface and has shown wrapper-specific serialization bugs.
- **Update** — `tools\Update-Codex.cmd` fetches the latest release zip and overlays it (preserving `tools/`).
- **Soft refresh / watchdog** *(only needed for legacy non-shared dispatches via `codex exec resume`)* — see [`docs/HANDOFF.md`](docs/HANDOFF.md).

State file: `~/.codex/desktop-shared-app-server.json` holds the live `ws_url`, `port`, `sidecar_pid`, and `log` path while Codex is running.

## Credits & License

- Upstream binary: [Haleclipse/CodexDesktop-Rebuild](https://github.com/Haleclipse/CodexDesktop-Rebuild) — cross-platform repackage of OpenAI Codex.
- Codex CLI (inside the asar): © OpenAI, [Apache-2.0](https://github.com/openai/codex).
- This repo's patcher scripts and glue: MIT (see [LICENSE](LICENSE)).

The released `CodexDesktop-Patched-*.zip` artifact is a binary derived from the upstream rebuild with our patches applied. Original copyright holders retain rights to their portions; the patches themselves are MIT.

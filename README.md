# Codex Desktop (Patched)

A patched build of OpenAI Codex Desktop that fixes:

1. **Recent-conversation visibility cap** — sidebar shows up to ~2000 threads instead of the default 50.
2. **Stuck thread updates after cross-session CLI dispatch** — UI keeps streaming when one Codex Desktop session delegates to another via `codex exec --resume <id>`.
3. **Stale renderer cache on sidecar restart** — the soft-refresh workflow (kill sidecar, Electron auto-respawns) actually refreshes UI content.
4. **External CLI invisibility** *(workaround, not a true patch)* — a watchdog daemon periodically restarts the sidecar when JSONL writes from `codex resume -all` or similar terminal commands are detected.

The patches are **derived patches** applied on top of upstream binary releases:

- Source binary: [Haleclipse/CodexDesktop-Rebuild](https://github.com/Haleclipse/CodexDesktop-Rebuild) — a cross-platform repackage of OpenAI's Codex Desktop.
- This repo holds **only the patcher scripts + automation**. The output is a `Codex-Patched-win-x64-*.zip` published as a Release.

## Install (end users)

1. Go to the [Releases page](https://github.com/ngojclee/codex-desktop/releases) and download the latest `CodexDesktop-Patched-win-x64-*.zip`.
2. Extract to a folder of your choice (e.g. `%LOCALAPPDATA%\CodexDesktopPatched\` or `D:\Apps\CodexPatched\`).
3. Double-click `Codex.exe` to launch.
4. (Optional) Copy scripts from `runtime/` next to `Codex.exe` for soft-refresh and watchdog workflow — see [HANDOFF.md](docs/HANDOFF.md).

## Architecture

```
This repo (scripts only — no binaries)
├── patches/                 Python patchers, idempotent, pattern-based
│   ├── patch_codex_asar_recent_window.py    Patch A — limit:50 -> limit:1000
│   ├── patch_codex_electron_fuse.py         Patch B — disable asar integrity validation
│   ├── patch_codex_asar_autopaginate_v2.py  Patch C v2 — guarded auto-paginate to 2000
│   └── patch_codex_asar_reconnect_clear.py  Patch D — clear conversations Map on reconnect
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
3. If our repo doesn't have that version yet → downloads upstream Windows zip → applies all four patches via `apply-all-patches.ps1` → verifies markers → repackages → publishes release.
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

### Patch C v2 — Guarded auto-paginate

Rewrites `refetchThreadList` to loop `listRecentThreads({limit:100, cursor})` until `nextCursor` is exhausted or 2000 threads are loaded. Guarded by `this.fetchedRecentConversations` so the loop only runs on the **first** invocation per session — subsequent refetches behave like the 1-page original. This prevents a regression where the unconditional loop overwrote per-thread state during cross-session dispatch.

### Patch D — Clear conversations Map on reconnect

When the renderer's `markAllConversationsNeedResumeAfterReconnect` runs (called when the sidecar reconnects), the existing logic only flipped a `resumeState` flag — the cached `conversations` Map was preserved with stale data. Patcher injects a clear: for every cached id, call `applyConversationState(id, null)`, and reset `fetchedRecentConversations=false`. Combined with the soft-refresh workflow (kill sidecar → Electron respawns → renderer reconnects → Patch D fires → UI re-fetches), the stuck thread gets a fresh snapshot from disk.

## Runtime workflow

See [`docs/HANDOFF.md`](docs/HANDOFF.md) for the full daily-use guide, including:

- The soft-refresh script `refresh-codex-app-server.ps1` and its `Refresh-Codex.cmd` wrapper
- The auto-refresh watchdog daemon `auto-refresh-watchdog.ps1` (for `codex resume -all` workflows)
- One-click launcher `Launch-Codex-All.cmd` that starts the watchdog hidden then launches the app

## Credits & License

- Upstream binary: [Haleclipse/CodexDesktop-Rebuild](https://github.com/Haleclipse/CodexDesktop-Rebuild) — cross-platform repackage of OpenAI Codex.
- Codex CLI (inside the asar): © OpenAI, [Apache-2.0](https://github.com/openai/codex).
- This repo's patcher scripts and glue: MIT (see [LICENSE](LICENSE)).

The released `CodexDesktop-Patched-*.zip` artifact is a binary derived from the upstream rebuild with our patches applied. Original copyright holders retain rights to their portions; the patches themselves are MIT.

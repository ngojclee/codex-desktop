# Codex Desktop (Patched)

A patched build of OpenAI Codex Desktop that fixes:

1. **Recent-conversation visibility cap** — sidebar shows up to ~2000 threads instead of the default 50.
2. **Stuck thread updates after cross-session CLI dispatch** — UI keeps streaming when one Codex Desktop session delegates to another via `codex exec --resume <id>`.
3. **Stale renderer cache on sidecar restart** — the soft-refresh workflow (kill sidecar, Electron auto-respawns) actually refreshes UI content.
4. **External CLI invisibility** *(workaround)* — a watchdog daemon periodically restarts the sidecar when JSONL writes from external `codex resume` are detected.
5. **Shared-sidecar realtime UI** — Desktop and CLI clients share one app-server sidecar over `ws://127.0.0.1:<PORT>`. Any dispatch from CLI (via the bundled `codex-exec-remote.ps1`) streams into Desktop's UI in real time (spinner + token-by-token) — no more polling or sidecar restarts needed for the common Planner -> Worker flow.
6. **Renderer directive crash guard** — Windows paths inside app directives are normalized before markdown directive parsing so a single persisted directive cannot crash the whole thread view.
7. **`send_input` empty-items fix** — the default release now ships a source-patched `resources/codex.exe` that treats `items: []` as absent before validating mutually-exclusive `message` vs `items`.
8. **Computer Use unlock (Any App + Google Chrome)** — bypasses Statsig feature gates and build-flavor restrictions that block Computer Use on non-internal builds and restricted regions. Google Chrome CUA works immediately; Any App requires upstream 26.527+ which ships the Windows CUA binary.

The patches are **derived patches** applied on top of upstream binary releases:

- Source binary: [ngojclee/codex-desktop-rebuild](https://github.com/ngojclee/codex-desktop-rebuild) — our public unpatched rebuild fork of [Haleclipse/CodexDesktop-Rebuild](https://github.com/Haleclipse/CodexDesktop-Rebuild).
- This repo holds **only the patcher scripts + automation**. The output is a `CodexDesktop-Patched-win-x64-*.zip` published as a Release.

## Install (end users)

### Quick install (PowerShell, no `gh` required)

Use this on a fresh Windows machine. It downloads the latest public patched
release from GitHub and extracts it to `%LOCALAPPDATA%\CodexFromGithub`.
It creates the standard desktop shortcuts:
`Codex (GitHub Patched)`, `Update-Codex`, and `Codex Dev (GitHub Patched)`.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command '& {
  $ErrorActionPreference = "Stop"
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

  $repo = "ngojclee/codex-desktop"
  $installDir = Join-Path $env:LOCALAPPDATA "CodexFromGithub"
  $zip = Join-Path $env:TEMP "codex-patched.zip"

  $release = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest"
  $asset = $release.assets |
    Where-Object { $_.name -like "CodexDesktop-Patched-win-x64-*.zip" } |
    Select-Object -First 1
  if (-not $asset) {
    throw "No Windows patched zip found in release $($release.tag_name)"
  }

  try {
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip

    $actualSize = (Get-Item -LiteralPath $zip).Length
    if ($actualSize -ne [int64]$asset.size) {
      throw "Download incomplete: got $actualSize bytes, expected $($asset.size)"
    }

    New-Item -ItemType Directory -Force -Path $installDir | Out-Null
    tar -xf $zip -C $installDir
    if ($LASTEXITCODE -ne 0) {
      throw "tar extraction failed"
    }

    $versionFile = Join-Path $installDir "tools\.version-tag"
    if (Test-Path -LiteralPath (Split-Path -Parent $versionFile)) {
      $release.tag_name | Set-Content -LiteralPath $versionFile -NoNewline
    }
  }
  finally {
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
  }

  function Get-DesktopPath {
    $candidates = @([Environment]::GetFolderPath("Desktop"))
    $shellDesktop = (Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders" -ErrorAction SilentlyContinue).Desktop
    if ($shellDesktop) { $candidates += $shellDesktop }
    if ($env:OneDrive) { $candidates += (Join-Path $env:OneDrive "Desktop") }
    $candidates += (Join-Path $env:USERPROFILE "OneDrive\Desktop")
    $candidates += (Join-Path $env:USERPROFILE "Desktop")

    foreach ($candidate in ($candidates | Where-Object { $_ })) {
      $expanded = [Environment]::ExpandEnvironmentVariables($candidate)
      if (Test-Path -LiteralPath $expanded) { return $expanded }
    }

    $fallback = Join-Path $env:USERPROFILE "Desktop"
    New-Item -ItemType Directory -Force -Path $fallback | Out-Null
    return $fallback
  }

  $desktop = Get-DesktopPath
  $icon = Join-Path $installDir "Codex.exe"
  $ws = New-Object -ComObject WScript.Shell
  function New-CodexShortcut([string]$Name, [string]$TargetPath) {
    if (-not (Test-Path -LiteralPath $TargetPath)) { return }
    $path = Join-Path $desktop $Name
    if (Test-Path -LiteralPath $path) { return }
    $sc = $ws.CreateShortcut($path)
    $sc.TargetPath = $TargetPath
    $sc.WorkingDirectory = Split-Path $TargetPath
    if (Test-Path -LiteralPath $icon) { $sc.IconLocation = "$icon,0" }
    $sc.Save()
  }

  New-CodexShortcut "Codex (GitHub Patched).lnk" (Join-Path $installDir "tools\Launch-Codex.vbs")
  New-CodexShortcut "Update-Codex.lnk" (Join-Path $installDir "tools\Update-Codex.cmd")
  New-CodexShortcut "Codex Dev (GitHub Patched).lnk" (Join-Path $installDir "tools\Launch-Codex-Dev.vbs")

  Write-Host "Installed $($release.tag_name) to: $installDir"
  Write-Host "Standard shortcuts ensured on: $desktop"
}'
```

The outer `-Command` argument uses single quotes on purpose. If you use double
quotes there while pasting into an existing PowerShell window, the parent shell
expands `$zip`, `$release`, `$_`, etc. before the installer runs.

This command uses Windows `tar.exe` instead of `Expand-Archive`; the release zip
is large, and some Windows PowerShell archive builds can mis-detect it as a
split/spanned archive.

### Quick install (GitHub CLI)

If you already have [`gh`](https://cli.github.com/) installed and authenticated,
this is the shortest install path. Use this for private/authenticated release
access; the no-`gh` installer above is preferred for the public repo.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command '& {
  $ErrorActionPreference = "Stop"

  $repo = "ngojclee/codex-desktop"
  $installDir = Join-Path $env:LOCALAPPDATA "CodexFromGithub"
  $downloadDir = Join-Path $env:TEMP "codex-patched-release"

  try {
    Remove-Item -LiteralPath $downloadDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
    New-Item -ItemType Directory -Force -Path $installDir | Out-Null

    gh release download --repo $repo --pattern "CodexDesktop-Patched-win-x64-*.zip" --dir $downloadDir --clobber
    if ($LASTEXITCODE -ne 0) {
      throw "gh release download failed"
    }

    $zip = Get-ChildItem -LiteralPath $downloadDir -Filter "CodexDesktop-Patched-win-x64-*.zip" | Select-Object -First 1
    if (-not $zip) {
      throw "No downloaded Windows patched zip found"
    }

    tar -xf $zip.FullName -C $installDir
    if ($LASTEXITCODE -ne 0) {
      throw "tar extraction failed"
    }

    $versionFile = Join-Path $installDir "tools\.version-tag"
    if (Test-Path -LiteralPath (Split-Path -Parent $versionFile)) {
      (gh release view --repo $repo --json tagName --jq ".tagName") | Set-Content -LiteralPath $versionFile -NoNewline
    }
  }
  finally {
    Remove-Item -LiteralPath $downloadDir -Recurse -Force -ErrorAction SilentlyContinue
  }

  function Get-DesktopPath {
    $candidates = @([Environment]::GetFolderPath("Desktop"))
    $shellDesktop = (Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders" -ErrorAction SilentlyContinue).Desktop
    if ($shellDesktop) { $candidates += $shellDesktop }
    if ($env:OneDrive) { $candidates += (Join-Path $env:OneDrive "Desktop") }
    $candidates += (Join-Path $env:USERPROFILE "OneDrive\Desktop")
    $candidates += (Join-Path $env:USERPROFILE "Desktop")

    foreach ($candidate in ($candidates | Where-Object { $_ })) {
      $expanded = [Environment]::ExpandEnvironmentVariables($candidate)
      if (Test-Path -LiteralPath $expanded) { return $expanded }
    }

    $fallback = Join-Path $env:USERPROFILE "Desktop"
    New-Item -ItemType Directory -Force -Path $fallback | Out-Null
    return $fallback
  }

  $desktop = Get-DesktopPath
  $icon = Join-Path $installDir "Codex.exe"
  $ws = New-Object -ComObject WScript.Shell
  function New-CodexShortcut([string]$Name, [string]$TargetPath) {
    if (-not (Test-Path -LiteralPath $TargetPath)) { return }
    $path = Join-Path $desktop $Name
    if (Test-Path -LiteralPath $path) { return }
    $sc = $ws.CreateShortcut($path)
    $sc.TargetPath = $TargetPath
    $sc.WorkingDirectory = Split-Path $TargetPath
    if (Test-Path -LiteralPath $icon) { $sc.IconLocation = "$icon,0" }
    $sc.Save()
  }

  New-CodexShortcut "Codex (GitHub Patched).lnk" (Join-Path $installDir "tools\Launch-Codex.vbs")
  New-CodexShortcut "Update-Codex.lnk" (Join-Path $installDir "tools\Update-Codex.cmd")
  New-CodexShortcut "Codex Dev (GitHub Patched).lnk" (Join-Path $installDir "tools\Launch-Codex-Dev.vbs")

  Write-Host "Installed latest patched release to: $installDir"
  Write-Host "Standard shortcuts ensured on: $desktop"
}'
```

### Quick update (existing install)

The updater is available after install because it ships in `tools/`. It updates
the same `%LOCALAPPDATA%\CodexFromGithub` install directory and leaves
`~/.codex/` sessions/config untouched.
For a public repo it downloads through the GitHub Releases API with no token.
If the repo is private or the public asset request is denied, it falls back to
authenticated `gh` automatically. After a successful update it checks the
standard desktop shortcuts and creates any missing ones without overwriting
existing shortcuts:
`Codex (GitHub Patched)`, `Update-Codex`, and `Codex Dev (GitHub Patched)`.

```powershell
& "$env:LOCALAPPDATA\CodexFromGithub\tools\Update-Codex.ps1" -Force
```

### Create desktop shortcuts

Run this separately when you also want the optional log launcher shortcut, or
when you want to recreate/repair shortcuts on another Windows profile. It
creates these desktop shortcuts when the matching launcher exists:
`Codex (GitHub Patched Logs)`, `Codex (GitHub Patched)`, `Update-Codex`, and
`Codex Dev (GitHub Patched)`.

```powershell
function Get-DesktopPath {
  $candidates = @([Environment]::GetFolderPath("Desktop"))
  $shellDesktop = (Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders" -ErrorAction SilentlyContinue).Desktop
  if ($shellDesktop) { $candidates += $shellDesktop }
  if ($env:OneDrive) { $candidates += (Join-Path $env:OneDrive "Desktop") }
  $candidates += (Join-Path $env:USERPROFILE "OneDrive\Desktop")
  $candidates += (Join-Path $env:USERPROFILE "Desktop")

  foreach ($candidate in ($candidates | Where-Object { $_ })) {
    $expanded = [Environment]::ExpandEnvironmentVariables($candidate)
    if (Test-Path -LiteralPath $expanded) {
      return $expanded
    }
  }

  $fallback = Join-Path $env:USERPROFILE "Desktop"
  New-Item -ItemType Directory -Force -Path $fallback | Out-Null
  return $fallback
}

$desktop = Get-DesktopPath
$target = "$env:LOCALAPPDATA\CodexFromGithub\tools\Launch-Codex.vbs"
$logTarget = "$env:LOCALAPPDATA\CodexFromGithub\tools\Launch-Codex-Logs.vbs"
$devTarget = "$env:LOCALAPPDATA\CodexFromGithub\tools\Launch-Codex-Dev.vbs"
$updateTarget = "$env:LOCALAPPDATA\CodexFromGithub\tools\Update-Codex.cmd"
$icon = "$env:LOCALAPPDATA\CodexFromGithub\Codex.exe"

if (-not (Test-Path -LiteralPath $target)) {
  throw "Missing launcher: $target"
}
if (-not (Test-Path -LiteralPath $icon)) {
  throw "Missing icon exe: $icon"
}

$ws = New-Object -ComObject WScript.Shell
function New-CodexShortcut([string]$Name, [string]$TargetPath) {
  if (-not (Test-Path -LiteralPath $TargetPath)) { return }
  $path = Join-Path $desktop $Name
  if (Test-Path -LiteralPath $path) { return }
  $sc = $ws.CreateShortcut($path)
  $sc.TargetPath = $TargetPath
  $sc.WorkingDirectory = Split-Path $TargetPath
  $sc.IconLocation = "$icon,0"
  $sc.Save()
}

New-CodexShortcut "Codex (GitHub Patched).lnk" $target
New-CodexShortcut "Codex (GitHub Patched Logs).lnk" $logTarget
New-CodexShortcut "Update-Codex.lnk" $updateTarget
New-CodexShortcut "Codex Dev (GitHub Patched).lnk" $devTarget
```

### Manual install

1. Go to the [Releases page](https://github.com/ngojclee/codex-desktop/releases) and download the latest `CodexDesktop-Patched-win-x64-*.zip`.
2. Extract to `%LOCALAPPDATA%\CodexFromGithub\` (the launcher scripts assume this path; you can install elsewhere but you will have to edit them).
3. Launch via `tools\Launch-Codex.vbs` (or pin it to your desktop). Use
   `tools\Launch-Codex-Logs.vbs` when you want a visible sidecar log window,
   or `tools\Launch-Codex-Dev.vbs` when you specifically want to probe the
   Dev build-flavor lane.
   The launcher:
   - Picks a free port in `24567..24600`
   - Starts a shared `codex.exe app-server --listen ws://127.0.0.1:<PORT>` in the background
   - Sets `CODEX_APP_SERVER_WS_URL` and launches `Codex.exe`
   - Cleans up the sidecar when the last `Codex.exe` process exits
   - Writes live state to `~/.codex/desktop-shared-app-server.json`
4. From any terminal, the bundled wrapper dispatches into the same sidecar so Desktop sees real-time updates:
   ```powershell
   & "$env:LOCALAPPDATA\CodexFromGithub\tools\codex-exec-remote.ps1" `
       -ThreadId "019df565-7953-7bf2-af3e-cea3c59cc576" -Prompt "ping"
   ```
   Replaces `codex exec resume <id> "<text>"` for the Planner -> Worker pattern when you want Desktop UI to show progress live.
5. If `~\.codex\skills` is a symlink/junction to a NAS, SMB share, or shared
   drive, keep Codex's generated system skills local per machine. Close Codex,
   run the repair dry-run, then apply only if the plan looks right:
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\CodexFromGithub\tools\Repair-Codex-SystemSkills.ps1"
   powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\CodexFromGithub\tools\Repair-Codex-SystemSkills.ps1" -Apply
   ```
   This replaces the single shared `skills` link with a local `skills`
   directory, keeps shared user skills as individual links, and leaves
   `~\.codex\skills\.system` as a real local directory.
   If Windows cannot create directory symlinks, run the apply command from an
   elevated PowerShell or append `-CopySharedSkills` to make local copies of
   shared skills instead of links.

Do not rely on Codex's internal `functions.send_input` tool as the primary cross-session dispatch path. Field evidence from 2026-05-18 showed that some Codex surfaces serialize `message` plus an empty `items: []`, and the backend rejects that shape with `Provide either message or items, but not both`. Other surfaces omit `items` and may work against the same target thread, so the behavior is surface-dependent. The supported path in this repo is the shared sidecar wrapper above.

The `Update-Codex.cmd` shortcut pulls the latest release and overlays it on the install dir, preserving `tools/`. Use `tools\Update-Codex.ps1 -Tag <release-tag>` only when you want to pin to a specific release. The updater ensures the three standard desktop shortcuts exist and leaves existing shortcuts alone; use the shortcut snippet above when you also want the optional Logs shortcut.

## Architecture

```
This repo (scripts only — no binaries)
├── patches/                 Python patchers, idempotent, pattern-based
│   ├── patch_codex_asar_recent_window.py     Patch A — limit:50 -> limit:1000
│   ├── patch_codex_electron_fuse.py          Patch B — disable asar integrity validation
│   ├── patch_codex_asar_autopaginate_v3.py   Patch C v3 — always-paginate to 2000
│   ├── patch_codex_asar_reconnect_clear.py   Patch D — clear conversations Map on reconnect
│   ├── patch_codex_asar_ws_socks_bypass.py   Patch G — bypass SOCKS5 in WS transport (shared sidecar)
│   ├── patch_codex_asar_directive_windows_path.py Patch H — normalize directive Windows paths
│   ├── patch_codex_asar_computer_use_gate.py Patch J — bypass Statsig gates for Computer Use
│   ├── patch_codex_asar_codex_mobile_gate.py Patch K — expose Codex mobile setup
│   └── patch_codex_plugin_scoped_node_modules.py Patch L — decode plugin `%40` package folders
├── Patch I                 Source-built sidecar fix for `functions.send_input` `items: []`
├── runtime/                 Windows-side glue (.ps1, .cmd) for daily use
├── docs/HANDOFF.md          Long-form technical handoff
├── apply-all-patches.ps1    Orchestrator — runs the patch set on a given app dir
└── .github/workflows/auto-repatch-release.yml   CI: detect upstream release, repatch, release
```

## How updates work

The patches identify functions in the renderer JS by **pattern match** on stable substrings (e.g. `` `thread/turns/list` ``, `markAllConversationsNeedResumeAfterReconnect(){...}`). When upstream releases a new minified bundle, these substrings tend to survive across versions because they are tied to RPC method names or class field names — not random minifier output.

GitHub Action `.github/workflows/auto-repatch-release.yml`:

1. Runs every 3h (or manually via `workflow_dispatch`).
2. Checks our unpatched rebuild upstream (`ngojclee/codex-desktop-rebuild` by default) for a new release tag.
3. If our repo doesn't have that version yet -> downloads upstream Windows zip -> applies the compatible patch set via `apply-all-patches.ps1` -> verifies markers -> repackages -> publishes release.
4. If patterns no longer match (upstream refactored), the verification step fails loudly and the maintainer needs to update the patcher pattern strings.
5. Manual `workflow_dispatch` can still publish isolated lanes with `release_suffix` if needed, but the default lane already includes Patch I and no longer needs a separate `-sendinput` tag.

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

Classic Electron builds ship `Codex.exe` with the Electron fuse `EnableEmbeddedAsarIntegrityValidation` enabled. Without flipping it, any modification to `app.asar` causes the app to refuse to launch. Patcher locates fuse byte index 4 in the executable and flips it to `REMOVED`. Newer Owl shell Windows builds do not contain the Electron fuse sentinel; Patch B detects that layout and safely skips because there is no Electron fuse to flip.

### Patch C v3 — Always-paginate

Rewrites `refetchThreadList` to loop `listRecentThreads({limit:100, cursor})` until `nextCursor` is exhausted or 2000 threads are loaded. Unlike v2 there is no `fetchedRecentConversations` guard — every refetch re-paginates. v2's guard caused the sidebar to shrink to a single page whenever an external `codex resume -all` triggered a refresh because the renderer kept the partial result. v3 trades the tiny extra cost of pagination for a stable sidebar.

### Patch D — Clear conversations Map on reconnect

When the renderer's `markAllConversationsNeedResumeAfterReconnect` runs (called when the sidecar reconnects), the existing logic only flipped a `resumeState` flag — the cached `conversations` Map was preserved with stale data. Patcher injects a clear: for every cached id, call `applyConversationState(id, null)`, and reset `fetchedRecentConversations=false`. Combined with the soft-refresh workflow (kill sidecar -> Electron respawns -> renderer reconnects -> Patch D fires -> UI re-fetches), the stuck thread gets a fresh snapshot from disk.

Note: upstream `26.513.x` changed renderer hydration behavior enough that Patch D now appears to trigger thread-open regressions for some sessions. `apply-all-patches.ps1` therefore auto-skips Patch D on `26.513.x` until a safer reconnect fix is found.

### Patch G — Bypass hardcoded SOCKS5 in WS transport

The WS app-server transport class hardcodes `agent: new SocksProxyAgent(\`socks5h://127.0.0.1:1080\`)` for every WebSocket connection. When `CODEX_APP_SERVER_WS_URL=ws://127.0.0.1:<PORT>` points Desktop at a local sidecar, the connection dials through a SOCKS proxy that doesn't exist and fails — and the renderer maps that failure to a login UI, which is misleading because the user is on apikey/cliproxy mode and the loopback `--ws-auth` is not even required. Patcher removes the `agent` option from the WS constructor (`th()` returns `{}` anyway, so no further tweak is needed) and the loopback connection succeeds. This unlocks the shared-sidecar pattern: Desktop and the bundled `codex-exec-remote.ps1` both attach to the same `app-server`, the sidecar broadcasts `item/agentMessage/delta` and `turn/completed` to every subscribed client, and any CLI dispatch shows up in Desktop's UI in real time.

### Patch H — Directive Windows path sanitizer

Renderer markdown parsing can throw on app directives that contain Windows paths, such as `::git-stage{cwd="D:\\Python\\projects\\codex-desktop"}`. The exception bubbles into the thread page error boundary even though the backend and JSONL are healthy. Patch H normalizes backslashes to forward slashes only on single-line Codex app directives before markdown parsing. It does not rewrite session files, normal prose, code blocks, or sidecar traffic.


### Patch I — `send_input` empty-items sidecar fix

Patch I is now part of the default stable lane. The failure lives in the bundled Rust sidecar/CLI (`resources\codex.exe`): some Codex tool adapters serialize `functions.send_input` as `message` plus `items: []`, and the backend rejects that as "Provide either message or items, but not both". The release pipeline now builds `openai/codex` from source and inserts one normalization line in `parse_collab_input`: empty `items` becomes absent before mutual-exclusion validation. No separate `-sendinput` lane is required for the default release.

### Patch J -- Computer Use gate bypass

Computer Use (Any App + Google Chrome) is blocked on non-internal builds by three layers:

1. **Build flavor gate** -- the bundled plugin reconciliation requires isInternal(buildFlavor) on Windows. The Haleclipse rebuild ships codexBuildFlavor=prod which fails this check. The default launcher sets BUILD_FLAVOR=owl so it stays on the Owl shell lane while passing the internal-build gate; `Launch-Codex-Dev.vbs` uses the same shared-sidecar launcher with BUILD_FLAVOR=dev for feature probing.
2. **Feature flag** -- features.computerUse must be true. The launcher sets CODEX_ELECTRON_ENABLE_WINDOWS_COMPUTER_USE=1 to force it.
3. **Statsig feature gates** -- the renderer checks three server-side gates before enabling the UI toggles. Patcher replaces each gate check with !0 (true) using a flexible regex that matches any minified function name wrapping the gate ID.

Gate IDs bypassed:
- 1506311413 -- computer_use (Any App)
- 410065390 -- browser_use_external (Google Chrome)
- 410262010 -- browser_use (In-app Browser)

The replacement is same-length (padded with spaces), so no ASAR repack is needed. The patcher uses regex [a-zA-Z_] + backtick-wrapped ID to handle minifier renaming across builds.

Note: Google Chrome CUA works immediately. Any App requires upstream 26.527+ which ships codex-computer-use.exe (the Windows CUA helper binary). Earlier builds do not include this binary.

The launcher also clears the generated `~/.codex/.tmp/bundled-marketplaces/openai-bundled` cache when a 26.527+ bundle contains `computer-use` but the runtime marketplace was generated without it, forcing Desktop to reconcile the bundled plugin list again.

### Patch K -- Codex mobile setup entrypoint

Recent Codex Desktop bundles include the Codex mobile route (`/codex-mobile`) and setup flow, but the sidebar entrypoint is hidden behind remote-control feature gates. Patch K exposes the local setup entrypoint by relaxing the renderer sidebar gate and bypassing the two related Statsig gates:

- 1042620455 -- remote-control feature visibility
- 2798711298 -- Codex mobile onboarding

This does not bypass the actual pairing backend. The setup flow still calls the upstream ChatGPT/WHAM remote-control APIs and will require a ChatGPT-authenticated account with server-side access. If the account is not entitled, the UI can be opened but pairing may still fail or redirect to login.

### Patch L -- Computer Use package folder decode fix

Some rebuild zips can extract scoped npm packages with the scope percent-escaped,
for example `node_modules\%40oai\sky`. The Computer Use plugin imports
`../node_modules/@oai/sky/...` and dynamically imports `@oai/sky`, so Node
resolution requires the real decoded folder names. Patch L renames direct
percent-escaped package folders such as `node_modules\%40*`,
`.pnpm\%40rollup_plugin-typescript%401_...`, and `.pnpm\objc-js%401.5.0`
back to their decoded names and verifies
`resources\plugins\openai-bundled\plugins\computer-use\node_modules\@oai\sky`
exists when the Computer Use plugin is bundled.

## Runtime workflow

The release zip now bundles `tools/` next to `Codex.exe`. Day-to-day:

- **Launch** — double-click `tools\Launch-Codex.vbs` (or any shortcut pointing at it). Spawns a hidden shared sidecar, sets `CODEX_APP_SERVER_WS_URL`, runs `Codex.exe`, kills the sidecar when the last `Codex.exe` process exits.
- **Launch with logs** — double-click `tools\Launch-Codex-Logs.vbs`. Fresh launches show the shared sidecar console. If Codex is already running on the shared sidecar, it opens a tail window for the current sidecar log and focuses the app.
- **Launch Dev lane** — double-click `tools\Launch-Codex-Dev.vbs`. This uses the same shared-sidecar launcher but passes `-BuildFlavor dev`; keep the normal Owl shortcut for daily use and use Dev only for feature probing.
- **Dispatch from terminal** — `tools\codex-exec-remote.ps1 -ThreadId <UUID> -Prompt "..."` round-trips a non-interactive turn through the shared sidecar via JSON-RPC. Streams `item/agentMessage/delta` to stdout and exits on `turn/completed`. Desktop UI shows the same spinner + tokens as if you typed in the UI. Prefer this over `functions.send_input` for cross-session work; `send_input` is an internal tool surface and has shown wrapper-specific serialization bugs.
- **Repair system skills** — if sidecar logs show `failed to install system skills` or `failed to read skills dir ...\.codex\skills\.system`, run `tools\Repair-Codex-SystemSkills.ps1` once. This is for setups where `~\.codex\skills` points at a network/share path; generated `.system` skills should stay local on each Windows machine.
- **Update** — `tools\Update-Codex.cmd` fetches the latest release zip and overlays it (preserving `tools/`). Public repos download without `gh`; private/authenticated repos fall back to `gh`. Missing standard desktop shortcuts are created after update.
- **Soft refresh / watchdog** *(only needed for legacy non-shared dispatches via `codex exec resume`)* — see [`docs/HANDOFF.md`](docs/HANDOFF.md).

State file: `~/.codex/desktop-shared-app-server.json` holds the live `ws_url`, `port`, `sidecar_pid`, and `log` path while Codex is running.

## Credits & License

- Upstream binary: [ngojclee/codex-desktop-rebuild](https://github.com/ngojclee/codex-desktop-rebuild) — our unpatched rebuild lane, forked from [Haleclipse/CodexDesktop-Rebuild](https://github.com/Haleclipse/CodexDesktop-Rebuild).
- Codex CLI (inside the asar): © OpenAI, [Apache-2.0](https://github.com/openai/codex).
- This repo's patcher scripts and glue: MIT (see [LICENSE](LICENSE)).

The released `CodexDesktop-Patched-*.zip` artifact is a binary derived from the upstream rebuild with our patches applied. Original copyright holders retain rights to their portions; the patches themselves are MIT.

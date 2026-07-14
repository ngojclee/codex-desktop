# Apply all Codex Desktop patches to an extracted app directory.
#
# Usage:
#   .\apply-all-patches.ps1 -AppDir 'C:\path\to\extracted\rebuild'
#   .\apply-all-patches.ps1 -AppDir '...\CodexRebuildTest' -Limit 1000 -SkipFuse
#
# Expects the AppDir to contain:
#   <AppDir>\Codex.exe
#   <AppDir>\resources\app.asar
#
# Order matters: A (limit-bump) must run before C v3 (auto-paginate) because
# C v3 expects A's `limit:1000*pageCount` pattern. D is independent.
# B (Electron fuse flip) is needed on builds that bake
# `EnableEmbeddedAsarIntegrityValidation=ENABLE` into the Codex.exe — both the
# official Microsoft Store build and the Haleclipse rebuild do. Without B the
# app refuses to start after asar mutation.

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$AppDir,

    [int]$Limit = 1000,

    [switch]$SkipFuse,

    [switch]$SkipA,
    [switch]$SkipC,
    [switch]$SkipD,
    [switch]$SkipG,
    [switch]$SkipM,
    [switch]$SkipH,
    [switch]$SkipJ,
    [switch]$SkipK,
    [switch]$SkipL,
    [switch]$SkipO,
    [switch]$SkipP,
    [switch]$SkipQ,
    [switch]$SkipR,

    [string]$UpstreamTag
)

$ErrorActionPreference = 'Stop'

$AppDir = (Resolve-Path -LiteralPath $AppDir).Path
$asar = Join-Path $AppDir 'resources\app.asar'
$exe  = Join-Path $AppDir 'Codex.exe'

if (-not (Test-Path -LiteralPath $asar)) { throw "Missing asar: $asar" }
if (-not (Test-Path -LiteralPath $exe))  { throw "Missing Codex.exe: $exe" }

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$patchesDir = Join-Path $scriptDir 'patches'

function Run-Patch($pyName, $argList, $label) {
    $py = Join-Path $patchesDir $pyName
    if (-not (Test-Path -LiteralPath $py)) { throw "Missing patcher: $py" }
    Write-Host ""
    Write-Host "==> $label ($pyName)" -ForegroundColor Cyan
    & python $py @argList
    if ($LASTEXITCODE -ne 0) { throw "$label FAILED (exit $LASTEXITCODE)" }
}

if (-not $SkipA) {
    Run-Patch 'patch_codex_asar_recent_window.py' @('--app-dir', $AppDir, '--limit', "$Limit") 'Patch A — limit bump 50 -> 1000'
}

if (-not $SkipFuse) {
    Run-Patch 'patch_codex_electron_fuse.py' @('--exe', $exe) 'Patch B — Electron asar-integrity fuse flip'
}

if (-not $SkipC) {
    Run-Patch 'patch_codex_asar_autopaginate_v3.py' @('--app-dir', $AppDir) 'Patch C v3 — always-paginate (sidebar > 100 threads, no v2 guard)'
}

$autoSkipD = $false
if ($UpstreamTag -like 'v26.513.*') {
    $autoSkipD = $true
    Write-Host "Auto-skip Patch D for upstream $UpstreamTag (known renderer regression on 26.513.x)." -ForegroundColor Yellow
}

if (-not $SkipD -and -not $autoSkipD) {
    Run-Patch 'patch_codex_asar_reconnect_clear.py' @('--app-dir', $AppDir) 'Patch D — clear renderer cache on reconnect'
} elseif ($SkipD -or $autoSkipD) {
    Write-Host ""
    Write-Host '==> Patch D — skipped' -ForegroundColor Yellow
}

if (-not $SkipG) {
    Run-Patch 'patch_codex_asar_ws_socks_bypass.py' @('--app-dir', $AppDir) 'Patch G — WS transport SOCKS5 proxy bypass (enables shared-sidecar)'
}

if (-not $SkipM) {
    Run-Patch 'patch_codex_asar_ws_max_payload.py' @('--app-dir', $AppDir) 'Patch M — raise shared-sidecar WS max payload'
}

if (-not $SkipH) {
    Run-Patch 'patch_codex_asar_directive_windows_path.py' @('--app-dir', $AppDir) 'Patch H — markdown directive Windows path sanitizer'
}

if (-not $SkipJ) {
    Run-Patch 'patch_codex_asar_computer_use_gate.py' @('--app-dir', $AppDir) 'Patch J — bypass Statsig gates for Computer Use (Any App + Chrome)'
}

if (-not $SkipK) {
    Run-Patch 'patch_codex_asar_codex_mobile_gate.py' @('--app-dir', $AppDir) 'Patch K — expose Codex mobile setup entrypoint'
}

if (-not $SkipL) {
    Run-Patch 'patch_codex_plugin_scoped_node_modules.py' @('--app-dir', $AppDir) 'Patch L — decode plugin package folders (%40 -> @)'
}

if (-not $SkipO) {
    Run-Patch 'patch_codex_asar_model_availability_filter.py' @('--app-dir', $AppDir) 'Patch O — keep local catalog models visible through Statsig allowlist'
}

if (-not $SkipP) {
    Run-Patch 'patch_codex_asar_sol_max_effort.py' @('--app-dir', $AppDir) 'Patch P — add Sol Max to the compact Power slider'
}

if (-not $SkipQ) {
    Run-Patch 'patch_codex_asar_gpt_model_labels.py' @('--app-dir', $AppDir) 'Patch Q — preserve GPT prefixes in model labels'
}

if (-not $SkipR) {
    Run-Patch 'patch_codex_asar_custom_provider_fast_mode.py' @('--app-dir', $AppDir) 'Patch R — expose catalog-declared Fast selector for custom providers'
}

Write-Host ""
Write-Host "All requested patches applied." -ForegroundColor Green
Write-Host "Asar: $asar"
Write-Host "Exe : $exe"

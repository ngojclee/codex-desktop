param(
    [int]$Limit = 1000,
    [string]$TargetRoot = "$env:LOCALAPPDATA\OpenAI\CodexDesktopPatched",
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$patcher = Join-Path $scriptDir 'patch_codex_asar_recent_window.py'
$fusePatcher = Join-Path $scriptDir 'patch_codex_electron_fuse.py'
# Patch C v2: guarded auto-paginate. v1 unconditional loop overwrote per-thread
# realtime state when delegation A->B ran concurrently; v2 only paginates on the
# first refetchThreadList per session, then behaves like 1-page original.
$autoPaginatePatcher = Join-Path $scriptDir 'patch_codex_asar_autopaginate_v2.py'
# Patch D: clear renderer conversations map on sidecar reconnect, so the
# soft-refresh flow (kill codex.exe sidecar -> Electron respawn) actually
# results in a fresh thread view rather than the stale renderer cache.
$reconnectClearPatcher = Join-Path $scriptDir 'patch_codex_asar_reconnect_clear.py'
if (!(Test-Path -LiteralPath $patcher)) {
    throw "Missing patcher: $patcher"
}
if (!(Test-Path -LiteralPath $fusePatcher)) {
    throw "Missing fuse patcher: $fusePatcher"
}
if (!(Test-Path -LiteralPath $autoPaginatePatcher)) {
    throw "Missing auto-paginate patcher: $autoPaginatePatcher"
}
if (!(Test-Path -LiteralPath $reconnectClearPatcher)) {
    throw "Missing reconnect-clear patcher: $reconnectClearPatcher"
}

$appx = Get-AppxPackage -Name OpenAI.Codex -ErrorAction SilentlyContinue |
    Sort-Object Version -Descending |
    Select-Object -First 1

if ($null -ne $appx -and $appx.InstallLocation) {
    $packageName = $appx.PackageFullName
    $sourcePackageDir = $appx.InstallLocation
} else {
    $proc = Get-Process Codex -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like '*\OpenAI.Codex_*__2p2nqsd0c76g0\app\Codex.exe' } |
        Select-Object -First 1
    if ($null -eq $proc) {
        throw "Could not find OpenAI.Codex via Get-AppxPackage or running Codex process."
    }
    $sourcePackageDir = Split-Path -Parent (Split-Path -Parent $proc.Path)
    $packageName = Split-Path -Leaf $sourcePackageDir
}

$sourceAppDir = Join-Path $sourcePackageDir 'app'
if (!(Test-Path -LiteralPath (Join-Path $sourceAppDir 'resources\app.asar'))) {
    throw "Source app.asar not found under $sourceAppDir"
}

$resolvedTargetRoot = [System.IO.Path]::GetFullPath($TargetRoot)
$targetAppDir = Join-Path (Join-Path $resolvedTargetRoot $packageName) 'app'
$resolvedTargetAppDir = [System.IO.Path]::GetFullPath($targetAppDir)
if (!$resolvedTargetAppDir.StartsWith($resolvedTargetRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to copy outside target root: $resolvedTargetAppDir"
}

$logDir = Join-Path $env:LOCALAPPDATA 'OpenAI\CodexDesktopPatched\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("codex-desktop-recent-window-patch-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))

function Write-Log($Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    $line | Tee-Object -FilePath $log -Append
}

Write-Log "Source app: $sourceAppDir"
Write-Log "Target app: $resolvedTargetAppDir"
Write-Log "Limit: $Limit"

New-Item -ItemType Directory -Force -Path $resolvedTargetAppDir | Out-Null
Write-Log "Copying app files to patched user folder. First run can take a few minutes."

$robocopyArgs = @(
    $sourceAppDir,
    $resolvedTargetAppDir,
    '/E',
    '/COPY:DAT',
    '/DCOPY:DAT',
    '/R:1',
    '/W:1',
    '/NFL',
    '/NDL',
    '/NP'
)
& robocopy @robocopyArgs | Tee-Object -FilePath $log -Append
$rc = $LASTEXITCODE
if ($rc -gt 7) {
    throw "Robocopy failed with exit code $rc. See log: $log"
}
Write-Log "Copy complete with robocopy code $rc"

Write-Log "Patching copied app.asar"
python $patcher --app-dir $resolvedTargetAppDir --limit $Limit | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) {
    throw "Patch failed. See log: $log"
}

Write-Log "Disabling Electron ASAR integrity fuse on copied Codex.exe"
$copiedExe = Join-Path $resolvedTargetAppDir 'Codex.exe'
python $fusePatcher --exe $copiedExe | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) {
    throw "Fuse patch failed. See log: $log"
}

Write-Log "Rewriting refetchThreadList with guarded auto-paginate (v2 — fixes realtime-event regression)"
python $autoPaginatePatcher --app-dir $resolvedTargetAppDir | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) {
    throw "Auto-paginate v2 patch failed. See log: $log"
}

Write-Log "Patching markAllConversationsNeedResumeAfterReconnect to clear renderer cache (Patch D — soft-refresh fix)"
python $reconnectClearPatcher --app-dir $resolvedTargetAppDir | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) {
    throw "Reconnect-clear patch (D) failed. See log: $log"
}

# Cleanup older patched versions. Only keep the version we just patched —
# previous Store releases leave gigabyte-sized stale copies behind. We never
# delete the running version or the source under WindowsApps.
$siblings = Get-ChildItem -LiteralPath $resolvedTargetRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like 'OpenAI.Codex_*_x64*' -and $_.Name -ne $packageName }
foreach ($d in $siblings) {
    try {
        $size = (Get-ChildItem $d.FullName -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
        Write-Log ("Removing stale patched version: {0}  ({1:N0} MB)" -f $d.Name, ($size/1MB))
        Remove-Item -Recurse -Force -LiteralPath $d.FullName
    } catch {
        Write-Log "Failed to remove $($d.Name): $_"
    }
}

$launchCmd = Join-Path $resolvedTargetRoot 'Launch-Codex-Patched.cmd'
$launchVbs = Join-Path $resolvedTargetRoot 'Launch-Codex-Patched.vbs'
$codexExe = Join-Path $resolvedTargetAppDir 'Codex.exe'
@"
@echo off
start "" "$codexExe"
"@ | Set-Content -LiteralPath $launchCmd -Encoding ASCII

@"
' Hidden launcher for patched Codex Desktop. Avoids the brief cmd window flash.
Set sh = CreateObject("WScript.Shell")
cmd = """$launchCmd"""
sh.Run cmd, 0, False
"@ | Set-Content -LiteralPath $launchVbs -Encoding ASCII

Write-Log "Launch file: $launchCmd"
Write-Log "Hidden launcher: $launchVbs"

# Create / refresh the Codex Patched shortcuts (Desktop + Start Menu).
$shortcutName = 'Codex Patched.lnk'
$shortcutTargets = @(
    [System.IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), $shortcutName),
    [System.IO.Path]::Combine([Environment]::GetFolderPath('StartMenu'), 'Programs', $shortcutName)
)
$wshell = New-Object -ComObject WScript.Shell
foreach ($lnkPath in $shortcutTargets) {
    $parent = Split-Path -Parent $lnkPath
    if (!(Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $lnk = $wshell.CreateShortcut($lnkPath)
    $lnk.TargetPath       = 'wscript.exe'
    $lnk.Arguments        = "`"$launchVbs`""
    $lnk.WorkingDirectory = $resolvedTargetRoot
    $lnk.IconLocation     = "$codexExe,0"
    $lnk.Description      = 'Codex Desktop with patched recent-window limit'
    $lnk.WindowStyle      = 1
    $lnk.Save()
    Write-Log "Shortcut: $lnkPath"
}

Write-Log "Done. Close Store Codex Desktop, then click the 'Codex Patched' shortcut."

if ($Launch) {
    Write-Log "Launching patched Codex now."
    Start-Process -FilePath $codexExe
}

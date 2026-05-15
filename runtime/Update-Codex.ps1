# One-click updater for Codex (GitHub Patched).
#
# Pulls the latest release from ngojclee/codex-desktop, replaces the local
# install with the new build, preserves the tools/ folder and any user data
# that lives elsewhere (~/.codex/ is untouched — Codex stores sessions there).
#
# Requires: gh CLI authenticated as a user with access to the private repo.

[CmdletBinding()]
param(
    [string]$Repo = 'ngojclee/codex-desktop',
    [string]$InstallDir = "$env:LOCALAPPDATA\CodexFromGithub",
    [switch]$Force,        # update even if current version matches latest tag
    [switch]$NoLaunch      # don't auto-launch after update
)

$ErrorActionPreference = 'Stop'

# IMPORTANT: this script will rename the install dir. PowerShell holds a
# handle on its current working directory; if CWD is anywhere under the
# install dir (which it will be when launched via the shortcut), the rename
# fails. Move CWD out to %TEMP% before touching anything else.
Set-Location -LiteralPath $env:TEMP

function Log($m) { Write-Host "[$(Get-Date -Format HH:mm:ss)] $m" }

function Update-CodexShortcut {
    param([string]$InstallDir)

    $launcher = Join-Path $InstallDir 'tools\Launch-Codex.vbs'
    if (-not (Test-Path -LiteralPath $launcher)) { return }

    $shortcutPath = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\Codex.lnk'
    $shortcutDir = Split-Path -Parent $shortcutPath
    New-Item -ItemType Directory -Force -Path $shortcutDir | Out-Null

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $launcher
    $shortcut.Arguments = ''
    $shortcut.WorkingDirectory = Split-Path -Parent $launcher
    $shortcut.IconLocation = (Join-Path $InstallDir 'Codex.exe') + ',0'
    $shortcut.Save()
}

# Resolve current installed version from tools/.version-tag if present
$versionFile = Join-Path $InstallDir 'tools\.version-tag'
$currentTag = if (Test-Path -LiteralPath $versionFile) { (Get-Content $versionFile -Raw).Trim() } else { '(unknown)' }
Log "Current installed tag: $currentTag"

# Check latest release on the remote.
# Parse JSON in PowerShell rather than jq — avoids quoting headaches with the
# regex pattern when the jq expression is passed through pwsh argument parser.
$releaseJson = gh release view --repo $Repo --json tagName,assets
if ($LASTEXITCODE -ne 0 -or -not $releaseJson) {
    throw "gh release view failed for $Repo (exit $LASTEXITCODE)"
}
$release = $releaseJson | ConvertFrom-Json
$assetMatch = $release.assets | Where-Object { $_.name -like 'CodexDesktop-Patched-win-x64-*.zip' } | Select-Object -First 1
if (-not $release.tagName -or -not $assetMatch) {
    throw "Could not find tag or matching asset on latest release of $Repo. Assets present: $($release.assets.name -join ', ')"
}
$latest = [PSCustomObject]@{ tag = $release.tagName; asset = $assetMatch.name }
Log "Latest remote tag : $($latest.tag)"
Log "Latest asset      : $($latest.asset)"

if (-not $Force -and $currentTag -eq $latest.tag) {
    Log "Already on the latest tag. Pass -Force to reinstall."
    return
}

# Close any running Codex from the install dir
$running = Get-Process Codex,codex -EA SilentlyContinue | Where-Object { $_.Path -like "$InstallDir*" }
if ($running) {
    Log "Closing $($running.Count) running Codex process(es)..."
    foreach ($p in $running) {
        try { Stop-Process -Id $p.Id -Force -EA Stop } catch { Log "  failed PID $($p.Id): $_" }
    }
    Start-Sleep -Seconds 2
}

# Workspace
$staging = Join-Path $env:TEMP "codex-update-$([guid]::NewGuid().Guid.Substring(0,8))"
New-Item -ItemType Directory -Force -Path $staging | Out-Null

try {
    Log "Downloading $($latest.asset)..."
    & gh release download $latest.tag --repo $Repo --pattern $latest.asset --dir $staging --clobber
    if ($LASTEXITCODE -ne 0) { throw "gh release download failed with exit $LASTEXITCODE" }
    $zip = Get-Item (Join-Path $staging $latest.asset)
    Log ("Downloaded {0:N0} MB" -f ($zip.Length / 1MB))

    Log "Extracting to staging..."
    $extractDir = Join-Path $staging 'extract'
    Expand-Archive -LiteralPath $zip.FullName -DestinationPath $extractDir -Force

    # Preserve tools/ from current install — they are co-located helper scripts
    # we don't want to lose during the swap. Future releases may bundle their
    # own tools/, in which case the new ones win.
    $oldTools = Join-Path $InstallDir 'tools'
    $newTools = Join-Path $extractDir 'tools'
    if (-not (Test-Path -LiteralPath $newTools) -and (Test-Path -LiteralPath $oldTools)) {
        Log "Preserving tools/ from previous install"
        Copy-Item -Recurse -Force -LiteralPath $oldTools -Destination $extractDir
    }

    # Atomic-ish swap: rename old, move new, delete old
    $backup = "$InstallDir.old-$(Get-Date -Format yyyyMMdd-HHmmss)"
    if (Test-Path -LiteralPath $InstallDir) {
        Log "Renaming current install to backup: $backup"
        Rename-Item -LiteralPath $InstallDir -NewName (Split-Path -Leaf $backup) -ErrorAction Stop
    }
    Log "Moving extracted build into place"
    Move-Item -LiteralPath $extractDir -Destination $InstallDir

    # Record version tag for next update check
    $newVersionFile = Join-Path $InstallDir 'tools\.version-tag'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $newVersionFile) | Out-Null
    $latest.tag | Set-Content -LiteralPath $newVersionFile -NoNewline

    # Cleanup backup
    if (Test-Path -LiteralPath $backup) {
        Log "Removing backup: $backup"
        Remove-Item -Recurse -Force -LiteralPath $backup
    }

    Log "Update complete: $currentTag -> $($latest.tag)"

    Update-CodexShortcut -InstallDir $InstallDir

    if (-not $NoLaunch) {
        $launcher = Join-Path $InstallDir 'tools\Launch-Codex.vbs'
        $exe = Join-Path $InstallDir 'Codex.exe'
        if (Test-Path -LiteralPath $launcher) {
            Log "Launching Codex via shared-sidecar launcher..."
            Start-Process -FilePath $launcher
        } elseif (Test-Path -LiteralPath $exe) {
            Log "WARN: shared-sidecar launcher missing; launching Codex.exe directly"
            Start-Process -FilePath $exe
        } else {
            Log "WARN: Codex.exe missing at $exe after update"
        }
    }
} finally {
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -Recurse -Force -LiteralPath $staging -EA SilentlyContinue
    }
}

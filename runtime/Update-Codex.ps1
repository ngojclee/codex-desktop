# One-click updater for Codex (GitHub Patched).
#
# Pulls the latest release from ngojclee/codex-desktop, replaces the local
# install with the new build, preserves the tools/ folder and any user data
# that lives elsewhere (~/.codex/ is untouched — Codex stores sessions there).
#
# Uses the GitHub Releases API directly, so GitHub CLI is not required.
# Requires Windows tar.exe for extraction.

[CmdletBinding()]
param(
    [string]$Repo = 'ngojclee/codex-desktop',
    [string]$InstallDir = "$env:LOCALAPPDATA\CodexFromGithub",
    [string]$Tag,         # install a specific release tag instead of latest
    [switch]$Force,        # update even if current version matches latest tag
    [switch]$NoLaunch      # don't auto-launch after update
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# IMPORTANT: this script will rename the install dir. PowerShell holds a
# handle on its current working directory; if CWD is anywhere under the
# install dir (which it will be when launched via the shortcut), the rename
# fails. Move CWD out to %TEMP% before touching anything else.
Set-Location -LiteralPath $env:TEMP

function Log($m) { Write-Host "[$(Get-Date -Format HH:mm:ss)] $m" }

$GitHubHeaders = @{
    'Accept' = 'application/vnd.github+json'
    'User-Agent' = 'codex-desktop-patched-updater'
}

function Get-StatusCode {
    param($ErrorRecord)

    try {
        if ($ErrorRecord.Exception.Response) {
            return [int]$ErrorRecord.Exception.Response.StatusCode
        }
    } catch {}
    return $null
}

function Invoke-GitHubApiWithFallback {
    param(
        [Parameter(Mandatory=$true)][string]$Repo,
        [string]$Tag
    )

    if ($Tag) {
        $apiPath = "repos/$Repo/releases/tags/$Tag"
    } else {
        $apiPath = "repos/$Repo/releases/latest"
    }
    $releaseUrl = "https://api.github.com/$apiPath"

    try {
        Log "Checking release via public GitHub API..."
        return Invoke-RestMethod -Uri $releaseUrl -Headers $GitHubHeaders -ErrorAction Stop
    } catch {
        $status = Get-StatusCode $_
        $gh = Get-Command gh -ErrorAction SilentlyContinue
        if (-not $gh) {
            throw "Could not read release from public GitHub API ($status). If $Repo is private, install GitHub CLI and run 'gh auth login'. Original error: $($_.Exception.Message)"
        }

        Log "Public GitHub API failed ($status); trying GitHub CLI auth fallback..."
        $json = & gh api $apiPath 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "GitHub CLI fallback failed. Run 'gh auth login' if $Repo is private. gh output: $json"
        }
        return ($json | ConvertFrom-Json)
    }
}

function Save-GitHubReleaseAsset {
    param(
        [Parameter(Mandatory=$true)]$Release,
        [Parameter(Mandatory=$true)]$Asset,
        [Parameter(Mandatory=$true)][string]$Repo,
        [Parameter(Mandatory=$true)][string]$DestinationDir
    )

    $zipPath = Join-Path $DestinationDir $Asset.name
    try {
        Invoke-WebRequest -Uri $Asset.browser_download_url -Headers $GitHubHeaders -OutFile $zipPath -ErrorAction Stop
        return $zipPath
    } catch {
        $status = Get-StatusCode $_
        $gh = Get-Command gh -ErrorAction SilentlyContinue
        if (-not $gh) {
            throw "Could not download release asset via public URL ($status). If $Repo is private, install GitHub CLI and run 'gh auth login'. Original error: $($_.Exception.Message)"
        }

        Log "Public asset download failed ($status); trying GitHub CLI auth fallback..."
        & gh release download $Release.tag_name --repo $Repo --pattern $Asset.name --dir $DestinationDir --clobber
        if ($LASTEXITCODE -ne 0) {
            throw "GitHub CLI asset download failed. Run 'gh auth login' if $Repo is private."
        }

        $downloaded = Get-ChildItem -LiteralPath $DestinationDir -Filter $Asset.name | Select-Object -First 1
        if (-not $downloaded) {
            throw "GitHub CLI reported success, but asset was not found in $DestinationDir"
        }
        return $downloaded.FullName
    }
}

function Update-CodexShortcut {
    param([string]$InstallDir)

    $launcher = Join-Path $InstallDir 'tools\Launch-Codex.vbs'
    $logLauncher = Join-Path $InstallDir 'tools\Launch-Codex-Logs.vbs'
    $devLauncher = Join-Path $InstallDir 'tools\Launch-Codex-Dev.vbs'
    $updateLauncher = Join-Path $InstallDir 'tools\Update-Codex.cmd'
    $icon = Join-Path $InstallDir 'Codex.exe'
    if (-not (Test-Path -LiteralPath $launcher)) { return }

    $shell = New-Object -ComObject WScript.Shell

    function Get-DesktopPath {
        $candidates = @([Environment]::GetFolderPath('Desktop'))
        $shellDesktop = (Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' -ErrorAction SilentlyContinue).Desktop
        if ($shellDesktop) { $candidates += $shellDesktop }
        if ($env:OneDrive) { $candidates += (Join-Path $env:OneDrive 'Desktop') }
        $candidates += (Join-Path $env:USERPROFILE 'OneDrive\Desktop')
        $candidates += (Join-Path $env:USERPROFILE 'Desktop')

        foreach ($candidate in ($candidates | Where-Object { $_ })) {
            $expanded = [Environment]::ExpandEnvironmentVariables($candidate)
            if (Test-Path -LiteralPath $expanded) { return $expanded }
        }

        $fallback = Join-Path $env:USERPROFILE 'Desktop'
        New-Item -ItemType Directory -Force -Path $fallback | Out-Null
        return $fallback
    }

    function Set-Shortcut {
        param(
            [Parameter(Mandatory=$true)][string]$Path,
            [Parameter(Mandatory=$true)][string]$TargetPath,
            [string]$Description = 'Codex Desktop (GitHub Patched)',
            [switch]$ForceUpdate
        )

        if (-not (Test-Path -LiteralPath $TargetPath)) { return }
        if ((Test-Path -LiteralPath $Path) -and -not $ForceUpdate) { return }

        $shortcutDir = Split-Path -Parent $Path
        New-Item -ItemType Directory -Force -Path $shortcutDir | Out-Null

        $shortcut = $shell.CreateShortcut($Path)
        $shortcut.TargetPath = $TargetPath
        $shortcut.Arguments = ''
        $shortcut.WorkingDirectory = Split-Path -Parent $TargetPath
        if (Test-Path -LiteralPath $icon) { $shortcut.IconLocation = "$icon,0" }
        $shortcut.Description = $Description
        $shortcut.Save()
    }

    $startShortcut = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\Codex.lnk'
    Set-Shortcut -Path $startShortcut -TargetPath $launcher
    if (Test-Path -LiteralPath $devLauncher) {
        $startDevShortcut = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\Codex Dev.lnk'
        Set-Shortcut `
            -Path $startDevShortcut `
            -TargetPath $devLauncher `
            -Description 'Codex Desktop (GitHub Patched) Dev build-flavor lane'
    }
    if (Test-Path -LiteralPath $updateLauncher) {
        $startUpdateShortcut = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\Update-Codex.lnk'
        Set-Shortcut `
            -Path $startUpdateShortcut `
            -TargetPath $updateLauncher `
            -Description 'Update Codex Desktop (GitHub Patched)'
    }

    $desktopDir = Get-DesktopPath
    if ($desktopDir) {
        Set-Shortcut `
            -Path (Join-Path $desktopDir 'Codex (GitHub Patched).lnk') `
            -TargetPath $launcher

        if (Test-Path -LiteralPath $devLauncher) {
            Set-Shortcut `
                -Path (Join-Path $desktopDir 'Codex Dev (GitHub Patched).lnk') `
                -TargetPath $devLauncher `
                -Description 'Codex Desktop (GitHub Patched) Dev build-flavor lane'
        }

        if (Test-Path -LiteralPath $updateLauncher) {
            Set-Shortcut `
                -Path (Join-Path $desktopDir 'Update-Codex.lnk') `
                -TargetPath $updateLauncher `
                -Description 'Update Codex Desktop (GitHub Patched)'
        }
    }

    # Repair a pinned taskbar shortcut only when it already points at this
    # install. That keeps unrelated Codex pins untouched while preventing future
    # launches from bypassing the shared-sidecar/dev-mode launcher.
    $taskbarShortcut = Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\Codex.lnk'
    if (Test-Path -LiteralPath $taskbarShortcut) {
        try {
            $existing = $shell.CreateShortcut($taskbarShortcut)
            $target = [string]$existing.TargetPath
            $installExe = Join-Path $InstallDir 'Codex.exe'
            $isThisInstall =
                $target.Equals($installExe, [System.StringComparison]::OrdinalIgnoreCase) -or
                $target.Equals($launcher, [System.StringComparison]::OrdinalIgnoreCase) -or
                ($logLauncher -and $target.Equals($logLauncher, [System.StringComparison]::OrdinalIgnoreCase)) -or
                ($devLauncher -and $target.Equals($devLauncher, [System.StringComparison]::OrdinalIgnoreCase))

            if ($isThisInstall) {
                Set-Shortcut -Path $taskbarShortcut -TargetPath $launcher -ForceUpdate
            }
        } catch {
            Log "WARN: could not update taskbar shortcut: $_"
        }
    }
}

# Resolve current installed version from tools/.version-tag if present
$versionFile = Join-Path $InstallDir 'tools\.version-tag'
$currentTag = if (Test-Path -LiteralPath $versionFile) { (Get-Content $versionFile -Raw).Trim() } else { '(unknown)' }
Log "Current installed tag: $currentTag"

# Check release on the remote. Default = latest; -Tag installs an explicit lane.
# Public repos use unauthenticated GitHub HTTP. Private repos fall back to
# GitHub CLI auth when public access is denied.
$release = Invoke-GitHubApiWithFallback -Repo $Repo -Tag $Tag
$assetMatch = $release.assets | Where-Object { $_.name -like 'CodexDesktop-Patched-win-x64-*.zip' } | Select-Object -First 1
if (-not $release.tag_name -or -not $assetMatch) {
    throw "Could not find tag or matching asset on release of $Repo. Assets present: $($release.assets.name -join ', ')"
}
$latest = [PSCustomObject]@{ tag = $release.tag_name; asset = $assetMatch }
Log "Remote tag       : $($latest.tag)"
Log "Latest asset      : $($latest.asset.name)"

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
    Log "Downloading $($latest.asset.name)..."
    $zipPath = Save-GitHubReleaseAsset -Release $release -Asset $latest.asset -Repo $Repo -DestinationDir $staging
    $zip = Get-Item -LiteralPath $zipPath
    if ($zip.Length -ne [int64]$latest.asset.size) {
        throw "Download incomplete: got $($zip.Length) bytes, expected $($latest.asset.size)"
    }
    Log ("Downloaded {0:N0} MB" -f ($zip.Length / 1MB))

    Log "Extracting to staging..."
    $extractDir = Join-Path $staging 'extract'
    New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
    tar -xf $zip.FullName -C $extractDir
    if ($LASTEXITCODE -ne 0) { throw "tar extraction failed with exit $LASTEXITCODE" }

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

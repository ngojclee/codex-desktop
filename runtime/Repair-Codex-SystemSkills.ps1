# Repair-Codex-SystemSkills.ps1
#
# One-time repair for Codex installs where %USERPROFILE%\.codex\skills is a
# symlink/junction to a network share. Codex CLI 0.136+ installs generated
# system skills under .codex\skills\.system on startup. Keeping that generated
# .system directory on SMB/NFS can fail with Access denied / network error and
# can block thread loading.
#
# This script keeps shared user skills on the network, but makes:
#   %USERPROFILE%\.codex\skills\.system
# a real local directory on each machine.
#
# Default mode is dry-run. Pass -Apply to change files.

[CmdletBinding()]
param(
    [string]$LocalSkillsDir = "$env:USERPROFILE\.codex\skills",
    [string]$SharedSkillsDir,
    [switch]$Apply,
    [switch]$CopySharedSkills,
    [switch]$ForceWhileRunning
)

$ErrorActionPreference = 'Stop'

function Log([string]$Message) {
    Write-Host $Message
}

function Is-ReparsePoint([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $item = Get-Item -LiteralPath $Path -Force
    return [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Resolve-LinkTarget([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $item = Get-Item -LiteralPath $Path -Force
    if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $null }
    $target = if ($item.Target -is [array]) { [string]$item.Target[0] } else { [string]$item.Target }
    if ($target.StartsWith('UNC\', [StringComparison]::OrdinalIgnoreCase)) {
        return '\\' + $target.Substring(4)
    }
    if ($target.StartsWith('\??\UNC\', [StringComparison]::OrdinalIgnoreCase)) {
        return '\\' + $target.Substring(8)
    }
    if ($target.StartsWith('\\?\UNC\', [StringComparison]::OrdinalIgnoreCase)) {
        return '\\' + $target.Substring(8)
    }
    return $target
}

function Assert-NoCodexProcesses {
    if ($ForceWhileRunning) { return }
    $running = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -ieq 'Codex.exe' -or $_.Name -ieq 'codex.exe'
    })
    if ($running.Count -gt 0) {
        throw "Codex/Codex sidecar is running. Close Codex first, or pass -ForceWhileRunning if you know what you are doing."
    }
}

function Ensure-Directory([string]$Path) {
    if ($Apply) {
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
    }
    Log "ensure dir: $Path"
}

function Remove-DirectoryLink([string]$Path) {
    if (-not (Is-ReparsePoint $Path)) {
        throw "Refusing to remove non-reparse directory: $Path"
    }
    Log "remove directory link only: $Path"
    if ($Apply) {
        [IO.Directory]::Delete($Path, $false)
    }
}

function Assert-CanCreateDirectorySymlink([string]$ParentDir, [string]$TargetDir) {
    if ((-not $Apply) -or $CopySharedSkills -or (-not $TargetDir)) { return }

    $probe = Join-Path $ParentDir ('.codex-repair-symlink-probe-' + [guid]::NewGuid().Guid.Substring(0,8))
    try {
        New-Item -ItemType SymbolicLink -Path $probe -Target $TargetDir -ErrorAction Stop | Out-Null
    } catch {
        throw "Cannot create directory symlinks in $ParentDir. Run PowerShell as Administrator, enable Windows Developer Mode, or re-run with -CopySharedSkills. Original error: $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $probe) {
            Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
        }
    }
}

function New-SkillEntry([string]$LinkPath, [string]$TargetPath, [bool]$IsDirectory, [switch]$AssumeMissing) {
    if ((-not $AssumeMissing) -and (Test-Path -LiteralPath $LinkPath)) {
        return
    }

    $kind = if ($IsDirectory) { 'Directory' } else { 'File' }
    $verb = if ($CopySharedSkills) { 'copy' } else { 'link' }
    Log "$verb $kind`: $LinkPath -> $TargetPath"
    if ($Apply) {
        if ($CopySharedSkills) {
            Copy-Item -LiteralPath $TargetPath -Destination $LinkPath -Recurse:$IsDirectory -Force
        } else {
            New-Item -ItemType SymbolicLink -Path $LinkPath -Target $TargetPath | Out-Null
        }
    }
}

$currentTarget = Resolve-LinkTarget $LocalSkillsDir
if (-not $SharedSkillsDir) {
    $SharedSkillsDir = $currentTarget
}

Log "Local skills : $LocalSkillsDir"
Log "Shared skills: $(if ($SharedSkillsDir) { $SharedSkillsDir } else { '(none detected; no shared links will be created)' })"
Log "Mode         : $(if ($Apply) { 'APPLY' } else { 'DRY-RUN' })"
Log "Shared mode  : $(if ($CopySharedSkills) { 'copy' } else { 'symlink' })"

if ((Is-ReparsePoint $LocalSkillsDir) -and (-not $SharedSkillsDir)) {
    throw "Local skills is a reparse point, but the target could not be detected. Re-run with -SharedSkillsDir <UNC-or-path>."
}

if ($SharedSkillsDir -and (-not (Test-Path -LiteralPath $SharedSkillsDir))) {
    throw "Shared skills directory is not reachable: $SharedSkillsDir"
}

Assert-NoCodexProcesses

$localParent = Split-Path -Parent $LocalSkillsDir
Ensure-Directory $localParent
Assert-CanCreateDirectorySymlink -ParentDir $localParent -TargetDir $SharedSkillsDir

$localSkillsWillBeFresh = $false
if (Test-Path -LiteralPath $LocalSkillsDir) {
    if (Is-ReparsePoint $LocalSkillsDir) {
        $target = Resolve-LinkTarget $LocalSkillsDir
        Log "current local skills is a reparse point -> $target"
        Remove-DirectoryLink $LocalSkillsDir
        Ensure-Directory $LocalSkillsDir
        $localSkillsWillBeFresh = $true
    } else {
        Log "current local skills is already a real directory"
    }
} else {
    Ensure-Directory $LocalSkillsDir
    $localSkillsWillBeFresh = $true
}

$systemDir = Join-Path $LocalSkillsDir '.system'
if ($localSkillsWillBeFresh -and -not $Apply) {
    Ensure-Directory $systemDir
} elseif (Test-Path -LiteralPath $systemDir) {
    if (Is-ReparsePoint $systemDir) {
        Log "remove .system reparse point: $systemDir"
        if ($Apply) {
            [IO.Directory]::Delete($systemDir, $false)
        }
        Ensure-Directory $systemDir
    } else {
        Log ".system is already local: $systemDir"
    }
} else {
    Ensure-Directory $systemDir
}

$metadata = Join-Path $LocalSkillsDir '.shared-skills-target.txt'
if ($SharedSkillsDir) {
    Log "write metadata: $metadata"
} else {
    Log "skip metadata: no shared skills target"
}
if ($Apply -and $SharedSkillsDir) {
    $SharedSkillsDir | Set-Content -LiteralPath $metadata -Encoding UTF8 -NoNewline
}

$sharedItems = @()
if ($SharedSkillsDir) {
    $sharedItems = Get-ChildItem -LiteralPath $SharedSkillsDir -Force | Where-Object {
        $_.Name -ne '.system' -and $_.Name -ne '.shared-skills-target.txt'
    }
}

foreach ($item in $sharedItems) {
    $linkPath = Join-Path $LocalSkillsDir $item.Name
    $assumeMissing = $localSkillsWillBeFresh -and (-not $Apply)
    if ((-not $assumeMissing) -and (Test-Path -LiteralPath $linkPath)) {
        continue
    }
    New-SkillEntry -LinkPath $linkPath -TargetPath $item.FullName -IsDirectory ([bool]$item.PSIsContainer) -AssumeMissing:$assumeMissing
}

Log ""
Log "Done. Start Codex again and verify:"
Log "  Test-Path `"$systemDir`""
Log "  Get-Item `"$LocalSkillsDir`" | Select FullName,Attributes,LinkType,Target"
Log "  Get-ChildItem -Force `"$systemDir`""

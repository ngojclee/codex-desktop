# Refresh-Codex-SharedSkills.ps1
#
# Safe shared-skill refresh for Codex Desktop installs.
#
# Codex CLI 0.136+ owns %USERPROFILE%\.codex\skills\.system and rewrites it on
# startup. That generated directory must stay local per machine. User-created
# skills can still live on a shared UNC/NAS directory as individual symlinks.
#
# This script links or copies any missing shared skills into the local skills
# directory, always skipping .system. By default it never overwrites existing
# local skills and never auto-publishes local-only skills back to the shared
# directory. Pass -RelinkExistingSharedSkills for a deliberate one-time
# conversion of copied shared skills back into individual symlinks.

[CmdletBinding()]
param(
    [string]$LocalSkillsDir = "$env:USERPROFILE\.codex\skills",
    [string]$SharedSkillsDir,
    [switch]$CopySharedSkills,
    [switch]$RepairRootLink,
    [switch]$RelinkExistingSharedSkills,
    [string]$RelinkBackupRoot,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

function Log([string]$Message) {
    if (-not $Quiet) { Write-Host $Message }
}

function Warn([string]$Message) {
    Write-Warning $Message
}

function Get-ItemOrNull([string]$Path) {
    try {
        return Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    } catch {
        return $null
    }
}

function Test-PathOrLink([string]$Path) {
    return $null -ne (Get-ItemOrNull $Path)
}

function Is-ReparsePoint([string]$Path) {
    $item = Get-ItemOrNull $Path
    if (-not $item) { return $false }
    return [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Resolve-LinkTarget([string]$Path) {
    $item = Get-ItemOrNull $Path
    if (-not $item) { return $null }
    if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $null }

    $target = if ($item.Target -is [array]) { [string]$item.Target[0] } else { [string]$item.Target }
    if (-not $target) { return $null }

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

function Get-NormalizedPath([string]$Path) {
    try {
        return [IO.Path]::GetFullPath($Path).TrimEnd('\')
    } catch {
        return $Path.TrimEnd('\')
    }
}

function Test-SamePath([string]$A, [string]$B) {
    if (-not $A -or -not $B) { return $false }
    $left = Get-NormalizedPath $A
    $right = Get-NormalizedPath $B
    return $left.Equals($right, [StringComparison]::OrdinalIgnoreCase)
}

function Read-SharedTargetMetadata([string]$SkillsDir) {
    $metadata = Join-Path $SkillsDir '.shared-skills-target.txt'
    if (-not (Test-Path -LiteralPath $metadata)) { return $null }
    try {
        $value = (Get-Content -Raw -LiteralPath $metadata).Trim()
        if ($value) { return $value }
    } catch {}
    return $null
}

function Resolve-SharedSkillsDir {
    if ($SharedSkillsDir) { return $SharedSkillsDir }
    if ($env:CODEX_SHARED_SKILLS_DIR) { return $env:CODEX_SHARED_SKILLS_DIR }

    $metadataTarget = Read-SharedTargetMetadata $LocalSkillsDir
    if ($metadataTarget) { return $metadataTarget }

    $rootTarget = Resolve-LinkTarget $LocalSkillsDir
    if ($rootTarget) { return $rootTarget }

    return $null
}

function Test-CodexProcessesRunning {
    $running = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -ieq 'Codex.exe' -or $_.Name -ieq 'codex.exe'
    })
    return $running.Count -gt 0
}

function Assert-CanCreateDirectorySymlink([string]$ParentDir, [string]$TargetDir) {
    if ($CopySharedSkills -or (-not $TargetDir)) { return }

    $probe = Join-Path $ParentDir ('.codex-refresh-symlink-probe-' + [guid]::NewGuid().Guid.Substring(0,8))
    try {
        New-Item -ItemType SymbolicLink -Path $probe -Target $TargetDir -ErrorAction Stop | Out-Null
    } catch {
        throw "Cannot create directory symlinks in $ParentDir. Run PowerShell as Administrator, enable Windows Developer Mode, set CODEX_SHARED_SKILLS_COPY=1 for launcher copy mode, or run this script with -CopySharedSkills. Original error: $($_.Exception.Message)"
    } finally {
        if (Test-PathOrLink $probe) {
            Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
        }
    }
}

function Ensure-LocalRoot {
    $parent = Split-Path -Parent $LocalSkillsDir
    New-Item -ItemType Directory -Force -Path $parent | Out-Null

    if (Is-ReparsePoint $LocalSkillsDir) {
        $target = Resolve-LinkTarget $LocalSkillsDir
        if (-not $script:ResolvedSharedSkillsDir) {
            $script:ResolvedSharedSkillsDir = $target
        }

        if (-not $RepairRootLink) {
            Warn "Local skills root is still a reparse point: $LocalSkillsDir -> $target. Run Repair-Codex-SystemSkills.ps1 -Apply, or rerun refresh with -RepairRootLink."
            return
        }
        if (Test-CodexProcessesRunning) {
            Warn "Codex is running; leaving the root skills link untouched for this launch."
            return
        }
        if (-not $script:ResolvedSharedSkillsDir) {
            throw "Local skills is a reparse point, but the target could not be detected. Re-run with -SharedSkillsDir <UNC-or-path>."
        }
        if (-not (Test-Path -LiteralPath $script:ResolvedSharedSkillsDir)) {
            throw "Shared skills directory is not reachable: $($script:ResolvedSharedSkillsDir)"
        }

        Assert-CanCreateDirectorySymlink -ParentDir $parent -TargetDir $script:ResolvedSharedSkillsDir
        Log "convert root skills link to local dir: $LocalSkillsDir -> $($script:ResolvedSharedSkillsDir)"
        [IO.Directory]::Delete($LocalSkillsDir, $false)
        New-Item -ItemType Directory -Force -Path $LocalSkillsDir | Out-Null
        return
    }

    if (-not (Test-PathOrLink $LocalSkillsDir)) {
        New-Item -ItemType Directory -Force -Path $LocalSkillsDir | Out-Null
        Log "created local skills dir: $LocalSkillsDir"
    }
}

function Ensure-LocalSystemSkills {
    if (Is-ReparsePoint $LocalSkillsDir) { return }

    $systemDir = Join-Path $LocalSkillsDir '.system'
    $systemItem = Get-ItemOrNull $systemDir
    if ($systemItem -and -not $systemItem.PSIsContainer) {
        throw "Refusing to replace non-directory .system entry: $systemDir"
    }

    if ($systemItem -and ($systemItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        if (-not $RepairRootLink) {
            Warn ".system is a reparse point; leaving it untouched. Run Repair-Codex-SystemSkills.ps1 -Apply or refresh with -RepairRootLink."
            return
        }
        if (Test-CodexProcessesRunning) {
            Warn "Codex is running; leaving .system link untouched for this launch."
            return
        }
        Log "replace .system link with local dir: $systemDir"
        [IO.Directory]::Delete($systemDir, $false)
    }

    if (-not (Test-PathOrLink $systemDir)) {
        New-Item -ItemType Directory -Force -Path $systemDir | Out-Null
        Log "created local system skills dir: $systemDir"
    }
}

function Write-SharedTargetMetadata {
    if (-not $script:ResolvedSharedSkillsDir) { return }
    if (Is-ReparsePoint $LocalSkillsDir) { return }

    $metadata = Join-Path $LocalSkillsDir '.shared-skills-target.txt'
    $script:ResolvedSharedSkillsDir | Set-Content -LiteralPath $metadata -Encoding UTF8 -NoNewline
}

function New-SharedSkillEntry([string]$LocalPath, [string]$TargetPath, [bool]$IsDirectory) {
    if (Test-PathOrLink $LocalPath) { return }

    $kind = if ($IsDirectory) { 'Directory' } else { 'File' }
    $verb = if ($CopySharedSkills) { 'copy' } else { 'link' }
    Log "$verb $kind`: $LocalPath -> $TargetPath"

    try {
        if ($CopySharedSkills) {
            if ($IsDirectory) {
                Copy-Item -LiteralPath $TargetPath -Destination $LocalPath -Recurse -Force
            } else {
                Copy-Item -LiteralPath $TargetPath -Destination $LocalPath -Force
            }
        } else {
            New-Item -ItemType SymbolicLink -Path $LocalPath -Target $TargetPath -ErrorAction Stop | Out-Null
        }
    } catch {
        Warn "Could not $verb shared skill '$([IO.Path]::GetFileName($LocalPath))': $($_.Exception.Message)"
    }
}

function Get-RelinkBackupDir {
    if ($script:RelinkBackupDir) { return $script:RelinkBackupDir }

    $backupRoot = $RelinkBackupRoot
    if (-not $backupRoot) {
        $backupRoot = Join-Path (Split-Path -Parent $LocalSkillsDir) 'skills-copy-backups'
    }

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $script:RelinkBackupDir = Join-Path $backupRoot $stamp
    New-Item -ItemType Directory -Force -Path $script:RelinkBackupDir | Out-Null
    return $script:RelinkBackupDir
}

function Move-LocalSkillToRelinkBackup([string]$LocalPath) {
    $backupDir = Get-RelinkBackupDir
    $name = Split-Path -Leaf $LocalPath
    $backupPath = Join-Path $backupDir $name
    if (Test-PathOrLink $backupPath) {
        $backupPath = Join-Path $backupDir ($name + '-' + [guid]::NewGuid().Guid.Substring(0,8))
    }

    Log "backup local copy: $LocalPath -> $backupPath"
    Move-Item -LiteralPath $LocalPath -Destination $backupPath -ErrorAction Stop
    return $backupPath
}

function Relink-ExistingSharedSkill([string]$LocalPath, [string]$TargetPath, [bool]$IsDirectory) {
    if (-not $RelinkExistingSharedSkills) { return }
    if (-not (Test-PathOrLink $LocalPath)) { return }

    $localItem = Get-ItemOrNull $LocalPath
    if (-not $localItem) { return }
    if ($localItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { return }
    if ([bool]$localItem.PSIsContainer -ne $IsDirectory) {
        Warn "Skipping relink for '$($localItem.Name)': local/shared item types differ."
        return
    }

    $kind = if ($IsDirectory) { 'Directory' } else { 'File' }
    $backupPath = Move-LocalSkillToRelinkBackup -LocalPath $LocalPath
    try {
        Log "relink $kind`: $LocalPath -> $TargetPath"
        New-Item -ItemType SymbolicLink -Path $LocalPath -Target $TargetPath -ErrorAction Stop | Out-Null
    } catch {
        Warn "Could not relink '$([IO.Path]::GetFileName($LocalPath))': $($_.Exception.Message)"
        if ((-not (Test-PathOrLink $LocalPath)) -and (Test-PathOrLink $backupPath)) {
            Move-Item -LiteralPath $backupPath -Destination $LocalPath -ErrorAction SilentlyContinue
        }
    }
}

$script:ResolvedSharedSkillsDir = Resolve-SharedSkillsDir
$script:RelinkBackupDir = $null

Log "Local skills : $LocalSkillsDir"
Log "Shared skills: $(if ($script:ResolvedSharedSkillsDir) { $script:ResolvedSharedSkillsDir } else { '(none configured)' })"
Log "Shared mode  : $(if ($CopySharedSkills) { 'copy' } else { 'symlink' })"
if ($RelinkExistingSharedSkills) {
    Log "Relink mode  : existing local shared-skill copies will be backed up and replaced with symlinks"
}

if ($CopySharedSkills -and $RelinkExistingSharedSkills) {
    throw "-RelinkExistingSharedSkills requires symlink mode. Remove -CopySharedSkills and rerun from an elevated PowerShell or with Windows Developer Mode enabled."
}

if ($script:ResolvedSharedSkillsDir -and -not (Test-Path -LiteralPath $script:ResolvedSharedSkillsDir)) {
    Warn "Shared skills directory is not reachable: $($script:ResolvedSharedSkillsDir)"
}

Ensure-LocalRoot
Ensure-LocalSystemSkills
Write-SharedTargetMetadata

if (-not $script:ResolvedSharedSkillsDir) {
    Log "No shared skills target configured; refresh complete."
    return
}
if (-not (Test-Path -LiteralPath $script:ResolvedSharedSkillsDir)) {
    Log "Shared skills target is not reachable; refresh complete."
    return
}
if (Is-ReparsePoint $LocalSkillsDir) {
    Log "Local skills root is still a link; individual refresh skipped."
    return
}
if (Test-SamePath $LocalSkillsDir $script:ResolvedSharedSkillsDir) {
    Warn "Local and shared skills directories resolve to the same path; individual refresh skipped."
    return
}

if (-not $CopySharedSkills) {
    Assert-CanCreateDirectorySymlink -ParentDir $LocalSkillsDir -TargetDir $script:ResolvedSharedSkillsDir
}

$sharedItems = Get-ChildItem -LiteralPath $script:ResolvedSharedSkillsDir -Force | Where-Object {
    $_.Name -ne '.system' -and
    $_.Name -ne '.shared-skills-target.txt'
}

foreach ($item in $sharedItems) {
    $localPath = Join-Path $LocalSkillsDir $item.Name
    if (Test-PathOrLink $localPath) {
        Relink-ExistingSharedSkill -LocalPath $localPath -TargetPath $item.FullName -IsDirectory ([bool]$item.PSIsContainer)
        continue
    }
    New-SharedSkillEntry -LocalPath $localPath -TargetPath $item.FullName -IsDirectory ([bool]$item.PSIsContainer)
}

Log "Shared skills refresh complete."

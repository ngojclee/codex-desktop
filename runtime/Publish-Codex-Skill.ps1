# Publish-Codex-Skill.ps1
#
# Explicitly publish one local Codex skill to the shared skills directory.
#
# Local skills are intentionally not auto-published. This command copies a
# chosen local skill to the shared UNC/NAS location, then replaces the local
# copy with a symlink back to the shared copy. .system is refused because Codex
# owns it and it must stay local per machine.

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$SkillName,
    [string]$LocalSkillsDir = "$env:USERPROFILE\.codex\skills",
    [string]$SharedSkillsDir,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

function Log([string]$Message) {
    Write-Host $Message
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

function Assert-CanCreateDirectorySymlink([string]$ParentDir, [string]$TargetDir) {
    $probe = Join-Path $ParentDir ('.codex-publish-symlink-probe-' + [guid]::NewGuid().Guid.Substring(0,8))
    try {
        New-Item -ItemType SymbolicLink -Path $probe -Target $TargetDir -ErrorAction Stop | Out-Null
    } catch {
        throw "Cannot create directory symlinks in $ParentDir. Run PowerShell as Administrator or enable Windows Developer Mode before publishing. Original error: $($_.Exception.Message)"
    } finally {
        if (Test-PathOrLink $probe) {
            Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
        }
    }
}

if ([IO.Path]::GetFileName($SkillName) -ne $SkillName) {
    throw "Pass only a skill directory name, not a path: $SkillName"
}
if ($SkillName -eq '.system' -or $SkillName -eq '.shared-skills-target.txt') {
    throw "Refusing to publish reserved Codex skills entry: $SkillName"
}

$resolvedSharedSkillsDir = Resolve-SharedSkillsDir
if (-not $resolvedSharedSkillsDir) {
    throw "Shared skills directory is not configured. Pass -SharedSkillsDir <UNC-or-path> or set CODEX_SHARED_SKILLS_DIR."
}

if (Is-ReparsePoint $LocalSkillsDir) {
    throw "Local skills root is still a reparse point. Run Repair-Codex-SystemSkills.ps1 -Apply first so .system stays local and shared skills become individual links."
}

if (-not (Test-Path -LiteralPath $LocalSkillsDir)) {
    throw "Local skills directory does not exist: $LocalSkillsDir"
}

New-Item -ItemType Directory -Force -Path $resolvedSharedSkillsDir | Out-Null

$localSkillPath = Join-Path $LocalSkillsDir $SkillName
$sharedSkillPath = Join-Path $resolvedSharedSkillsDir $SkillName

if (-not (Test-PathOrLink $localSkillPath)) {
    throw "Local skill does not exist: $localSkillPath"
}

$localItem = Get-ItemOrNull $localSkillPath
if ($localItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    $target = Resolve-LinkTarget $localSkillPath
    if (Test-SamePath $target $sharedSkillPath) {
        Log "Already published: $SkillName -> $target"
        return
    }
    throw "Local skill is already a reparse point to a different target: $localSkillPath -> $target"
}

if (Test-SamePath $localSkillPath $sharedSkillPath) {
    throw "Local and shared skill paths are the same. Refusing to publish: $localSkillPath"
}

if ((Test-PathOrLink $sharedSkillPath) -and -not $Force) {
    throw "Shared skill already exists: $sharedSkillPath. Pass -Force only if you want to replace it."
}

Assert-CanCreateDirectorySymlink -ParentDir $LocalSkillsDir -TargetDir $resolvedSharedSkillsDir

if (Test-PathOrLink $sharedSkillPath) {
    Log "replace existing shared skill: $sharedSkillPath"
    Remove-Item -LiteralPath $sharedSkillPath -Recurse -Force
}

$isDirectory = [bool]$localItem.PSIsContainer
Log "copy local skill to shared: $localSkillPath -> $sharedSkillPath"
if ($isDirectory) {
    Copy-Item -LiteralPath $localSkillPath -Destination $sharedSkillPath -Recurse -Force
} else {
    Copy-Item -LiteralPath $localSkillPath -Destination $sharedSkillPath -Force
}

$backupRoot = Join-Path $env:TEMP ('codex-skill-publish-' + [guid]::NewGuid().Guid.Substring(0,8))
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
$backupPath = Join-Path $backupRoot $SkillName

try {
    Log "replace local skill with shared symlink: $localSkillPath -> $sharedSkillPath"
    Move-Item -LiteralPath $localSkillPath -Destination $backupPath
    try {
        New-Item -ItemType SymbolicLink -Path $localSkillPath -Target $sharedSkillPath -ErrorAction Stop | Out-Null
    } catch {
        if (Test-PathOrLink $localSkillPath) {
            Remove-Item -LiteralPath $localSkillPath -Recurse -Force -ErrorAction SilentlyContinue
        }
        Move-Item -LiteralPath $backupPath -Destination $localSkillPath
        throw
    }
} finally {
    if (Test-Path -LiteralPath $backupRoot) {
        Remove-Item -LiteralPath $backupRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Log "Published skill: $SkillName"

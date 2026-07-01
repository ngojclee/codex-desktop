# Sync-Codex-ModelCatalog.ps1
#
# Keep Codex's local model catalog files parseable and in sync.
# Source of truth:
#   ~/.codex/model_catalog.json  - manually curated model catalog
#   ~/.codex/models_cache.json   - mirror used by some local flows
#
# This script intentionally does not read tray_config.json, add models, or add
# model_catalog_json to config.toml. Model catalog opt-in and model entries stay
# manual because some Desktop builds can fail during startup when forced to load
# an incompatible custom catalog.

[CmdletBinding()]
param(
    [string]$CodexHome = (Join-Path $env:USERPROFILE '.codex'),
    [int]$BackupKeep = 5,
    [switch]$Quiet,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'

function Write-Info([string]$Message) {
    if (-not $Quiet -and -not $Json) {
        Write-Host $Message
    }
}

function ConvertTo-PrettyJson($Value) {
    $Value | ConvertTo-Json -Depth 96
}

function Test-HasUtf8Bom([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $bytes = [IO.File]::ReadAllBytes($Path)
    return ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
}

function Write-Utf8NoBom([string]$Path, [string]$Text) {
    $utf8NoBom = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllText($Path, $Text, $utf8NoBom)
}

function Remove-OldBackups([string]$Path, [string]$Prefix, [int]$Keep) {
    if ($Keep -lt 0) { return }
    $parent = Split-Path -Parent $Path
    $leaf = Split-Path -Leaf $Path
    Get-ChildItem -LiteralPath $parent -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "$leaf.$Prefix-*" } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip $Keep |
        ForEach-Object {
            try { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction Stop } catch {}
        }
}

function Backup-File([string]$Path, [string]$Prefix, [int]$Keep) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backup = "$Path.$Prefix-$stamp"
    Copy-Item -LiteralPath $Path -Destination $backup -Force
    Remove-OldBackups -Path $Path -Prefix $Prefix -Keep $Keep
}

function Read-Catalog([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Model catalog not found: $Path"
    }

    try {
        $catalog = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    } catch {
        throw "Failed to parse model catalog $Path as JSON: $($_.Exception.Message)"
    }

    if (-not ($catalog.PSObject.Properties.Name -contains 'models')) {
        throw "Model catalog $Path is missing a top-level models array."
    }

    $models = @($catalog.models)
    if ($models.Count -eq 0) {
        throw "Model catalog $Path has an empty models array."
    }

    return $catalog
}

function Write-CatalogIfChanged([string]$Path, $Catalog, [string]$Prefix, [int]$Keep) {
    $before = if (Test-Path -LiteralPath $Path) { Get-Content -Raw -LiteralPath $Path } else { '' }
    $after = ConvertTo-PrettyJson $Catalog
    $changed = ($before.Trim() -ne $after.Trim()) -or (Test-HasUtf8Bom $Path)

    if ($changed) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
        Backup-File -Path $Path -Prefix $Prefix -Keep $Keep
        Write-Utf8NoBom -Path $Path -Text ($after + [Environment]::NewLine)
    }

    return [pscustomobject]@{
        path = $Path
        changed = $changed
        model_count = @($Catalog.models).Count
    }
}

$catalogPath = Join-Path $CodexHome 'model_catalog.json'
$cachePath = Join-Path $CodexHome 'models_cache.json'

$catalog = Read-Catalog -Path $catalogPath
$catalogResults = @(
    (Write-CatalogIfChanged -Path $catalogPath -Catalog $catalog -Prefix 'bak-modelsync' -Keep $BackupKeep),
    (Write-CatalogIfChanged -Path $cachePath -Catalog $catalog -Prefix 'bak-modelsync' -Keep $BackupKeep)
)

$changed = [bool](@($catalogResults | Where-Object { $_.changed }).Count -gt 0)
$result = [pscustomobject]@{
    changed = $changed
    source = $catalogPath
    model_count = @($catalog.models).Count
    catalogs = $catalogResults
}

if ($Json) {
    $result | ConvertTo-Json -Depth 16 -Compress
} else {
    Write-Info ("Model catalog sync complete. changed={0}, models={1}" -f $changed, @($catalog.models).Count)
    foreach ($catalogResult in $catalogResults) {
        Write-Info ("  {0}: changed={1}, count={2}" -f
            $catalogResult.path,
            $catalogResult.changed,
            $catalogResult.model_count)
    }
}

# Sync-Codex-ModelCatalog.ps1
#
# Keep Codex's local model catalog files parseable and in sync.
# Source of truth:
#   ~/.codex/model_catalog.json  - manually curated model catalog
#   ~/.codex/models_cache.json   - mirror used by some local flows
#
# Existing gpt-5.6 Sol/Terra/Luna entries receive the verified Max and Ultra
# effort metadata used by Settings > Agent > Model features. This script does
# not read tray_config.json, add models, or add model_catalog_json to
# config.toml. Catalog opt-in stays manual because some Desktop builds can fail
# during startup when forced to load an incompatible custom catalog.

[CmdletBinding()]
param(
    [string]$CodexHome = (Join-Path $env:USERPROFILE '.codex'),
    [switch]$SkipKnownModelFeatures,
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

function Test-Truthy([string]$Value) {
    return $Value -match '^(1|true|yes|on)$'
}

function Get-ModelSlug($Model) {
    if ($Model.PSObject.Properties.Name -contains 'slug') {
        return [string]$Model.slug
    }
    if ($Model.PSObject.Properties.Name -contains 'id') {
        return [string]$Model.id
    }
    return ''
}

function Ensure-KnownModelFeatures($Catalog) {
    $disabled = $SkipKnownModelFeatures -or
        (Test-Truthy ([string]$env:CODEX_MODEL_FEATURE_SYNC_DISABLE))
    if ($disabled) { return @() }

    $knownModels = @{
        'gpt-5.6-sol' = @(
            [ordered]@{
                effort = 'max'
                description = 'Maximum reasoning depth for the hardest problems'
            },
            [ordered]@{
                effort = 'ultra'
                description = 'Ultra orchestration with provider-compatible maximum reasoning'
            }
        )
        'gpt-5.6-terra' = @(
            [ordered]@{
                effort = 'max'
                description = 'Maximum reasoning depth for the hardest problems'
            },
            [ordered]@{
                effort = 'ultra'
                description = 'Ultra orchestration with provider-compatible maximum reasoning'
            }
        )
        'gpt-5.6-luna' = @(
            [ordered]@{
                effort = 'max'
                description = 'Maximum reasoning depth for the hardest problems'
            },
            [ordered]@{
                effort = 'ultra'
                description = 'Ultra orchestration with provider-compatible maximum reasoning'
            }
        )
    }

    $changes = New-Object 'System.Collections.Generic.List[object]'
    foreach ($model in @($Catalog.models)) {
        $slug = Get-ModelSlug $model
        if (-not $knownModels.ContainsKey($slug)) { continue }

        $levels = New-Object 'System.Collections.Generic.List[object]'
        if ($model.PSObject.Properties.Name -contains 'supported_reasoning_levels') {
            foreach ($level in @($model.supported_reasoning_levels)) {
                [void]$levels.Add($level)
            }
        }
        $existingEfforts = @($levels | ForEach-Object {
            if ($_ -is [string]) { [string]$_ } else { [string]$_.effort }
        })

        foreach ($definition in $knownModels[$slug]) {
            if ($existingEfforts -contains $definition.effort) { continue }

            [void]$levels.Add([pscustomobject]$definition)
            $existingEfforts += $definition.effort
            [void]$changes.Add([pscustomobject]@{
                model = $slug
                effort = $definition.effort
            })
        }

        if ($model.PSObject.Properties.Name -contains 'supported_reasoning_levels') {
            $model.supported_reasoning_levels = $levels.ToArray()
        } else {
            $model | Add-Member -NotePropertyName supported_reasoning_levels -NotePropertyValue $levels.ToArray()
        }
    }

    return $changes.ToArray()
}

function Write-CatalogIfChanged([string]$Path, $Catalog) {
    $before = if (Test-Path -LiteralPath $Path) { Get-Content -Raw -LiteralPath $Path } else { '' }
    $after = ConvertTo-PrettyJson $Catalog
    $changed = ($before.Trim() -ne $after.Trim()) -or (Test-HasUtf8Bom $Path)

    if ($changed) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
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
$featureChanges = @(Ensure-KnownModelFeatures -Catalog $catalog)
$catalogResults = @(
    (Write-CatalogIfChanged -Path $catalogPath -Catalog $catalog),
    (Write-CatalogIfChanged -Path $cachePath -Catalog $catalog)
)

$changed = [bool](@($catalogResults | Where-Object { $_.changed }).Count -gt 0)
$result = [pscustomobject]@{
    changed = $changed
    source = $catalogPath
    model_count = @($catalog.models).Count
    feature_changes = $featureChanges
    feature_sync_disabled = [bool](
        $SkipKnownModelFeatures -or
        (Test-Truthy ([string]$env:CODEX_MODEL_FEATURE_SYNC_DISABLE))
    )
    catalogs = $catalogResults
}

if ($Json) {
    $result | ConvertTo-Json -Depth 16 -Compress
} else {
    Write-Info ("Model catalog sync complete. changed={0}, models={1}, feature_changes={2}" -f
        $changed,
        @($catalog.models).Count,
        $featureChanges.Count)
    foreach ($catalogResult in $catalogResults) {
        Write-Info ("  {0}: changed={1}, count={2}" -f
            $catalogResult.path,
            $catalogResult.changed,
            $catalogResult.model_count)
    }
}

# Sync-Codex-ModelCatalog.ps1
#
# Keep Codex's local model catalog files parseable and in sync.
# Source of truth:
#   ~/.codex/model_catalog.json  - manually curated model catalog
#   ~/.codex/models_cache.json   - ModelsCacheEntry used by newer codex builds
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

function Read-Utf8Text([string]$Path) {
    $utf8Strict = [Text.UTF8Encoding]::new($false, $true)
    $text = [IO.File]::ReadAllText($Path, $utf8Strict)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) {
        return $text.Substring(1)
    }
    return $text
}

function Read-Catalog([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Model catalog not found: $Path"
    }

    try {
        $catalog = Read-Utf8Text -Path $Path | ConvertFrom-Json
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

function Test-Rfc3339Timestamp([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value) -or
        $Value -notmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$') {
        return $false
    }

    $parsed = [DateTimeOffset]::MinValue
    return [DateTimeOffset]::TryParse(
        [string]$Value,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind,
        [ref]$parsed
    )
}

function ConvertFrom-JsonPreservingDates([string]$Text) {
    # PowerShell 7.5+ otherwise turns RFC3339 strings into DateTime objects.
    # Windows PowerShell 5.1 keeps them as strings and has no -DateKind switch.
    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) {
        return $Text | ConvertFrom-Json -DateKind String
    }
    return $Text | ConvertFrom-Json
}

function Get-Rfc3339TimestampString([object]$Value) {
    if ($Value -is [string]) {
        return [string]$Value
    }
    if ($Value -is [DateTimeOffset]) {
        return ([DateTimeOffset]$Value).ToUniversalTime().ToString('o')
    }
    if ($Value -is [DateTime]) {
        return ([DateTime]$Value).ToUniversalTime().ToString('o')
    }
    return $null
}

function Test-JsonEquivalent($Left, $Right) {
    return (
        (ConvertTo-Json -InputObject @($Left) -Depth 96 -Compress) -ceq
        (ConvertTo-Json -InputObject @($Right) -Depth 96 -Compress)
    )
}

function New-ModelsCacheEntry([string]$Path, $Models) {
    $existing = $null
    $existingFetchedAt = $null
    if (Test-Path -LiteralPath $Path) {
        try {
            $existingText = Read-Utf8Text -Path $Path
            $existing = ConvertFrom-JsonPreservingDates $existingText
            if ($existing.PSObject.Properties.Name -contains 'fetched_at') {
                $existingFetchedAt = Get-Rfc3339TimestampString $existing.fetched_at
            }
        } catch {
            # A malformed or legacy cache is repaired from the curated catalog.
            $existing = $null
        }
    }

    $requiredProperties = @('fetched_at', 'etag', 'client_version', 'identity', 'models')
    $hasEnvelope = $null -ne $existing
    foreach ($property in $requiredProperties) {
        if ($null -eq $existing -or -not ($existing.PSObject.Properties.Name -contains $property)) {
            $hasEnvelope = $false
            break
        }
    }

    $canPreserve = $hasEnvelope -and
        (Test-Rfc3339Timestamp $existingFetchedAt) -and
        (Test-JsonEquivalent -Left $existing.models -Right $Models)

    if ($canPreserve) {
        return [ordered]@{
            fetched_at = $existingFetchedAt
            etag = $existing.etag
            client_version = $existing.client_version
            identity = $existing.identity
            models = $Models
        }
    }

    return [ordered]@{
        fetched_at = (Get-Date).ToUniversalTime().ToString('o')
        etag = $null
        client_version = $null
        identity = $null
        models = $Models
    }
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
    $before = if (Test-Path -LiteralPath $Path) { Read-Utf8Text -Path $Path } else { '' }
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

# models_cache.json is read by newer codex builds as a ModelsCacheEntry
# envelope { fetched_at, etag, client_version, identity, models }. A bare
# {"models": [...]} mirror makes the models manager fail with
# "missing field `fetched_at`" and the Desktop model list comes up empty.
$cacheEntry = New-ModelsCacheEntry -Path $cachePath -Models @($catalog.models)
$catalogResults = @(
    (Write-CatalogIfChanged -Path $catalogPath -Catalog $catalog),
    (Write-CatalogIfChanged -Path $cachePath -Catalog ([pscustomobject]$cacheEntry))
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

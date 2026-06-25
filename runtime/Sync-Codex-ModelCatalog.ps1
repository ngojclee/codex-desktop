# Sync-Codex-ModelCatalog.ps1
#
# Keep Codex Desktop's model picker aligned with the local tray pinned models.
# Source of truth:
#   ~/.codex/model_catalog.json  - app-server/debug models catalog
#   ~/.codex/models_cache.json   - same shape, used by some local flows
#   ~/.codex/tray_config.json    - user curated pinned proxy/custom models
#
# By default this syncs every tray_config.json:model_catalog entry so models
# added by the tray/proxy config appear in Desktop. Set
# CODEX_MODEL_SYNC_PINNED_ONLY=1 to import only tray_config.json:pinned_models
# when you want a smaller picker.

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

function Read-JsonFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
}

function ConvertTo-PrettyJson($Value) {
    $Value | ConvertTo-Json -Depth 96
}

function Clone-JsonObject($Value) {
    return ($Value | ConvertTo-Json -Depth 96 | ConvertFrom-Json)
}

function Get-ModelId($Entry) {
    if ($null -eq $Entry) { return $null }
    if ($Entry.PSObject.Properties.Name -contains 'id') { return [string]$Entry.id }
    if ($Entry.PSObject.Properties.Name -contains 'slug') { return [string]$Entry.slug }
    return $null
}

function Get-ModelLabel($Entry, [string]$Fallback) {
    foreach ($name in @('desc', 'display_name', 'displayName', 'name')) {
        if ($Entry.PSObject.Properties.Name -contains $name) {
            $value = [string]$Entry.$name
            if (-not [string]::IsNullOrWhiteSpace($value)) { return $value.Trim() }
        }
    }
    return $Fallback
}

function Ensure-Array($Value) {
    if ($null -eq $Value) { return @() }
    return @($Value)
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

function Set-PropertyValue($Object, [string]$Name, $Value) {
    if ($Object.PSObject.Properties.Name -contains $Name) {
        $Object.$Name = $Value
    } else {
        Add-Member -InputObject $Object -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function New-CatalogModel($Template, [string]$Id, [string]$Label, [int]$Priority) {
    $model = Clone-JsonObject $Template
    Set-PropertyValue $model 'slug' $Id
    Set-PropertyValue $model 'display_name' $Label
    Set-PropertyValue $model 'description' 'Pinned custom model from tray_config.json.'
    Set-PropertyValue $model 'visibility' 'list'
    Set-PropertyValue $model 'supported_in_api' $true
    Set-PropertyValue $model 'priority' $Priority
    Set-PropertyValue $model 'availability_nux' $null
    Set-PropertyValue $model 'upgrade' $null

    if ($model.PSObject.Properties.Name -contains 'service_tiers') {
        Set-PropertyValue $model 'service_tiers' @()
    }
    if ($model.PSObject.Properties.Name -contains 'additional_speed_tiers') {
        Set-PropertyValue $model 'additional_speed_tiers' @()
    }
    return $model
}

function Sync-OneCatalog([string]$Path, [array]$DesiredEntries) {
    $catalog = Read-JsonFile $Path
    if ($null -eq $catalog) {
        $catalog = [pscustomobject]@{ models = @() }
    }
    if (-not ($catalog.PSObject.Properties.Name -contains 'models')) {
        Add-Member -InputObject $catalog -NotePropertyName 'models' -NotePropertyValue @()
    }

    $models = Ensure-Array $catalog.models
    if ($models.Count -eq 0) {
        throw "Cannot sync $Path because it has no model template."
    }

    $template = @($models | Where-Object { $_.slug -eq 'gpt-5.5' } | Select-Object -First 1)
    if ($template.Count -eq 0) {
        $template = @($models | Where-Object { $_.slug -eq 'gpt-5.4' } | Select-Object -First 1)
    }
    if ($template.Count -eq 0) {
        $template = @($models | Select-Object -First 1)
    }

    $bySlug = @{}
    foreach ($model in $models) {
        if ($model.PSObject.Properties.Name -contains 'slug' -and $model.slug) {
            $bySlug[[string]$model.slug] = $model
        }
    }

    $added = @()
    $updated = @()
    $priority = 20
    foreach ($entry in $DesiredEntries) {
        $id = Get-ModelId $entry
        if ([string]::IsNullOrWhiteSpace($id)) { continue }
        $label = Get-ModelLabel $entry $id
        if ($bySlug.ContainsKey($id)) {
            $existing = $bySlug[$id]
            $changedExisting = $false
            if (($existing.PSObject.Properties.Name -contains 'display_name') -and [string]$existing.display_name -ne $label) {
                $existing.display_name = $label
                $changedExisting = $true
            }
            if (($existing.PSObject.Properties.Name -contains 'visibility') -and [string]$existing.visibility -ne 'list') {
                $existing.visibility = 'list'
                $changedExisting = $true
            }
            if (($existing.PSObject.Properties.Name -contains 'supported_in_api') -and $existing.supported_in_api -ne $true) {
                $existing.supported_in_api = $true
                $changedExisting = $true
            }
            if ($changedExisting) { $updated += $id }
        } else {
            $newModel = New-CatalogModel -Template $template[0] -Id $id -Label $label -Priority $priority
            $models += $newModel
            $bySlug[$id] = $newModel
            $added += $id
        }
        $priority++
    }

    $before = if (Test-Path -LiteralPath $Path) { Get-Content -Raw -LiteralPath $Path } else { '' }
    $catalog.models = @($models | Sort-Object @{ Expression = {
        if ($_.PSObject.Properties.Name -contains 'priority') { [int]$_.priority } else { 999999 }
    } }, @{ Expression = {
        if ($_.PSObject.Properties.Name -contains 'slug') { [string]$_.slug } else { '' }
    } })
    $after = ConvertTo-PrettyJson $catalog

    $changed = $before.Trim() -ne $after.Trim()
    if ($changed) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
        Backup-File -Path $Path -Prefix 'bak-modelsync' -Keep $BackupKeep
        $after | Set-Content -LiteralPath $Path -Encoding UTF8
    }

    return [pscustomobject]@{
        path = $Path
        changed = $changed
        added = $added
        updated = $updated
        model_count = @($catalog.models).Count
    }
}

$trayPath = Join-Path $CodexHome 'tray_config.json'
$catalogPath = Join-Path $CodexHome 'model_catalog.json'
$cachePath = Join-Path $CodexHome 'models_cache.json'
$tray = Read-JsonFile $trayPath

if ($null -eq $tray) {
    $result = [pscustomobject]@{
        changed = $false
        reason = "tray_config.json not found"
        tray = $trayPath
        catalogs = @()
    }
    if ($Json) { $result | ConvertTo-Json -Depth 16 -Compress } else { Write-Info $result.reason }
    return
}

$trayModels = Ensure-Array $tray.model_catalog
$pinned = Ensure-Array $tray.pinned_models
$syncPinnedOnly = $env:CODEX_MODEL_SYNC_PINNED_ONLY -eq '1'
$desired = if ($syncPinnedOnly) {
    $pinnedSet = @{}
    foreach ($id in $pinned) { $pinnedSet[[string]$id] = $true }
    @($trayModels | Where-Object { $pinnedSet.ContainsKey((Get-ModelId $_)) })
} else {
    $trayModels
}

$catalogResults = @()
foreach ($path in @($catalogPath, $cachePath)) {
    $catalogResults += Sync-OneCatalog -Path $path -DesiredEntries $desired
}

$changed = [bool](@($catalogResults | Where-Object { $_.changed }).Count -gt 0)
$result = [pscustomobject]@{
    changed = $changed
    sync_all = -not $syncPinnedOnly
    pinned_only = $syncPinnedOnly
    desired_count = @($desired).Count
    desired_models = @($desired | ForEach-Object { Get-ModelId $_ })
    catalogs = $catalogResults
}

if ($Json) {
    $result | ConvertTo-Json -Depth 32 -Compress
} else {
    Write-Info ("Model catalog sync complete. changed={0}, desired={1}" -f $changed, @($desired).Count)
    foreach ($catalogResult in $catalogResults) {
        Write-Info ("  {0}: changed={1}, added={2}, updated={3}, count={4}" -f
            $catalogResult.path,
            $catalogResult.changed,
            (($catalogResult.added | ForEach-Object { [string]$_ }) -join ','),
            (($catalogResult.updated | ForEach-Object { [string]$_ }) -join ','),
            $catalogResult.model_count)
    }
}

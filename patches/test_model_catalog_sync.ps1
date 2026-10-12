$ErrorActionPreference = 'Stop'

function Assert-True {
    param(
        [Parameter(Mandatory=$true)][bool]$Condition,
        [Parameter(Mandatory=$true)][string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Write-Utf8NoBomJson {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)]$Value
    )

    $text = $Value | ConvertTo-Json -Depth 32
    [IO.File]::WriteAllText($Path, $text + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
}

function Get-JsonHash([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Test-HasUtf8Bom([string]$Path) {
    $bytes = [IO.File]::ReadAllBytes($Path)
    return $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
}

function ConvertTo-Rfc3339String([object]$Value) {
    if ($Value -is [DateTime]) {
        return ([DateTime]$Value).ToUniversalTime().ToString('o')
    }
    if ($Value -is [DateTimeOffset]) {
        return ([DateTimeOffset]$Value).ToUniversalTime().ToString('o')
    }
    return [string]$Value
}

function Assert-CacheEnvelope {
    param(
        [Parameter(Mandatory=$true)]$Cache,
        [Parameter(Mandatory=$true)]$ExpectedModels
    )

    $properties = @($Cache.PSObject.Properties.Name)
    foreach ($name in @('fetched_at', 'etag', 'client_version', 'identity', 'models')) {
        Assert-True ($properties -contains $name) "Cache is missing ModelsCacheEntry field '$name'."
    }

    $parsedTimestamp = [DateTimeOffset]::MinValue
    Assert-True (
        [DateTimeOffset]::TryParse(
            [string]$Cache.fetched_at,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind,
            [ref]$parsedTimestamp
        )
    ) 'Cache fetched_at is not a valid RFC3339 timestamp.'
    Assert-True (
        ((ConvertTo-Json -InputObject @($Cache.models) -Depth 32 -Compress) -ceq
         (ConvertTo-Json -InputObject @($ExpectedModels) -Depth 32 -Compress))
    ) 'Cache models do not match the curated catalog.'
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$syncScript = Join-Path $repoRoot 'runtime\Sync-Codex-ModelCatalog.ps1'
$tempRoot = Join-Path $env:TEMP ("codex-model-sync-test-{0}" -f [guid]::NewGuid().ToString('N'))
$unicodeDescription = "User$([char]0x2019)s model"

try {
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null

    $catalog = [ordered]@{
        models = @(
            [ordered]@{
                slug = 'gpt-5.6-sol'
                supported_reasoning_levels = @(
                    [ordered]@{ effort = 'low'; description = 'Low' },
                    [ordered]@{ effort = 'xhigh'; description = 'Extra high' }
                )
            },
            [ordered]@{
                slug = 'gpt-5.6-terra'
                supported_reasoning_levels = @(
                    [ordered]@{ effort = 'max'; description = 'Existing Max' }
                )
            },
            [ordered]@{
                slug = 'gpt-5.6-luna'
                supported_reasoning_levels = @()
            },
            [ordered]@{
                slug = 'gpt-5.5'
                description = $unicodeDescription
                supported_reasoning_levels = @(
                    [ordered]@{ effort = 'xhigh'; description = 'Extra high' }
                )
            }
        )
    }

    $catalogPath = Join-Path $tempRoot 'model_catalog.json'
    $configPath = Join-Path $tempRoot 'config.toml'
    Write-Utf8NoBomJson -Path $catalogPath -Value $catalog
    'model = "gpt-5.5"' | Set-Content -LiteralPath $configPath -Encoding utf8
    $configHashBefore = (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash

    $first = (& $syncScript -CodexHome $tempRoot -Json) | ConvertFrom-Json
    Assert-True ($first.changed -eq $true) 'First sync should update the catalog.'
    Assert-True (@($first.feature_changes).Count -eq 5) 'Expected five missing feature efforts.'

    $updated = Get-Content -Raw -LiteralPath $catalogPath | ConvertFrom-Json
    foreach ($slug in @('gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna')) {
        $model = @($updated.models | Where-Object { $_.slug -eq $slug })[0]
        $efforts = @($model.supported_reasoning_levels | ForEach-Object { $_.effort })
        Assert-True ($efforts -contains 'max') "$slug is missing Max."
        Assert-True ($efforts -contains 'ultra') "$slug is missing Ultra."
    }

    $other = @($updated.models | Where-Object { $_.slug -eq 'gpt-5.5' })[0]
    $otherEfforts = @($other.supported_reasoning_levels | ForEach-Object { $_.effort })
    Assert-True ($other.description -eq $unicodeDescription) 'UTF-8 catalog text was corrupted.'
    Assert-True ($otherEfforts -notcontains 'max') 'Unverified models must not receive Max.'
    Assert-True ($otherEfforts -notcontains 'ultra') 'Unverified models must not receive Ultra.'

    $cachePath = Join-Path $tempRoot 'models_cache.json'
    Assert-True (Test-Path -LiteralPath $cachePath) 'models_cache.json was not created.'
    $cache = Get-Content -Raw -LiteralPath $cachePath | ConvertFrom-Json
    Assert-CacheEnvelope -Cache $cache -ExpectedModels $updated.models
    Assert-True ($null -eq $cache.etag) 'New cache etag must default to null.'
    Assert-True ($null -eq $cache.client_version) 'New cache client_version must default to null.'
    Assert-True ($null -eq $cache.identity) 'New cache identity must default to null.'
    Assert-True (-not (Test-HasUtf8Bom $catalogPath)) 'Catalog must be UTF-8 without BOM.'
    Assert-True (-not (Test-HasUtf8Bom $cachePath)) 'Cache must be UTF-8 without BOM.'
    Assert-True (
        $configHashBefore -eq (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash
    ) 'The sync must not edit config.toml or add model_catalog_json.'

    $catalogHashBeforeSecond = Get-JsonHash $catalogPath
    $cacheHashBeforeSecond = Get-JsonHash $cachePath
    $second = (& $syncScript -CodexHome $tempRoot -Json) | ConvertFrom-Json
    Assert-True ($second.changed -eq $false) 'Second sync should be idempotent.'
    Assert-True (@($second.feature_changes).Count -eq 0) 'Second sync should add no efforts.'
    Assert-True ((Get-JsonHash $catalogPath) -eq $catalogHashBeforeSecond) 'Second sync changed catalog bytes.'
    Assert-True ((Get-JsonHash $cachePath) -eq $cacheHashBeforeSecond) 'Second sync changed cache bytes.'

    # Migrate the legacy bare { models: [...] } cache to ModelsCacheEntry.
    Write-Utf8NoBomJson -Path $cachePath -Value ([ordered]@{ models = $updated.models })
    $legacy = (& $syncScript -CodexHome $tempRoot -Json) | ConvertFrom-Json
    Assert-True ($legacy.changed -eq $true) 'Legacy bare cache should be repaired.'
    $migrated = Get-Content -Raw -LiteralPath $cachePath | ConvertFrom-Json
    Assert-CacheEnvelope -Cache $migrated -ExpectedModels $updated.models
    Assert-True ($null -eq $migrated.etag) 'Migrated cache etag must default to null.'
    Assert-True ($null -eq $migrated.client_version) 'Migrated cache client_version must default to null.'
    Assert-True ($null -eq $migrated.identity) 'Migrated cache identity must default to null.'

    $invalidEnvelope = [ordered]@{
        fetched_at = 'not-a-timestamp'
        etag = 'stale-etag'
        client_version = 'stale-version'
        identity = [ordered]@{ account_id = 'stale-account' }
        models = $updated.models
    }
    Write-Utf8NoBomJson -Path $cachePath -Value $invalidEnvelope
    $repaired = (& $syncScript -CodexHome $tempRoot -Json) | ConvertFrom-Json
    Assert-True ($repaired.changed -eq $true) 'Invalid cache timestamp should be repaired.'
    $repairedCache = Get-Content -Raw -LiteralPath $cachePath | ConvertFrom-Json
    Assert-CacheEnvelope -Cache $repairedCache -ExpectedModels $updated.models
    Assert-True ($null -eq $repairedCache.etag) 'Repaired cache must clear stale etag.'
    Assert-True ($null -eq $repairedCache.client_version) 'Repaired cache must clear stale client_version.'
    Assert-True ($null -eq $repairedCache.identity) 'Repaired cache must clear stale identity.'

    # Preserve valid envelope metadata and timestamp when models are unchanged.
    $preservedTimestamp = '2026-10-12T01:02:03.4567890Z'
    $preservedIdentity = [ordered]@{ account_id = 'test-account'; auth_mode = 'test' }
    $validEnvelope = [ordered]@{
        fetched_at = $preservedTimestamp
        etag = 'test-etag'
        client_version = '0.162.1'
        identity = $preservedIdentity
        models = $updated.models
    }
    Write-Utf8NoBomJson -Path $cachePath -Value $validEnvelope
    $validHashBefore = Get-JsonHash $cachePath
    $preserved = (& $syncScript -CodexHome $tempRoot -Json) | ConvertFrom-Json
    Assert-True ($preserved.changed -eq $false) 'Valid unchanged cache should remain unchanged.'
    Assert-True ((Get-JsonHash $cachePath) -eq $validHashBefore) 'Valid cache envelope was not byte-idempotent.'
    $preservedCache = Get-Content -Raw -LiteralPath $cachePath | ConvertFrom-Json
    Assert-True ((ConvertTo-Rfc3339String $preservedCache.fetched_at) -eq $preservedTimestamp) 'Valid fetched_at was not preserved.'
    Assert-True ($preservedCache.etag -eq 'test-etag') 'Valid etag was not preserved.'
    Assert-True ($preservedCache.client_version -eq '0.162.1') 'Valid client_version was not preserved.'
    Assert-True ($preservedCache.identity.account_id -eq 'test-account') 'Valid identity was not preserved.'

    # A real model change invalidates old cache metadata and refreshes fetched_at.
    $changedCatalog = Get-Content -Raw -LiteralPath $catalogPath | ConvertFrom-Json
    $changedCatalog.models[0] | Add-Member -NotePropertyName description -NotePropertyValue 'Changed model metadata'
    Write-Utf8NoBomJson -Path $catalogPath -Value $changedCatalog
    $refreshed = (& $syncScript -CodexHome $tempRoot -Json) | ConvertFrom-Json
    Assert-True ($refreshed.changed -eq $true) 'Changed models should refresh the cache.'
    $refreshedCache = Get-Content -Raw -LiteralPath $cachePath | ConvertFrom-Json
    Assert-CacheEnvelope -Cache $refreshedCache -ExpectedModels $changedCatalog.models
    Assert-True ($refreshedCache.fetched_at -ne $preservedTimestamp) 'Changed models kept a stale fetched_at.'
    Assert-True ($null -eq $refreshedCache.etag) 'Changed models must clear stale etag.'
    Assert-True ($null -eq $refreshedCache.client_version) 'Changed models must clear stale client_version.'
    Assert-True ($null -eq $refreshedCache.identity) 'Changed models must clear stale identity.'

    Assert-True (
        $configHashBefore -eq (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash
    ) 'The sync must never edit config.toml.'

    Write-Host 'Model catalog ModelsCacheEntry sync tests passed.'
} finally {
    $expectedPrefix = Join-Path $env:TEMP 'codex-model-sync-test-'
    if ($tempRoot.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

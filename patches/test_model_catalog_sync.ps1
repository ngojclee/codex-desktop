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
    $catalog | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $catalogPath -Encoding utf8
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
    Assert-True (
        (Get-FileHash -LiteralPath $catalogPath -Algorithm SHA256).Hash -eq
        (Get-FileHash -LiteralPath $cachePath -Algorithm SHA256).Hash
    ) 'Catalog and cache must be byte-identical.'
    Assert-True (
        $configHashBefore -eq (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash
    ) 'The sync must not edit config.toml or add model_catalog_json.'

    $second = (& $syncScript -CodexHome $tempRoot -Json) | ConvertFrom-Json
    Assert-True ($second.changed -eq $false) 'Second sync should be idempotent.'
    Assert-True (@($second.feature_changes).Count -eq 0) 'Second sync should add no efforts.'

    Write-Host 'Model catalog feature sync tests passed.'
} finally {
    $expectedPrefix = Join-Path $env:TEMP 'codex-model-sync-test-'
    if ($tempRoot.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

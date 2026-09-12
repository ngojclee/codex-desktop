param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'CodexFromGithub'),
    [int]$CpuSampleSeconds = 3
)

$ErrorActionPreference = 'Continue'

function Write-Section([string]$Name) {
    Write-Host ""
    Write-Host "=== $Name ==="
}

function Get-CodexInstallProcesses {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and
        $_.ExecutablePath.StartsWith($InstallDir, [System.StringComparison]::OrdinalIgnoreCase)
    })
}

function Get-BundledPluginCacheProcesses {
    $pluginRoots = @(
        (Join-Path $env:USERPROFILE '.codex\.tmp\bundled-marketplaces'),
        (Join-Path $env:USERPROFILE '.codex\plugins\cache\openai-bundled')
    )

    @(Get-CimInstance Win32_Process | Where-Object {
        if (-not $_.ExecutablePath) { return $false }
        foreach ($root in $pluginRoots) {
            if ($_.ExecutablePath.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
                return $true
            }
        }
        return $false
    })
}

function Get-MarketplacePluginNames([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    try {
        $json = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
        return @(@($json.plugins) | ForEach-Object {
            if ($_ -is [string]) {
                $_
            } elseif ($_.PSObject.Properties.Name -contains 'name') {
                [string]$_.name
            } elseif ($_.PSObject.Properties.Name -contains 'id') {
                [string]$_.id
            }
        } | Where-Object { $_ })
    } catch {
        return @()
    }
}

function Get-RuntimeMarketplacePluginNames([string]$RuntimeRoot) {
    $runtimeMarketplace = Join-Path $RuntimeRoot '.agents\plugins\marketplace.json'
    $names = @(Get-MarketplacePluginNames $runtimeMarketplace)
    if ($names.Count -gt 0) { return $names }

    $pluginsDir = Join-Path $RuntimeRoot 'plugins'
    if (-not (Test-Path -LiteralPath $pluginsDir)) { return @() }

    return @(Get-ChildItem -LiteralPath $pluginsDir -Directory -Force -EA SilentlyContinue |
        ForEach-Object { $_.Name } |
        Where-Object { $_ })
}

Write-Section 'Machine'
[pscustomobject]@{
    Time       = (Get-Date).ToString('o')
    Hostname   = $env:COMPUTERNAME
    User       = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    InstallDir = $InstallDir
} | Format-List

Write-Section 'Codex install processes'
$installProcesses = Get-CodexInstallProcesses
$installProcesses | Group-Object Name | Sort-Object Name | Select-Object Name,Count | Format-Table -AutoSize
$installProcesses | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine | Format-List

Write-Section 'Shared sidecar'
$stateFile = Join-Path $env:USERPROFILE '.codex\desktop-shared-app-server.json'
if (Test-Path -LiteralPath $stateFile) {
    $stateRaw = Get-Content -Raw -LiteralPath $stateFile
    $stateRaw
    try {
        $state = $stateRaw | ConvertFrom-Json
        Invoke-WebRequest "http://127.0.0.1:$($state.port)/healthz" -UseBasicParsing -TimeoutSec 3 |
            Select-Object StatusCode,Content | Format-List
    } catch {
        Write-Host "healthz_error=$($_.Exception.Message)"
    }
} else {
    Write-Host 'NO_SHARED_STATE'
}

Write-Section 'App-server lanes'
Get-CimInstance Win32_Process |
    Where-Object { $_.Name -ieq 'codex.exe' -and $_.CommandLine -match '\bapp-server\b' } |
    Group-Object {
        if ($_.CommandLine -match '--listen\s+ws://') { 'ws' }
        elseif ($_.CommandLine -match '--listen\s+stdio://') { 'stdio' }
        else { 'private/other' }
    } |
    Select-Object Name,Count | Format-Table -AutoSize

Write-Section 'Bundled plugin marketplace'
$bundleMarketplace = Join-Path $InstallDir 'resources\plugins\openai-bundled\.agents\plugins\marketplace.json'
$runtimeRoot = Join-Path $env:USERPROFILE '.codex\.tmp\bundled-marketplaces\openai-bundled'
$bundlePlugins = @(Get-MarketplacePluginNames $bundleMarketplace)
$runtimePlugins = @(Get-RuntimeMarketplacePluginNames $runtimeRoot)
[pscustomobject]@{
    BundleMarketplace = $bundleMarketplace
    BundlePlugins     = ($bundlePlugins -join ',')
    RuntimeRoot       = $runtimeRoot
    RuntimeExists     = (Test-Path -LiteralPath $runtimeRoot)
    RuntimePlugins    = ($runtimePlugins -join ',')
} | Format-List

Write-Section 'Bundled plugin helper processes'
Get-BundledPluginCacheProcesses |
    Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine |
    Format-List

Write-Section 'MCP processes'
Get-CimInstance Win32_Process |
    Where-Object { ($_.Name -match '^(node|powershell|pwsh)\.exe$') -and ($_.CommandLine -match 'run-mcp-server|remote-mcp|mcp_servers|/mcp|3110') } |
    Select-Object ProcessId,ParentProcessId,Name,CommandLine |
    Sort-Object ParentProcessId,ProcessId |
    Format-List

Write-Section 'MCP network connections'
Get-NetTCPConnection -ErrorAction SilentlyContinue |
    Where-Object { $_.RemotePort -eq 3110 -or $_.RemoteAddress -eq '10.21.4.101' } |
    Group-Object RemoteAddress,RemotePort,State,OwningProcess |
    Sort-Object Count -Descending |
    Select-Object Count,Name |
    Format-Table -AutoSize

Write-Section "CPU sample ${CpuSampleSeconds}s"
$first = @{}
Get-Process | ForEach-Object {
    if ($null -ne $_.CPU) { $first[$_.Id] = $_.CPU }
}
Start-Sleep -Seconds $CpuSampleSeconds
Get-Process | ForEach-Object {
    if ($null -ne $_.CPU -and $first.ContainsKey($_.Id)) {
        [pscustomobject]@{
            Id       = $_.Id
            Name     = $_.ProcessName
            CpuPct   = [Math]::Round(100 * (($_.CPU - $first[$_.Id]) / [Math]::Max(1, $CpuSampleSeconds)), 1)
            Path     = $_.Path
        }
    }
} | Sort-Object CpuPct -Descending | Select-Object -First 20 | Format-Table -AutoSize

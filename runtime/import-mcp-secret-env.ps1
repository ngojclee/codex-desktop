param(
  [string]$SecretsPath = "$env:USERPROFILE\.codex\mcp-secrets.local.json",
  [string]$ConfigPath = "$env:USERPROFILE\.codex\config.toml",
  [string[]]$EnvName,
  [switch]$All
)

$ErrorActionPreference = "Stop"

function Expand-EnvString {
  param([string]$Value)

  if ($null -eq $Value) {
    return $Value
  }

  $expanded = [Environment]::ExpandEnvironmentVariables($Value)
  $expanded = $expanded.Replace('$env:USERPROFILE', $env:USERPROFILE)
  $expanded = $expanded.Replace('${env:USERPROFILE}', $env:USERPROFILE)
  return $expanded
}

if (-not (Test-Path -LiteralPath $SecretsPath)) {
  return
}

$requestedEnv = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($name in @($EnvName)) {
  if ($name -and $name -match '^[A-Za-z_][A-Za-z0-9_]*$') {
    [void]$requestedEnv.Add($name)
  }
}

if (-not $All -and $requestedEnv.Count -eq 0 -and (Test-Path -LiteralPath $ConfigPath)) {
  foreach ($match in [regex]::Matches((Get-Content -Raw -LiteralPath $ConfigPath), '(?m)^\s*bearer_token_env_var\s*=\s*["'']([^"'']+)["'']')) {
    $name = $match.Groups[1].Value
    if ($name -match '^[A-Za-z_][A-Za-z0-9_]*$') {
      [void]$requestedEnv.Add($name)
    }
  }
}

if (-not $All -and $requestedEnv.Count -eq 0) {
  return
}

$secrets = Get-Content -Raw -LiteralPath $SecretsPath | ConvertFrom-Json
if (-not $secrets.PSObject.Properties["mcpServers"]) {
  throw "Local MCP secrets file does not contain mcpServers: $SecretsPath"
}

foreach ($server in @($secrets.mcpServers.PSObject.Properties)) {
  $envObject = $server.Value.env
  if (-not $envObject) {
    continue
  }

  foreach ($entry in @($envObject.PSObject.Properties)) {
    if ($entry.Name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
      continue
    }
    if (-not $All -and -not $requestedEnv.Contains($entry.Name)) {
      continue
    }
    if ($null -eq $entry.Value -or [string]$entry.Value -eq '') {
      continue
    }
    [Environment]::SetEnvironmentVariable($entry.Name, (Expand-EnvString -Value ([string]$entry.Value)), "Process")
  }
}

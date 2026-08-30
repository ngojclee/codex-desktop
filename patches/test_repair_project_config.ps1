$ErrorActionPreference = 'Stop'

$root = Join-Path $env:TEMP ("codex-project-config-test-" + [guid]::NewGuid().Guid)
New-Item -ItemType Directory -Force -Path $root | Out-Null

try {
    $existing = Join-Path $root 'existing'
    New-Item -ItemType Directory -Force -Path $existing | Out-Null
    $missingBasic = Join-Path $root 'missing-basic'
    $missingLiteral = Join-Path $root 'missing-literal'
    $config = Join-Path $root 'config.toml'
    $escape = {
        param([string]$Path)
        return $Path.Replace('\', '\\').Replace('"', '\"')
    }
    $text = @"
model = "gpt-5.6-sol"

[projects."$(& $escape $existing)"]
trust_level = "trusted"

[projects."$(& $escape $missingBasic)"]
trust_level = "trusted"

[projects.'$missingLiteral']
trust_level = "trusted"

[mcp_servers.example]
command = "cmd.exe"
args = ["/c", "echo", "ok"]
"@
    [IO.File]::WriteAllText($config, $text, [Text.UTF8Encoding]::new($false))

    $repair = Join-Path $PSScriptRoot '..\runtime\Repair-Codex-ProjectConfig.ps1'
    $preview = & $repair -ConfigPath $config -Json | ConvertFrom-Json
    if ($preview.status -ne 'preview' -or @($preview.removed).Count -ne 2) {
        throw "Expected two unreachable project entries in preview, got $($preview | ConvertTo-Json -Compress)"
    }

    $applied = & $repair -ConfigPath $config -Apply -Json | ConvertFrom-Json
    if ($applied.status -ne 'repaired' -or -not $applied.backup) {
        throw "Expected project repair with backup, got $($applied | ConvertTo-Json -Compress)"
    }
    $after = [IO.File]::ReadAllText($config)
    $escapedExisting = $existing.Replace('\', '\\')
    if ($after -notmatch [regex]::Escape($escapedExisting) -or
        $after -match [regex]::Escape($missingBasic) -or
        $after -match [regex]::Escape($missingLiteral) -or
        $after -notmatch '\[mcp_servers\.example\]') {
        throw @"
Project repair did not preserve the expected config sections.
Existing: $existing
Missing: $missingBasic / $missingLiteral
Content:
$after
"@
    }

    $all = & $repair -ConfigPath $config -Apply -RemoveAll -Json | ConvertFrom-Json
    if ($all.status -ne 'repaired') {
        throw "Expected RemoveAll project repair, got $($all | ConvertTo-Json -Compress)"
    }
    $afterAll = [IO.File]::ReadAllText($config)
    if ($afterAll -match '\[projects\.' -or $afterAll -notmatch '\[mcp_servers\.example\]') {
        throw 'RemoveAll project repair did not preserve non-project config.'
    }

    Write-Host 'Project config repair tests passed.'
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}

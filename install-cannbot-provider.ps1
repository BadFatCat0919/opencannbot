<#
.SYNOPSIS
  Install the CANNBOT provider plugins for OpenCode on Windows.
.DESCRIPTION
  Installs both CANNBOT (standard models) and CANNBOT-Pro (Claude Opus / GPT).
  Safe to run either from a local clone (.\install-cannbot-provider.ps1)
  or piped from the web (irm <url> | iex).
#>
$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 defaults to TLS 1.0/1.1, which GitHub rejects; force 1.2.
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}

$RepoRaw      = if ($env:CANNBOT_REPO_RAW) { $env:CANNBOT_REPO_RAW } else { 'https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main' }
$PluginUrl    = "$RepoRaw/cannbot-auth.js"
$ProPluginUrl = "$RepoRaw/cannbot-pro-auth.js"

# OpenCode follows XDG-style paths; on Windows these live under the user profile.
$ConfigDir     = if ($env:XDG_CONFIG_HOME) { Join-Path $env:XDG_CONFIG_HOME 'opencode' } else { Join-Path $env:USERPROFILE '.config\opencode' }
$DataDir       = if ($env:XDG_DATA_HOME)   { Join-Path $env:XDG_DATA_HOME   'opencode' } else { Join-Path $env:USERPROFILE '.local\share\opencode' }
$PluginDir     = Join-Path $ConfigDir 'plugins'
$PluginFile    = Join-Path $PluginDir 'cannbot-auth.js'
$ProPluginFile = Join-Path $PluginDir 'cannbot-pro-auth.js'
$OpencodeJson  = Join-Path $ConfigDir 'opencode.json'

function Write-Bold($t)   { Write-Host $t -ForegroundColor White }
function Write-Green($t)  { Write-Host $t -ForegroundColor Green }
function Write-Red($t)    { Write-Host $t -ForegroundColor Red }

Write-Bold "======================================="
Write-Bold "  CANNBOT Provider for OpenCode"
Write-Bold "======================================="
Write-Host ""

# Use `return` not `exit`: when piped via `irm | iex`, `exit` would close the
# user's whole PowerShell window.
if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
  Write-Red "opencode not found. Please install opencode first."
  return
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  Write-Red "node not found."
  return
}

New-Item -ItemType Directory -Force -Path $PluginDir, $DataDir | Out-Null

# ── 1. Get plugins ──────────────────────────────────────────────────────
# $PSScriptRoot is empty when run via `irm | iex`; in that case skip the local
# copy and download. (Join-Path rejects an empty Path, so guard before calling it.)

$LocalPlugin = if ($PSScriptRoot) { Join-Path $PSScriptRoot 'cannbot-auth.js' } else { $null }
if ($LocalPlugin -and (Test-Path $LocalPlugin)) {
  Copy-Item $LocalPlugin $PluginFile -Force
  Write-Green "[1/4] CANNBOT plugin copied from local clone -> $PluginFile"
} else {
  Invoke-WebRequest -Uri $PluginUrl -OutFile $PluginFile -UseBasicParsing
  Write-Green "[1/4] CANNBOT plugin downloaded -> $PluginFile"
}

$LocalProPlugin = if ($PSScriptRoot) { Join-Path $PSScriptRoot 'cannbot-pro-auth.js' } else { $null }
if ($LocalProPlugin -and (Test-Path $LocalProPlugin)) {
  Copy-Item $LocalProPlugin $ProPluginFile -Force
  Write-Green "[2/4] CANNBOT-Pro plugin copied from local clone -> $ProPluginFile"
} else {
  Invoke-WebRequest -Uri $ProPluginUrl -OutFile $ProPluginFile -UseBasicParsing
  Write-Green "[2/4] CANNBOT-Pro plugin downloaded -> $ProPluginFile"
}

# ── 2. Update opencode.json ─────────────────────────────────────────────

$PluginUri    = "file://$($PluginFile    -replace '\\','/')"
$ProPluginUri = "file://$($ProPluginFile -replace '\\','/')"

if (Test-Path $OpencodeJson) {
  $cfg = Get-Content $OpencodeJson -Raw | ConvertFrom-Json
  $plugins = @()
  if ($cfg.PSObject.Properties.Name -contains 'plugin' -and $cfg.plugin) { $plugins = @($cfg.plugin) }
  if ($plugins -notcontains $PluginUri)    { $plugins += $PluginUri }
  if ($plugins -notcontains $ProPluginUri) { $plugins += $ProPluginUri }
  $cfg | Add-Member -NotePropertyName 'plugin' -NotePropertyValue $plugins -Force
  $cfg | ConvertTo-Json -Depth 20 | Set-Content -Path $OpencodeJson -Encoding UTF8
} else {
  $cfg = [ordered]@{
    '$schema' = 'https://opencode.ai/config.json'
    plugin    = @($PluginUri, $ProPluginUri)
  }
  $cfg | ConvertTo-Json -Depth 20 | Set-Content -Path $OpencodeJson -Encoding UTF8
}

Write-Green "[3/4] opencode.json updated -> $OpencodeJson"
Write-Green "[4/4] Done!"

Write-Host ""
Write-Bold "Restart opencode, then run:"
Write-Host ""
Write-Host "  /connect"
Write-Host ""
Write-Host "Select 'CANNBOT' (standard) or 'CANNBOT-Pro' (Claude Opus / GPT) and enter your VK."
Write-Host ""

<#
.SYNOPSIS
  Install the CANNBOT provider plugins for OpenCode on Windows.
.DESCRIPTION
  Installs both CANNBOT (standard models) and CANNBOT-Pro (Claude Opus / GPT).
  OpenCode 2.x needs the V2 plugin (cannbot-v2/index.js); OpenCode 1.x uses the
  legacy plugin files. The script picks the right layout automatically.
  Safe to run either from a local clone (.\install-cannbot-provider.ps1)
  or piped from the web (irm <url> | iex).
#>
$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 defaults to TLS 1.0/1.1, which GitHub rejects; force 1.2.
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}

$RepoRaw      = if ($env:CANNBOT_REPO_RAW) { $env:CANNBOT_REPO_RAW.TrimEnd('/') } else { 'https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main' }
$PluginUrl    = "$RepoRaw/cannbot-auth.js"
$ProPluginUrl = "$RepoRaw/cannbot-pro-auth.js"
$V2PluginUrl  = "$RepoRaw/cannbot-v2/index.js"

# OpenCode follows XDG-style paths; on Windows these live under the user profile.
$ConfigDir     = if ($env:XDG_CONFIG_HOME) { Join-Path $env:XDG_CONFIG_HOME 'opencode' } else { Join-Path $env:USERPROFILE '.config\opencode' }
$DataDir       = if ($env:XDG_DATA_HOME)   { Join-Path $env:XDG_DATA_HOME   'opencode' } else { Join-Path $env:USERPROFILE '.local\share\opencode' }
$PluginDir     = Join-Path $ConfigDir 'plugins'
$PluginFile    = Join-Path $PluginDir 'cannbot-auth.js'
$ProPluginFile = Join-Path $PluginDir 'cannbot-pro-auth.js'
$V2Dir         = Join-Path $PluginDir 'cannbot'
$V2File        = Join-Path $V2Dir 'index.js'
$OpencodeJson  = Join-Path $ConfigDir 'opencode.json'

function Write-Bold($t)   { Write-Host $t -ForegroundColor White }
function Write-Green($t)  { Write-Host $t -ForegroundColor Green }
function Write-Yellow($t) { Write-Host $t -ForegroundColor Yellow }
function Write-Red($t)    { Write-Host $t -ForegroundColor Red }

function Save-Remote($url, $outFile) {
  try {
    Invoke-WebRequest -Uri $url -OutFile $outFile -UseBasicParsing
  } catch {
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $curl) { throw }
    & $curl.Source -fsSL --connect-timeout 10 --max-time 30 $url -o $outFile 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $url" }
  }
}

# Rewrites opencode.json(c) through the plugin array. Returns $false when the
# file exists but cannot be parsed (comments), so the caller can print manual steps.
function Update-OpencodeJson($addUris, [switch]$RemoveLegacy) {
  if (-not (Test-Path $OpencodeJson)) {
    if ($addUris.Count -eq 0) { return $true }
    $cfg = [ordered]@{
      '$schema' = 'https://opencode.ai/config.json'
      plugin    = @($addUris)
    }
    $cfg | ConvertTo-Json -Depth 100 | Set-Content -Path $OpencodeJson -Encoding UTF8
    return $true
  }

  $cfg = $null
  try { $cfg = Get-Content $OpencodeJson -Raw | ConvertFrom-Json } catch { return $false }
  if ($null -eq $cfg) { return $false }

  $changed = $false
  foreach ($key in @('plugin', 'plugins')) {
    $prop = $cfg.PSObject.Properties[$key]
    if ($null -eq $prop -or $null -eq $prop.Value) { continue }
    $kept = @()
    foreach ($entry in @($prop.Value)) {
      if ($null -eq $entry) { continue }
      $text = if ($entry -is [string]) { $entry } elseif ($entry.package) { [string]$entry.package } else { '' }
      if ($RemoveLegacy -and $text -match 'cannbot(-pro)?-auth\.js$') {
        $changed = $true
        continue
      }
      $kept += $entry
    }
    foreach ($uri in $addUris) {
      if ($kept -notcontains $uri) { $kept += $uri; $changed = $true }
    }
    if ($kept.Count -eq 0) {
      if ($null -ne $cfg.PSObject.Properties[$key]) { $cfg.PSObject.Properties.Remove($key); $changed = $true }
    } else {
      $cfg.$key = $kept
    }
  }

  if ($changed) {
    $cfg | ConvertTo-Json -Depth 100 | Set-Content -Path $OpencodeJson -Encoding UTF8
  }
  return $true
}

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

# ── 0. Detect plugin API version ────────────────────────────────────────

$VersionText = @(& opencode --version 2>$null | Select-Object -First 1)[0]
$Major = 0
if ("$VersionText" -match '(\d+)\.') { $Major = [int]$Matches[1] }
if ($Major -eq 0) {
  Write-Yellow "Could not determine the opencode version (""$VersionText""); assuming 2.x."
  $Major = 2
}

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { $null }

if ($Major -ge 2) {
  Write-Bold "Detected opencode $VersionText (V2 plugin API)."

  New-Item -ItemType Directory -Force -Path $V2Dir | Out-Null
  $localV2 = if ($ScriptDir) { Join-Path $ScriptDir 'cannbot-v2\index.js' } else { $null }
  if ($localV2 -and (Test-Path $localV2)) {
    Copy-Item $localV2 $V2File -Force
    Write-Green "[1/3] CANNBOT V2 plugin copied from local clone -> $V2File"
  } else {
    Save-Remote $V2PluginUrl $V2File
    Write-Green "[1/3] CANNBOT V2 plugin downloaded -> $V2File"
  }

  $removed = $false
  foreach ($legacy in @($PluginFile, $ProPluginFile)) {
    if (Test-Path $legacy) {
      Remove-Item $legacy -Force
      Write-Green "[2/3] Removed legacy V1 plugin -> $legacy"
      $removed = $true
    }
  }
  if (-not $removed) { Write-Green "[2/3] No legacy V1 plugin files found" }

  if (Test-Path $OpencodeJson) {
    if (Update-OpencodeJson -addUris @() -RemoveLegacy) {
      Write-Green "[3/3] Removed legacy cannnbot entries from opencode.json"
    } else {
      Write-Yellow "[3/3] Could not rewrite $OpencodeJson (comments/JSONC). Remove cannnbot-auth.js entries manually."
    }
  } else {
    Write-Green "[3/3] No opencode.json changes needed (plugins\cannbot is auto-discovered)"
  }

  Write-Host ""
  Write-Bold "Restart opencode, then:"
  Write-Host ""
  Write-Host "  1) /connect  -> select CANNBOT or CANNBOT-Pro, enter your VK"
  Write-Host "  2) pick a CANNBOT model in the model list"
  Write-Host ""
  Write-Yellow "Models appear after the VK is connected."
  Write-Yellow "For scripted runs, set the model in opencode.json instead of --model:"
  Write-Host '  { "model": "cannbot/deepseek-v4-pro" }'
  Write-Host ""
  return
}

# ── Legacy (1.x) layout ─────────────────────────────────────────────────

Write-Bold "Detected opencode $VersionText (V1 plugin API)."
if (Test-Path $V2Dir) {
  Remove-Item $V2Dir -Recurse -Force
  Write-Green "[1/4] Removed V2 plugin -> $V2Dir"
} else {
  Write-Green "[1/4] V1 plugin layout selected"
}

$LocalPlugin = if ($ScriptDir) { Join-Path $ScriptDir 'cannbot-auth.js' } else { $null }
if ($LocalPlugin -and (Test-Path $LocalPlugin)) {
  Copy-Item $LocalPlugin $PluginFile -Force
  Write-Green "[2/4] CANNBOT plugin copied from local clone -> $PluginFile"
} else {
  Save-Remote $PluginUrl $PluginFile
  Write-Green "[2/4] CANNBOT plugin downloaded -> $PluginFile"
}

$LocalProPlugin = if ($ScriptDir) { Join-Path $ScriptDir 'cannbot-pro-auth.js' } else { $null }
if ($LocalProPlugin -and (Test-Path $LocalProPlugin)) {
  Copy-Item $LocalProPlugin $ProPluginFile -Force
  Write-Green "[3/4] CANNBOT-Pro plugin copied from local clone -> $ProPluginFile"
} else {
  Save-Remote $ProPluginUrl $ProPluginFile
  Write-Green "[3/4] CANNBOT-Pro plugin downloaded -> $ProPluginFile"
}

$PluginUri    = ([System.Uri]([System.IO.Path]::GetFullPath($PluginFile))).AbsoluteUri
$ProPluginUri = ([System.Uri]([System.IO.Path]::GetFullPath($ProPluginFile))).AbsoluteUri

if (Update-OpencodeJson @($PluginUri, $ProPluginUri)) {
  Write-Green "[4/4] opencode.json updated -> $OpencodeJson"
} else {
  Write-Yellow "[4/4] Could not parse $OpencodeJson (comments/JSONC). Add these to the plugin array:"
  Write-Host "        ""$PluginUri"","
  Write-Host "        ""$ProPluginUri"""
}

Write-Host ""
Write-Bold "Restart opencode, then run:"
Write-Host ""
Write-Host "  /connect"
Write-Host ""
Write-Host "Select 'CANNBOT' (standard) or 'CANNBOT-Pro' (Claude Opus / GPT) and enter your VK."
Write-Host ""

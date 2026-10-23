# BridgeBot one-line installer (Windows PowerShell).
# Usage:
#   irm https://bridgebot-ai.onrender.com/downloads/install.ps1 | iex
#   irm …/install.ps1 | iex -ApiKey bbk_YOUR_KEY
#   $env:BRIDGEBOT_API_KEY='bbk_…'; irm …/install.ps1 | iex
param(
  [string]$ApiKey = $env:BRIDGEBOT_API_KEY,
  [string]$Base = $(if ($env:BRIDGEBOT_BASE) { $env:BRIDGEBOT_BASE } else { "https://bridgebot-ai.onrender.com" }),
  [string]$Dir = $(if ($env:BRIDGEBOT_DIR) { $env:BRIDGEBOT_DIR } else { Join-Path $HOME "bridgebot" }),
  [string]$MachineName = $(if ($env:BRIDGEBOT_MACHINE_NAME) { $env:BRIDGEBOT_MACHINE_NAME } else { $env:COMPUTERNAME })
)

$ErrorActionPreference = "Stop"
function Say($m) { Write-Host "`n▸ $m" -ForegroundColor Green }
function Die($m) { Write-Host "`n✖ $m" -ForegroundColor Red; throw $m }

if (-not $ApiKey) {
  $ApiKey = Read-Host "BridgeBot connector key (bbk_… from Dashboard → Add machine)"
}
if (-not $ApiKey.StartsWith("bbk_")) { Die "Key should start with bbk_." }

# Prefer py launcher, then python
$py = $null
foreach ($c in @("py -3", "python", "python3")) {
  try {
    $v = & cmd /c "$c --version 2>&1"
    if ($LASTEXITCODE -eq 0 -and $v -match "Python 3\.(\d+)") {
      if ([int]$Matches[1] -ge 9) { $py = $c; break }
    }
  } catch {}
}
if (-not $py) { Die "Python 3.9+ is required. Install from https://python.org (tick Add to PATH), then re-run." }

Say "Installing into $Dir"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
Set-Location $Dir

Say "Downloading connector"
Invoke-WebRequest "$Base/downloads/local_connector.py" -OutFile "local_connector.py" -UseBasicParsing
Invoke-WebRequest "$Base/downloads/requirements.txt" -OutFile "requirements.txt" -UseBasicParsing

Say "Creating venv + deps"
& cmd /c "$py -m venv .venv"
$venvPy = Join-Path $Dir ".venv\Scripts\python.exe"
& $venvPy -m pip install -q --upgrade pip
& $venvPy -m pip install -q -r requirements.txt

$config = @"
{
  "server_url": "$Base",
  "api_key": "$ApiKey",
  "machine_name": "$MachineName",
  "remote_desktop": true,
  "skills_dir": "./skills",
  "agents": [{"name": "default", "type": "generic"}]
}
"@
Set-Content -Path "connector_config.json" -Value $config -Encoding UTF8
New-Item -ItemType Directory -Force -Path "skills" | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $HOME ".bridgebot") | Out-Null

Say "Creating Scheduled Task (runs at logon, hidden)"
$taskName = "BridgeBotConnector"
$action = New-ScheduledTaskAction -Execute $venvPy -Argument "`"$Dir\local_connector.py`" --config `"$Dir\connector_config.json`" --remote-desktop" -WorkingDirectory $Dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
try { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "BridgeBot AI local connector" -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

Say "Done. Machine should show ONLINE in the dashboard within ~15s."
Write-Host "  Dashboard:  $Base/dashboard/"
Write-Host "  Remote:     $Base/remote/"
Write-Host "  Kill switch: New-Item `$HOME\.bridgebot\remote_desktop_off -Force"
Write-Host ""
Write-Host "Remote Desktop tip: first Connect may ask Windows to allow the app; accept it." -ForegroundColor Yellow

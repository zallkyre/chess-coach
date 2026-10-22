<#
  Chess Coach — one-command installer

    irm https://raw.githubusercontent.com/zallkyre/chess-coach/main/install.ps1 | iex

  Downloads the app, drops it in %LOCALAPPDATA%\ChessCoach, makes a desktop
  shortcut, stores your Groq key in the secrets-mcp vault, and launches it.

  Flags (when run as a file): -NoLaunch -Port 8777 -Quiet
#>

[CmdletBinding()]
param(
    [switch]$NoLaunch,
    [int]$Port = 8777,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$Repo     = "zallkyre/chess-coach"
$Asset    = "ChessCoach.exe"
$InstallDir = Join-Path $env:LOCALAPPDATA "ChessCoach"
$ExePath  = Join-Path $InstallDir "ChessCoach.exe"
$Desktop  = [Environment]::GetFolderPath("Desktop")
$LnkPath  = Join-Path $Desktop "Chess Coach.lnk"

function Say($text, $color = "Gray") { if (-not $Quiet) { Write-Host $text -ForegroundColor $color } }

Write-Host ""
Write-Host "  CHESS COACH" -ForegroundColor Green
Write-Host "  chess with Ray — stockfish grading, coach chat" -ForegroundColor DarkGray
Write-Host ""

# ---------- 1. download ----------
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$url = "https://github.com/$Repo/releases/latest/download/$Asset"
Say "  downloading $Asset..." "Gray"

$tmp = Join-Path $env:TEMP ("ChessCoach-" + [guid]::NewGuid().ToString("N") + ".exe")
try {
    Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing -TimeoutSec 180
} catch {
    Write-Host "  download failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  check https://github.com/$Repo/releases" -ForegroundColor DarkGray
    return
}

$size = [math]::Round((Get-Item $tmp).Length / 1MB, 1)
Say "  got $size MB" "DarkGray"

# stop a running copy so the file isn't locked
Get-Process ChessCoach -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 300

Move-Item -Force -Path $tmp -Destination $ExePath
Unblock-File -Path $ExePath -ErrorAction SilentlyContinue
Say "  installed to $ExePath" "DarkGray"

# ---------- 2. shortcut ----------
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($LnkPath)
$lnk.TargetPath       = $ExePath
$lnk.WorkingDirectory = $InstallDir
$lnk.IconLocation     = "$ExePath,0"
$lnk.Description      = "Chess Coach — play, get graded, get heckled"
$lnk.Save()
Say "  desktop shortcut created" "DarkGray"

# ---------- 3. run it so we can talk to its local server ----------
Start-Process -FilePath $ExePath
$base = "http://127.0.0.1:$Port"

$health = $null
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 250
    try { $health = Invoke-RestMethod -Uri "$base/api/health" -TimeoutSec 2; break } catch {}
}

if (-not $health) {
    Write-Host "  app started but the local server did not answer." -ForegroundColor Yellow
    Write-Host "  open it from the desktop shortcut; you can paste a key in Settings." -ForegroundColor DarkGray
    return
}

# ---------- 4. api key ----------
if (-not $health.key) {
    Write-Host ""
    Say "  Paste a Groq key to wake Ray up (Enter to skip):" "Cyan"
    Say "  free one: https://console.groq.com/keys" "DarkGray"
    $secure = Read-Host -AsSecureString "  key"
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }

    if ($plain) {
        try {
            $body = @{ handle = "groq"; key = $plain } | ConvertTo-Json
            $r = Invoke-RestMethod -Uri "$base/api/import" -Method Post -Body $body `
                                   -ContentType "application/json" -TimeoutSec 15
            Write-Host "  key stored in the vault as $($r.hint)" -ForegroundColor Green
        } catch {
            Write-Host "  could not store the key: $($_.Exception.Message)" -ForegroundColor Yellow
        } finally {
            $plain = $null; $body = $null
        }
    } else {
        Say "  skipped — Ray still talks, just with canned lines." "DarkGray"
    }
} else {
    Say "  api key already in the vault" "DarkGray"
}

Write-Host ""
Write-Host "  done. Chess Coach is on your desktop." -ForegroundColor Green
Write-Host "  uninstall: delete $InstallDir and the shortcut." -ForegroundColor DarkGray
Write-Host ""

if ($NoLaunch) { Get-Process ChessCoach -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue }

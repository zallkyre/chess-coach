<#
  Chess Coach ā€” one-command installer

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
$Asset    = "ChessCoach.zip"
$InstallDir = Join-Path $env:LOCALAPPDATA "ChessCoach"
$ExePath  = Join-Path $InstallDir "ChessCoach.exe"
$Desktop  = [Environment]::GetFolderPath("Desktop")
$LnkPath  = Join-Path $Desktop "Chess Coach.lnk"

function Say($text, $color = "Gray") { if (-not $Quiet) { Write-Host $text -ForegroundColor $color } }

Write-Host ""
Write-Host "  CHESS COACH" -ForegroundColor Green
Write-Host "  chess with Ray ā€” stockfish grading, coach chat" -ForegroundColor DarkGray
Write-Host ""

# ---------- 1. download ----------
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$url = "https://github.com/$Repo/releases/latest/download/$Asset"
Say "  downloading $Asset..." "Gray"

$tmp = Join-Path $env:TEMP ("ChessCoach-" + [guid]::NewGuid().ToString("N") + ".zip")
try {
    Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing -TimeoutSec 600
} catch {
    Write-Host "  download failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  check https://github.com/$Repo/releases" -ForegroundColor DarkGray
    return
}

$size = [math]::Round((Get-Item $tmp).Length / 1MB, 1)
Say "  got $size MB" "DarkGray"

# stop a running copy so nothing is locked
Get-Process ChessCoach -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 400

Get-ChildItem -Path $InstallDir -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -Path $tmp -DestinationPath $InstallDir -Force
Remove-Item $tmp -Force -ErrorAction SilentlyContinue
Unblock-File -Path $ExePath -ErrorAction SilentlyContinue
if (-not (Test-Path $ExePath)) {
    Write-Host "  unpack failed - no ChessCoach.exe in the archive" -ForegroundColor Red
    return
}
Say "  installed to $InstallDir" "DarkGray"

# ---------- 2. shortcut ----------
$ws = New-Object -ComObject WScript.Shell
Set-Content -Path "$env:TEMP\wsdiag.txt" -Value ("null=" + ($null -eq $ws) + " type=" + ($ws.GetType().FullName))
$lnk = $ws.CreateShortcut($LnkPath)
$lnk.TargetPath       = $ExePath
$lnk.WorkingDirectory = $InstallDir
$lnk.IconLocation     = "$ExePath,0"
$lnk.Description      = "Chess Coach ā€” play, get graded, get heckled"
$lnk.Save()
Say "  desktop shortcut created" "DarkGray"

# ---------- 3. launch ----------
if (-not $NoLaunch) { Start-Process -FilePath $ExePath }

Write-Host ""
Write-Host "  done. Chess Coach is on your desktop." -ForegroundColor Green
Write-Host ""
Write-Host "  One thing left: give Ray a brain." -ForegroundColor Cyan
Write-Host "    open the app -> click 'key...' next to the chat box" -ForegroundColor DarkGray
Write-Host "    paste a Groq key (free: https://console.groq.com/keys)" -ForegroundColor DarkGray
Write-Host "    it goes into your secrets-mcp vault, never into a file" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  uninstall: delete $InstallDir and the shortcut." -ForegroundColor DarkGray
Write-Host ""





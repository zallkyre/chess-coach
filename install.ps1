<#
  Chess Coach - one-command installer

    irm https://raw.githubusercontent.com/zallkyre/chess-coach/main/install.ps1 | iex

  Downloads the app, puts it in %LOCALAPPDATA%\ChessCoach, makes a desktop
  shortcut, and launches it.

  Flags (when run as a file): -NoLaunch -Quiet
#>

[CmdletBinding()]
param(
    [switch]$NoLaunch,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$Repo       = "zallkyre/chess-coach"
$Asset      = "ChessCoach.zip"
$InstallDir = Join-Path $env:LOCALAPPDATA "ChessCoach"
$ExePath    = Join-Path $InstallDir "ChessCoach.exe"
$Desktop    = [Environment]::GetFolderPath("Desktop")
$LnkPath    = Join-Path $Desktop "Chess Coach.lnk"

function Say($text, $color = "Gray") {
    if (-not $Quiet) { Write-Host $text -ForegroundColor $color }
}

Write-Host ""
Write-Host "  CHESS COACH" -ForegroundColor Green
Write-Host "  native chess with a coach who is not a pro" -ForegroundColor DarkGray
Write-Host ""

# ---------- 1. find the newest release that actually carries the asset ----------
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$url = "https://github.com/$Repo/releases/latest/download/$Asset"
try {
    $rels = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases" `
                              -Headers @{ "User-Agent" = "chess-coach-installer" } -TimeoutSec 30
    $pick = $rels | Where-Object { -not $_.draft -and -not $_.prerelease } |
        Sort-Object { try { [version]($_.tag_name -replace '^v', '') } catch { [version]"0.0.0" } } -Descending |
        Select-Object -First 1
    if ($pick) {
        $hit = $pick.assets | Where-Object { $_.name -eq $Asset } | Select-Object -First 1
        if ($hit) { $url = $hit.browser_download_url }
    }
} catch {}

Say "  downloading $Asset ..." "Gray"

$tmp = Join-Path $env:TEMP ("ChessCoach-" + [guid]::NewGuid().ToString("N") + ".zip")
try {
    Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing -TimeoutSec 900
} catch {
    Write-Host "  download failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  check https://github.com/$Repo/releases" -ForegroundColor DarkGray
    return
}

$size = [math]::Round((Get-Item $tmp).Length / 1MB, 1)
Say "  got $size MB" "DarkGray"

Get-Process ChessCoach -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 400

Get-ChildItem -Path $InstallDir -Force -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Expand-Archive -Path $tmp -DestinationPath $InstallDir -Force
Remove-Item $tmp -Force -ErrorAction SilentlyContinue
Unblock-File -Path $ExePath -ErrorAction SilentlyContinue

if (-not (Test-Path $ExePath)) {
    Write-Host "  unpack failed - no ChessCoach.exe in the archive" -ForegroundColor Red
    return
}
Say "  installed to $InstallDir" "DarkGray"

# ---------- 2. shortcut ----------
try {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($LnkPath)
    $lnk.TargetPath       = $ExePath
    $lnk.WorkingDirectory = $InstallDir
    $lnk.IconLocation     = "$ExePath,0"
    $lnk.Description      = "Chess Coach - play, get graded, get heckled"
    $lnk.Save()
    Say "  desktop shortcut created" "DarkGray"
} catch {
    Write-Host "  could not create the shortcut: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "  run $ExePath directly" -ForegroundColor DarkGray
}

# ---------- 3. launch ----------
if (-not $NoLaunch) { Start-Process -FilePath $ExePath }

Write-Host ""
Write-Host "  done. Chess Coach is on your desktop." -ForegroundColor Green
Write-Host ""
Write-Host "  One thing left: give Ray a brain." -ForegroundColor Cyan
Write-Host "    open the app, click 'key...' next to the chat box" -ForegroundColor DarkGray
Write-Host "    paste a Groq key (free: https://console.groq.com/keys)" -ForegroundColor DarkGray
Write-Host "    it lands in your secrets-mcp vault, never in a file" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  uninstall: delete $InstallDir and the shortcut." -ForegroundColor DarkGray
Write-Host ""

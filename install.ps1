<#
  Chess Coach - one-command installer

    irm https://raw.githubusercontent.com/zallkyre/chess-coach/main/install.ps1 | iex

  Installs the app (~35 MB) to %LOCALAPPDATA%\ChessCoach, then fetches
  Stockfish straight from the official Stockfish release, makes a desktop
  shortcut, and launches.

  Flags (when run as a file): -NoLaunch -NoEngine -Quiet
#>

[CmdletBinding()]
param(
    [switch]$NoLaunch,
    [switch]$NoEngine,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$Repo       = "zallkyre/chess-coach"
$Asset      = "ChessCoach.exe"
$InstallDir = Join-Path $env:LOCALAPPDATA "ChessCoach"
$ExePath    = Join-Path $InstallDir "ChessCoach.exe"
$EngineDir  = Join-Path $InstallDir "engine"
$EngineExe  = Join-Path $EngineDir "stockfish.exe"
$Desktop    = [Environment]::GetFolderPath("Desktop")
$LnkPath    = Join-Path $Desktop "Chess Coach.lnk"
$UA         = @{ "User-Agent" = "chess-coach-installer" }

function Say($text, $color = "Gray") {
    if (-not $Quiet) { Write-Host $text -ForegroundColor $color }
}

function Test-StockfishUrl($url) {
    # HEAD first: the canonical "latest" URL avoids the API rate limit entirely
    try {
        $r = Invoke-WebRequest -Uri $url -Method Head -UseBasicParsing -TimeoutSec 25 `
                               -MaximumRedirection 5
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Get-ReleaseAssetUrl($repo, $assetName, $pattern) {
    # newest non-draft release that actually carries a matching asset
    try {
        $rels = Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases" `
                                  -Headers $UA -TimeoutSec 30
        $pick = $rels | Where-Object { -not $_.draft -and -not $_.prerelease } |
            Sort-Object { try { [version]($_.tag_name -replace '^v', '') } catch { [version]"0.0.0" } } -Descending |
            Select-Object -First 1
        if ($pick) {
            $hit = $pick.assets |
                Where-Object { $_.name -eq $assetName -or ($pattern -and $_.name -like $pattern) } |
                Select-Object -First 1
            if ($hit) { return $hit.browser_download_url }
        }
    } catch {}
    return $null
}

Write-Host ""
Write-Host "  CHESS COACH" -ForegroundColor Green
Write-Host "  native chess with a coach who is not a pro" -ForegroundColor DarkGray
Write-Host ""

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# ---------- 1. the app ----------
$url = Get-ReleaseAssetUrl $Repo $Asset $null
if (-not $url) { $url = "https://github.com/$Repo/releases/latest/download/$Asset" }

Say "  downloading $Asset ..." "Gray"
$tmp = Join-Path $env:TEMP ("ChessCoach-" + [guid]::NewGuid().ToString("N") + ".exe")
try {
    Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing -TimeoutSec 600
} catch {
    Write-Host "  download failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  check https://github.com/$Repo/releases" -ForegroundColor DarkGray
    return
}

Get-Process ChessCoach -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 400

Move-Item -Force -Path $tmp -Destination $ExePath
Unblock-File -Path $ExePath -ErrorAction SilentlyContinue
Say ("  app installed ({0} MB) -> {1}" -f [math]::Round((Get-Item $ExePath).Length / 1MB, 1), $InstallDir) "DarkGray"

# ---------- 2. stockfish, straight from upstream ----------
if (-not $NoEngine -and -not (Test-Path $EngineExe)) {
    Say "  fetching stockfish from official-stockfish/Stockfish ..." "Gray"
    Say "  (~78 MB, once - it is a full NNUE engine)" "DarkGray"
    $sfUrl = "https://github.com/official-stockfish/Stockfish/releases/latest/download/stockfish-windows-x86-64-universal.zip"
    if (-not (Test-StockfishUrl $sfUrl)) {
        $sfUrl = Get-ReleaseAssetUrl "official-stockfish/Stockfish" $null "*windows-x86-64*universal*.zip"
    }
    if (-not $sfUrl) {
        $sfUrl = Get-ReleaseAssetUrl "official-stockfish/Stockfish" $null "*windows-x86-64*.zip"
    }

    if (-not $sfUrl) {
        Write-Host "  could not find a stockfish windows build" -ForegroundColor Yellow
    } else {
        $sfZip = Join-Path $env:TEMP ("stockfish-" + [guid]::NewGuid().ToString("N") + ".zip")
        $sfOut = Join-Path $env:TEMP ("stockfish-" + [guid]::NewGuid().ToString("N"))
        try {
            Invoke-WebRequest -Uri $sfUrl -OutFile $sfZip -UseBasicParsing -TimeoutSec 900
            Expand-Archive -Path $sfZip -DestinationPath $sfOut -Force
            $bin = Get-ChildItem -Path $sfOut -Recurse -Filter "stockfish*.exe" |
                   Sort-Object Length -Descending | Select-Object -First 1
            if ($bin) {
                New-Item -ItemType Directory -Force -Path $EngineDir | Out-Null
                Copy-Item -Force $bin.FullName $EngineExe
                Unblock-File -Path $EngineExe -ErrorAction SilentlyContinue
                Say ("  engine installed ({0} MB)" -f [math]::Round((Get-Item $EngineExe).Length / 1MB, 1)) "DarkGray"
            } else {
                Write-Host "  the archive had no stockfish exe - skipping" -ForegroundColor Yellow
            }
        } catch {
            Write-Host "  engine download failed: $($_.Exception.Message)" -ForegroundColor Yellow
            Write-Host "  drop stockfish.exe in $EngineDir and restart the app" -ForegroundColor DarkGray
        } finally {
            Remove-Item $sfZip -Force -ErrorAction SilentlyContinue
            Remove-Item $sfOut -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
} elseif (Test-Path $EngineExe) {
    Say "  engine already present" "DarkGray"
}

# ---------- 3. shortcut ----------
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

# ---------- 4. launch ----------
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

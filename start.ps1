# Coach Mode
# proxy.py serves the app AND resolves the Groq key from the secrets-mcp vault,
# so the key never touches the browser. Stockfish needs a real http origin too.
$ErrorActionPreference = "Stop"
$port = if ($env:PORT) { $env:PORT } else { "8777" }
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "  COACH MODE" -ForegroundColor Green
Write-Host "  http://127.0.0.1:$port" -ForegroundColor Cyan
Write-Host "  ctrl+c to stop" -ForegroundColor DarkGray
Write-Host ""

Start-Process "http://127.0.0.1:$port/index.html"
$env:PORT = $port
python (Join-Path $here "proxy.py")

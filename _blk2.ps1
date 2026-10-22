$ExePath = "C:\Windows\notepad.exe"
$InstallDir = "C:\Windows"
$LnkPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "blockshortcut.lnk"
function Say($t, $c = "Gray") { Write-Host $t }
$ws = New-Object -ComObject WScript.Shell
if ($null -eq $ws) { "WS NULL" } else { "WS OK" }
$lnk = $ws.CreateShortcut($LnkPath)
$lnk.TargetPath       = $ExePath
$lnk.WorkingDirectory = $InstallDir
$lnk.IconLocation     = "$ExePath,0"
$lnk.Description      = "test"
$lnk.Save()
"SAVED: " + (Test-Path $LnkPath)
Remove-Item $LnkPath -Force

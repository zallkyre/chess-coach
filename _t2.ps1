$ErrorActionPreference = "Stop"
try {
  $ws = New-Object -ComObject WScript.Shell
  if ($null -eq $ws) { "WS NULL" } else { "WS OK" }
  $lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "t2.lnk"))
  if ($null -eq $lnk) { "LNK NULL" } else { "LNK OK" }
  $lnk.TargetPath = "C:\Windows\notepad.exe"
  $lnk.Save()
  "SAVED: " + (Test-Path (Join-Path ([Environment]::GetFolderPath("Desktop")) "t2.lnk"))
} catch { "ERR: " + $_.Exception.Message }

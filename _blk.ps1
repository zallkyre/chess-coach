$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($LnkPath)
$lnk.TargetPath       = $ExePath
$lnk.WorkingDirectory = $InstallDir
$lnk.IconLocation     = "$ExePath,0"
$lnk.Description      = "Chess Coach ā€” play, get graded, get heckled"
$lnk.Save()
Say "  desktop shortcut created" "DarkGray"


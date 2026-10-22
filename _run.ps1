$ErrorActionPreference = "Stop"
try { & .\install.ps1 -NoLaunch }
catch {
  "CAUGHT: " + $_.Exception.Message
  "LINE: " + $_.InvocationInfo.ScriptLineNumber
  "TEXT: " + $_.InvocationInfo.Line
  "TYPE: " + $_.Exception.GetType().FullName
}

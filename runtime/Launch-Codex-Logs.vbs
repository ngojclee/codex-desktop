' Launch-Codex-Logs.vbs
'
' Hidden wrapper that starts Codex through Launch-Codex.ps1 with
' -ShowSidecarWindow. Fresh launches show the app-server console; if Codex is
' already running on the shared sidecar, it opens a tail window for the current
' sidecar log and focuses the existing app window.

Option Explicit

Dim shell, fso, scriptDir, ps1
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = fso.BuildPath(scriptDir, "Launch-Codex.ps1")

If Not fso.FileExists(ps1) Then
    MsgBox "Launch-Codex.ps1 not found next to this .vbs:" & vbCrLf & ps1, _
           vbCritical, "Codex Log Launcher"
    WScript.Quit 1
End If

' 0 = hidden parent launcher, False = do not wait. Launch-Codex.ps1 opens the
' visible log window itself when -ShowSidecarWindow is present.
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """ -ShowSidecarWindow", 0, False

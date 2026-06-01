' Launch-Codex-Dev.vbs
'
' Hidden wrapper for the Dev build-flavor lane. It uses the same shared
' sidecar lifecycle as Launch-Codex.vbs, but starts Desktop with
' BUILD_FLAVOR=dev for feature-probing.

Option Explicit

Dim shell, fso, scriptDir, ps1
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = fso.BuildPath(scriptDir, "Launch-Codex.ps1")

If Not fso.FileExists(ps1) Then
    MsgBox "Launch-Codex.ps1 not found next to this .vbs:" & vbCrLf & ps1, _
           vbCritical, "Codex Dev Launcher"
    WScript.Quit 1
End If

' 0 = hidden window, False = do not wait
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """ -BuildFlavor dev", 0, False

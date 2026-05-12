' Launch-Codex.vbs
'
' Completely hidden launcher — point your Codex shortcut at this file to
' avoid the brief console flash you'd see from Launch-Codex.cmd.
'
' Runs Launch-Codex.ps1 sitting next to this .vbs.

Option Explicit

Dim shell, fso, scriptDir, ps1
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = fso.BuildPath(scriptDir, "Launch-Codex.ps1")

If Not fso.FileExists(ps1) Then
    MsgBox "Launch-Codex.ps1 not found next to this .vbs:" & vbCrLf & ps1, _
           vbCritical, "Codex Launcher"
    WScript.Quit 1
End If

' 0 = hidden window, False = do not wait
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """", 0, False

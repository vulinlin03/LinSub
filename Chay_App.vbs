Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
currentPath = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = currentPath

pythonPath = "C:\Python314\pythonw.exe"
If Not FSO.FileExists(pythonPath) Then
    pythonPath = "pythonw.exe"
End If

WshShell.Run """" & pythonPath & """ linsub_desktop.py", 0, False

Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
currentPath = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = currentPath

pythonPath = "C:\Python314\pythonw.exe"
If Not FSO.FileExists(pythonPath) Then
    pythonPath = "pythonw.exe"
End If

scriptPath = currentPath & "\linsub_desktop.py"
WshShell.Run """" & pythonPath & """ """ & scriptPath & """", 0, False

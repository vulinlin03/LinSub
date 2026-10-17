Set WshShell = CreateObject("WScript.Shell")
currentPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = currentPath
WshShell.Run "pythonw.exe linsub_desktop.py", 0, False

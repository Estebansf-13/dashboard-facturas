Dim pythonw, script, carpeta
carpeta = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
script = carpeta & "watcher.py"

Dim shell
Set shell = CreateObject("WScript.Shell")
pythonw = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python313\pythonw.exe"

If Not CreateObject("Scripting.FileSystemObject").FileExists(pythonw) Then
    pythonw = "pythonw.exe"
End If

shell.Run Chr(34) & pythonw & Chr(34) & " " & Chr(34) & script & Chr(34), 0, False

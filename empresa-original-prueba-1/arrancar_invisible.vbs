Dim pythonw, script, carpeta
carpeta = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
script = carpeta & "watcher.py"

' Buscar pythonw.exe en el PATH
Dim shell
Set shell = CreateObject("WScript.Shell")
pythonw = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python313\pythonw.exe"

' Si no existe, intentar con python del PATH
If Not CreateObject("Scripting.FileSystemObject").FileExists(pythonw) Then
    pythonw = "pythonw.exe"
End If

' Ejecutar watcher sin ventana visible (0 = oculto, False = no esperar)
shell.Run Chr(34) & pythonw & Chr(34) & " " & Chr(34) & script & Chr(34), 0, False

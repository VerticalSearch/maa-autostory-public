Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
Set output = fs.CreateTextFile(root & "\runtime\STOP", True)
output.WriteLine "User requested stop"
output.Close

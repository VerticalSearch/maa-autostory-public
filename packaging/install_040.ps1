$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$appExe = Join-Path $projectRoot 'App-0.4.0\MAA自动推图助手.exe'
if (-not (Test-Path -LiteralPath $appExe)) { throw 'Verified 0.4.0 package is missing' }
if ((Get-Item -LiteralPath $appExe).VersionInfo.ProductVersion -ne '0.4.0') { throw 'Unexpected package version' }
$tracked = @('runtime\progress.json','runtime\campaign-results.json','runtime\status.json','runtime\operators.json')
$before = @{}
foreach ($relative in $tracked) { $before[$relative] = (Get-FileHash -LiteralPath (Join-Path $projectRoot $relative)).Hash }
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:18741/api/health' -NoProxy -TimeoutSec 5
if ($health.application -ne 'MAA-AutoStory' -or $health.root -ne $projectRoot) { throw 'Unexpected service owner' }
$oldServer = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$health.pid)
$oldExe = Join-Path $projectRoot 'App\MAA自动推图助手.exe'
if ($health.executable -ne $appExe) {
    if ($health.executable -ne $oldExe -or $oldServer.ExecutablePath -ne $oldExe -or $oldServer.CommandLine -notmatch ' --server --root ') { throw 'Server identity changed; no process was stopped' }
    # Only the identified local HTTP daemon is replaced; game/UI/worker processes are untouched.
    Stop-Process -Id ([int]$health.pid) -ErrorAction Stop
    $launcher = Start-Process -FilePath $appExe -ArgumentList '--headless' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
    # Wait only for the launcher, not its intentionally persistent service child.
    if (-not $launcher.WaitForExit(45000) -or $launcher.ExitCode -ne 0) { throw 'New server failed to start' }
}
$newHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:18741/api/health' -NoProxy -TimeoutSec 5
if ($newHealth.executable -ne $appExe -or $newHealth.root -ne $projectRoot) { throw 'New server identity mismatch' }
$shortcutPath = 'D:\Desktop\MAA 自动推图助手.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $appExe
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = $appExe + ',0'
$shortcut.Description = 'MAA 自动推图助手 0.4.0'
$shortcut.WindowStyle = 1
$shortcut.Save()
$checked = $shell.CreateShortcut($shortcutPath)
$shortcutRecord = [ordered]@{Path=$shortcutPath;Target=$checked.TargetPath;WorkingDirectory=$checked.WorkingDirectory;Icon=$checked.IconLocation}
if ($checked.TargetPath -ne $appExe) { throw 'Shortcut verification failed' }
$shortcutRecord | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot 'runtime\desktop-shortcut.json') -Encoding utf8
$vbsPath = Join-Path $projectRoot '启动后台助手.vbs'
$vbs = [IO.File]::ReadAllText($vbsPath,[Text.Encoding]::Unicode)
[IO.File]::WriteAllText($vbsPath,$vbs.Replace('\App\','\App-0.4.0\'),[Text.Encoding]::Unicode)
$after = @{}
foreach ($relative in $tracked) {
    $after[$relative] = (Get-FileHash -LiteralPath (Join-Path $projectRoot $relative)).Hash
    if ($before[$relative] -ne $after[$relative]) { throw ('Account record changed: ' + $relative) }
}
[ordered]@{At=[DateTimeOffset]::Now.ToString('o');Health=$newHealth;Shortcut=$shortcutRecord;RecordsUnchanged=$true;Before=$before;After=$after} |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $projectRoot 'runtime\deployment-0.4.0.json') -Encoding utf8
$newHealth | ConvertTo-Json

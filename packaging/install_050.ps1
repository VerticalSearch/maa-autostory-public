$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$appExe = Join-Path $projectRoot 'App-0.5.0\MAA自动推图助手.exe'
if ((Get-Item -LiteralPath $appExe).VersionInfo.ProductVersion -ne '0.5.0') { throw 'Unexpected package version' }
$manifest = Get-Content -LiteralPath (Join-Path $projectRoot 'runtime\package-ui-050-manifest.json') -Raw | ConvertFrom-Json
if (-not $manifest.ok -or $manifest.exe_sha256 -ne (Get-FileHash -LiteralPath $appExe).Hash) { throw 'Package does not match verification manifest' }
$tracked = @('runtime\progress.json','runtime\campaign-results.json','runtime\status.json','runtime\operators.json','settings.json')
$before = @{}
foreach ($relative in $tracked) { $before[$relative] = (Get-FileHash -LiteralPath (Join-Path $projectRoot $relative)).Hash }
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:18741/api/health' -NoProxy -TimeoutSec 5
if ($health.application -ne 'MAA-AutoStory' -or $health.root -ne $projectRoot) { throw 'Unexpected service owner' }
# Only close the identified control window; never terminate the daemon/worker.
$oldExe = Join-Path $projectRoot 'App-0.4.0\MAA自动推图助手.exe'
$oldWindows = Get-Process | Where-Object { $_.MainWindowTitle -eq 'MAA 自动推图助手' -and $_.Path -eq $oldExe }
foreach ($window in $oldWindows) {
    $record = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $window.Id)
    if ($record.CommandLine -match '--server|--worker|--headless') { throw 'Unexpected window process role' }
    if (-not $window.CloseMainWindow()) { throw 'Could not close the previous control window' }
    if (-not $window.WaitForExit(5000)) { throw 'Previous control window did not exit; no process was killed' }
}
$shortcutPath = 'D:\Desktop\MAA 自动推图助手.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $appExe
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = $appExe + ',0'
$shortcut.Description = 'MAA 自动推图助手 0.5.0'
$shortcut.WindowStyle = 1
$shortcut.Save()
$checked = $shell.CreateShortcut($shortcutPath)
if ($checked.TargetPath -ne $appExe) { throw 'Shortcut verification failed' }
$vbsPath = Join-Path $projectRoot '启动后台助手.vbs'
$vbs = [IO.File]::ReadAllText($vbsPath,[Text.Encoding]::Unicode)
[IO.File]::WriteAllText($vbsPath,$vbs.Replace('\App-0.4.0\','\App-0.5.0\'),[Text.Encoding]::Unicode)
# This is the requested interactive UI, intentionally visible to the user.
$opened = Start-Process -FilePath $appExe -WorkingDirectory $projectRoot -PassThru
$after = @{}
foreach ($relative in $tracked) { $after[$relative] = (Get-FileHash -LiteralPath (Join-Path $projectRoot $relative)).Hash }
$record = [ordered]@{Target=$checked.TargetPath;Path=$shortcutPath;WorkingDirectory=$projectRoot;Icon=$checked.IconLocation}
$record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot 'runtime\desktop-shortcut.json') -Encoding utf8
[ordered]@{Version='0.5.0';At=[DateTimeOffset]::Now.ToString('o');ServicePidPreserved=$health.pid;UIProcess=$opened.Id;Shortcut=$record;Before=$before;After=$after} |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $projectRoot 'runtime\deployment-0.5.0.json') -Encoding utf8
$record | ConvertTo-Json

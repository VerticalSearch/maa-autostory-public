$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$appExe = Join-Path $projectRoot 'App-0.8.0\MAA自动推图助手.exe'
$manifest = Get-Content -LiteralPath (Join-Path $projectRoot 'runtime\package-unified080-manifest.json') -Raw | ConvertFrom-Json
if ((Get-Item -LiteralPath $appExe).VersionInfo.ProductVersion -ne '0.8.0' -or -not $manifest.ok -or $manifest.exe_sha256 -ne (Get-FileHash -LiteralPath $appExe).Hash) { throw 'Unverified release' }
$tracked = @('runtime\progress.json','runtime\campaign-results.json','runtime\status.json','runtime\operators.json','runtime\attempt-history.json','settings.json')
$before = @{}
foreach ($relative in $tracked) { $before[$relative] = (Get-FileHash -LiteralPath (Join-Path $projectRoot $relative)).Hash }
$state = Invoke-RestMethod -Uri 'http://127.0.0.1:18741/api/state' -NoProxy -TimeoutSec 5
if ($state.running) { throw 'Worker running; service upgrade deferred' }
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:18741/api/health' -NoProxy -TimeoutSec 5
if ($health.application -ne 'MAA-AutoStory' -or $health.root -ne $projectRoot) { throw 'Unexpected service owner' }
if ($health.executable -ne $appExe) {
    $oldServer = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$health.pid)
    $allowed = @((Join-Path $projectRoot 'App-0.7.0\MAA自动推图助手.exe'),(Join-Path $projectRoot 'App-0.6.0\MAA自动推图助手.exe'),(Join-Path $projectRoot 'App-0.4.0\MAA自动推图助手.exe'),(Join-Path $projectRoot 'App-0.5.0\MAA自动推图助手.exe'))
    if ($oldServer.ExecutablePath -ne $health.executable -or $oldServer.ExecutablePath -notin $allowed -or $oldServer.CommandLine -notmatch ' --server --root ') { throw 'Server identity changed; no process stopped' }
    Stop-Process -Id ([int]$health.pid)
    $launcher = Start-Process -FilePath $appExe -ArgumentList '--headless' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
    if (-not $launcher.WaitForExit(45000) -or $launcher.ExitCode -ne 0) { throw 'Service launcher failed' }
}
$newHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:18741/api/health' -NoProxy -TimeoutSec 5
if ($newHealth.executable -ne $appExe -or $newHealth.root -ne $projectRoot) { throw 'New service identity mismatch' }
$shortcutPath = 'D:\Desktop\MAA 自动推图助手.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $appExe
$shortcut.WorkingDirectory = $projectRoot
$customIcon = Join-Path $projectRoot 'assets\shu.ico'
$shortcut.IconLocation = if (Test-Path -LiteralPath $customIcon) { $customIcon + ',0' } else { $appExe + ',0' }
$shortcut.Description = 'MAA 自动推图助手 0.8.0'
$shortcut.WindowStyle = 1
$shortcut.Save()
$checked = $shell.CreateShortcut($shortcutPath)
if ($checked.TargetPath -ne $appExe) { throw 'Shortcut verification failed' }
$shortcutRecord = [ordered]@{Path=$shortcutPath;Target=$checked.TargetPath;WorkingDirectory=$checked.WorkingDirectory;Icon=$checked.IconLocation}
$shortcutRecord | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot 'runtime\desktop-shortcut.json') -Encoding utf8
$vbsPath = Join-Path $projectRoot '启动后台助手.vbs'
$vbs = [IO.File]::ReadAllText($vbsPath,[Text.Encoding]::Unicode)
$vbs = $vbs.Replace('\App-0.7.0\','\App-0.8.0\').Replace('\App-0.6.0\','\App-0.8.0\').Replace('\App\','\App-0.8.0\').Replace('\App-0.4.0\','\App-0.8.0\').Replace('\App-0.5.0\','\App-0.8.0\')
[IO.File]::WriteAllText($vbsPath,$vbs,[Text.Encoding]::Unicode)
$gpuScript = Join-Path $projectRoot '启动GPU版本.ps1'
$gpuText = [IO.File]::ReadAllText($gpuScript)
[IO.File]::WriteAllText($gpuScript,$gpuText.Replace('App-0.5.1','App-0.8.0'),[Text.UTF8Encoding]::new($false))
$gpuLinkPath = 'D:\Desktop\MAA GPU 试验版.lnk'
if (Test-Path -LiteralPath $gpuLinkPath) {
    $gpuLink=$shell.CreateShortcut($gpuLinkPath)
    $gpuLink.TargetPath=$appExe
    $gpuLink.Arguments=''
    $gpuLink.WorkingDirectory=$projectRoot
    $gpuLink.IconLocation=$shortcut.IconLocation
    $gpuLink.Description='MAA 自动推图助手 0.8.0（统一 GPU 与恢复逻辑）'
    $gpuLink.Save()
}
$after = @{}
foreach ($relative in $tracked) {
    $after[$relative] = (Get-FileHash -LiteralPath (Join-Path $projectRoot $relative)).Hash
    if ($before[$relative] -ne $after[$relative]) { throw ('Record changed during upgrade: ' + $relative) }
}
[ordered]@{At=[DateTimeOffset]::Now.ToString('o');Version='0.8.0';Health=$newHealth;Shortcut=$shortcutRecord;RecordsUnchanged=$true;Before=$before;After=$after} |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $projectRoot 'runtime\deployment-0.8.0.json') -Encoding utf8
$newHealth | ConvertTo-Json

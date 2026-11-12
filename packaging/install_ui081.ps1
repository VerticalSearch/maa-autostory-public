$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$appExe = Join-Path $projectRoot 'App-0.8.1\MAA自动推图助手.exe'
$manifest = Get-Content -LiteralPath (Join-Path $projectRoot 'runtime\package-ui-081-manifest.json') -Raw | ConvertFrom-Json
if ((Get-Item -LiteralPath $appExe).VersionInfo.ProductVersion -ne '0.8.1' -or -not $manifest.ok -or $manifest.exe_sha256 -ne (Get-FileHash -LiteralPath $appExe).Hash) { throw 'Unverified UI release' }
$tracked = @('runtime\progress.json','runtime\campaign-results.json','runtime\status.json','runtime\operators.json','runtime\attempt-history.json','settings.json','assets\shu.ico')
$before = @{}
foreach ($relative in $tracked) { $before[$relative] = (Get-FileHash -LiteralPath (Join-Path $projectRoot $relative)).Hash }
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:18741/api/health' -NoProxy -TimeoutSec 5
if ($health.application -ne 'MAA-AutoStory' -or $health.root -ne $projectRoot) { throw 'Unexpected service owner' }
# Only close identified interactive control windows; preserve daemon/worker.
$oldPaths = @('App','App-0.4.0','App-0.5.0','App-0.5.1','App-0.6.0','App-0.7.0','App-0.8.0') | ForEach-Object { Join-Path $projectRoot ($_ + '\MAA自动推图助手.exe') }
$oldWindows = Get-Process | Where-Object { $_.MainWindowTitle -eq 'MAA 自动推图助手' -and $_.Path -in $oldPaths }
foreach ($window in $oldWindows) {
    $processRecord = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $window.Id)
    if ($processRecord.CommandLine -match '--server|--worker|--headless') { throw 'Unexpected window process role' }
    if (-not $window.CloseMainWindow()) { throw 'Could not close previous UI' }
    if (-not $window.WaitForExit(5000)) { throw 'Previous UI did not exit; no process killed' }
}
$shortcutPath = 'D:\Desktop\MAA 自动推图助手.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
if ($shortcut.TargetPath -notin $oldPaths -and $shortcut.TargetPath -ne $appExe) { throw 'Desktop shortcut has an unexpected target' }
$shortcut.TargetPath = $appExe
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = (Join-Path $projectRoot 'assets\shu.ico') + ',0'
$shortcut.Description = 'MAA 自动推图助手 0.8.1'
$shortcut.WindowStyle = 1
$shortcut.Save()
$checked = $shell.CreateShortcut($shortcutPath)
if ($checked.TargetPath -ne $appExe) { throw 'Shortcut verification failed' }
$shortcutRecord = [ordered]@{Path=$shortcutPath;Target=$checked.TargetPath;WorkingDirectory=$checked.WorkingDirectory;Icon=$checked.IconLocation}
$shortcutRecord | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot 'runtime\desktop-shortcut.json') -Encoding utf8
$vbsPath = Join-Path $projectRoot '启动后台助手.vbs'
$vbs = [IO.File]::ReadAllText($vbsPath,[Text.Encoding]::Unicode)
[IO.File]::WriteAllText($vbsPath,$vbs.Replace('\App-0.8.0\','\App-0.8.1\'),[Text.Encoding]::Unicode)
$gpuScript = Join-Path $projectRoot '启动GPU版本.ps1'
if (Test-Path -LiteralPath $gpuScript) {
    $gpuText = [IO.File]::ReadAllText($gpuScript)
    [IO.File]::WriteAllText($gpuScript,$gpuText.Replace('App-0.8.0','App-0.8.1'),[Text.UTF8Encoding]::new($false))
}
$gpuLinkPath = 'D:\Desktop\MAA GPU 试验版.lnk'
if (Test-Path -LiteralPath $gpuLinkPath) {
    $gpuLink = $shell.CreateShortcut($gpuLinkPath)
    if ($gpuLink.TargetPath -eq (Join-Path $projectRoot 'App-0.8.0\MAA自动推图助手.exe')) {
        $gpuLink.TargetPath = $appExe
        $gpuLink.Description = 'MAA 自动推图助手 0.8.1（统一 GPU 与恢复逻辑）'
        $gpuLink.Save()
    }
}
# Requested interactive UI is intentionally visible. Background service is reused.
$opened = Start-Process -FilePath $appExe -WorkingDirectory $projectRoot -PassThru
$after = @{}
foreach ($relative in $tracked) {
    $after[$relative] = (Get-FileHash -LiteralPath (Join-Path $projectRoot $relative)).Hash
    if ($before[$relative] -ne $after[$relative]) { throw ('Record changed during UI deployment: ' + $relative) }
}
$newHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:18741/api/health' -NoProxy -TimeoutSec 5
if ($newHealth.pid -ne $health.pid -or $newHealth.executable -ne $health.executable) { throw 'Service identity changed during UI deployment' }
[ordered]@{At=[DateTimeOffset]::Now.ToString('o');Version='0.8.1';ServicePidPreserved=$health.pid;UIProcess=$opened.Id;Shortcut=$shortcutRecord;RecordsUnchanged=$true;Before=$before;After=$after} |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $projectRoot 'runtime\deployment-ui-0.8.1.json') -Encoding utf8
$shortcutRecord | ConvertTo-Json

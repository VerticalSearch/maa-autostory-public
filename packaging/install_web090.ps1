$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$appExe = Join-Path $projectRoot 'App-0.9.0\MAA自动推图助手.exe'
$manifest = Get-Content -LiteralPath (Join-Path $projectRoot 'runtime\package-web-090-manifest.json') -Raw | ConvertFrom-Json
if (-not $manifest.ok -or (Get-Item -LiteralPath $appExe).VersionInfo.ProductVersion -ne '0.9.0' -or (Get-FileHash -LiteralPath $appExe).Hash -ne $manifest.exe_sha256) { throw 'Unverified package' }
if ($manifest.web_source_sha256 -ne (Get-FileHash -LiteralPath (Join-Path $projectRoot 'web\index.html')).Hash) { throw 'Web source differs from verified release' }
foreach($file in $manifest.host_files.PSObject.Properties){
    if($file.Value -ne (Get-FileHash -LiteralPath (Join-Path $projectRoot ('App-0.9.0\webview\'+$file.Name))).Hash){throw 'WebView host differs from build manifest'}
}
$browserAudit=Get-Content -LiteralPath (Join-Path $projectRoot 'runtime\web090-browser-audit.json') -Raw | ConvertFrom-Json
$nativeAudit=Get-Content -LiteralPath (Join-Path $projectRoot 'runtime\webhost-final.json') -Raw | ConvertFrom-Json
if(-not $browserAudit.ok -or -not $nativeAudit.ok -or -not $nativeAudit.dashboardLoaded){throw 'UI acceptance incomplete'}
$settings = Get-Content -LiteralPath (Join-Path $projectRoot 'settings.json') -Raw | ConvertFrom-Json
$url = 'http://127.0.0.1:' + [int]$settings.port
$health = Invoke-RestMethod -Uri ($url+'/api/health') -NoProxy -TimeoutSec 5
$state = Invoke-RestMethod -Uri ($url+'/api/state') -NoProxy -TimeoutSec 5
if ($state.running) { throw 'A game task is running; no deployment performed' }
if ($health.application -ne 'MAA-AutoStory' -or $health.root -ne $projectRoot) { throw 'Unexpected service' }
$oldServer = Get-CimInstance Win32_Process -Filter ('ProcessId = '+[int]$health.pid)
if ($oldServer.ExecutablePath -ne $health.executable -or (Split-Path -Parent (Split-Path -Parent $oldServer.ExecutablePath)) -ne $projectRoot -or $oldServer.CommandLine -notmatch ' --server --root ') { throw 'Service ownership mismatch' }
$shortcutPath = 'D:\Desktop\MAA 自动推图助手.lnk'
$gpuShortcutPath = 'D:\Desktop\MAA GPU 试验版.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
if ((Split-Path -Parent (Split-Path -Parent $shortcut.TargetPath)) -ne $projectRoot) { throw 'Unexpected desktop shortcut owner' }
$backupRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot ('runtime\backups\web090-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))))
if (-not $backupRoot.StartsWith($projectRoot+'\runtime\backups\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe backup root' }
New-Item -ItemType Directory -Path $backupRoot | Out-Null
$lockPath = Join-Path $projectRoot 'runtime\alltasks.lock'
$lock = [IO.File]::Open($lockPath,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
$held = $false
try {
    if ($lock.Length -eq 0) { $lock.WriteByte(0); $lock.Flush() }
    $lock.Lock(0,1); $held = $true
    $tracked = @('runtime\progress.json','runtime\campaign-results.json','runtime\operators.json','runtime\attempt-history.json','runtime\status.json','settings.json','assets\shu.ico')
    $before = @{}
    foreach ($relative in $tracked) { $before[$relative]=(Get-FileHash -LiteralPath (Join-Path $projectRoot $relative)).Hash }
    foreach ($name in @('启动后台助手.vbs','启动GPU版本.ps1','启动GPU版本.vbs','启动助手.ps1','active-release.json')) {
        $path=Join-Path $projectRoot $name
        if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination (Join-Path $backupRoot $name) }
    }
    Copy-Item -LiteralPath $shortcutPath -Destination (Join-Path $backupRoot 'MAA 自动推图助手.lnk')
    # Close only this tool's old control windows; the task lock excludes workers.
    $oldWindows = Get-Process | Where-Object { $_.MainWindowTitle -eq 'MAA 自动推图助手' -and $_.Path -and $_.Path.StartsWith($projectRoot+'\App',[StringComparison]::OrdinalIgnoreCase) -and $_.Path -ne $appExe }
    foreach ($window in $oldWindows) {
        $record=Get-CimInstance Win32_Process -Filter ('ProcessId = '+$window.Id)
        if ($record.CommandLine -match '--server|--worker|--headless') { throw 'Unexpected window role' }
        if (-not $window.CloseMainWindow() -or -not $window.WaitForExit(5000)) { throw 'Previous control window did not close' }
    }
    if ($health.executable -ne $appExe) {
        Stop-Process -Id ([int]$health.pid)
        $started=Start-Process -FilePath $appExe -ArgumentList '--headless' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
        if (-not $started.WaitForExit(45000) -or $started.ExitCode -ne 0) { throw 'Unified service failed to start' }
    }
    $afterHealth=Invoke-RestMethod -Uri ($url+'/api/health') -NoProxy -TimeoutSec 5
    if ($afterHealth.version -ne '0.9.0' -or $afterHealth.executable -ne $appExe -or $afterHealth.root -ne $projectRoot) { throw 'Unified service verification failed' }
    [ordered]@{version='0.9.0';directory='App-0.9.0';sha256=$manifest.exe_sha256} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot 'active-release.json') -Encoding utf8
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'launch_unified.ps1') -Destination (Join-Path $projectRoot '启动助手.ps1') -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'legacy_gpu_redirect.ps1') -Destination (Join-Path $projectRoot '启动GPU版本.ps1') -Force
    $pwsh=(Get-Command pwsh.exe).Source
    $vbs='Set sh = CreateObject("WScript.Shell")'+[Environment]::NewLine+'rc = sh.Run("""'+$pwsh+'"" -NoProfile -File ""'+(Join-Path $projectRoot '启动助手.ps1')+'"" -Headless", 0, True)'+[Environment]::NewLine+'If rc <> 0 Then MsgBox "MAA helper could not start. Check the launcher log.", 48, "MAA"'+[Environment]::NewLine
    [IO.File]::WriteAllText((Join-Path $projectRoot '启动后台助手.vbs'),$vbs,[Text.Encoding]::Unicode)
    $shortcut.TargetPath=$appExe; $shortcut.Arguments=''; $shortcut.WorkingDirectory=$projectRoot
    $shortcut.IconLocation=(Join-Path $projectRoot 'assets\shu.ico')+',0'; $shortcut.Description='MAA 自动推图助手 0.9.0'; $shortcut.Save()
    if (Test-Path -LiteralPath $gpuShortcutPath) {
        $gpuShortcut=$shell.CreateShortcut($gpuShortcutPath)
        if ($gpuShortcut.TargetPath -notlike ($projectRoot+'\App*\MAA自动推图助手.exe') -and $gpuShortcut.Arguments -notlike ('*'+$projectRoot+'\启动GPU版本.vbs*')) { throw 'Unexpected GPU shortcut owner' }
        $resolvedShortcut=(Resolve-Path -LiteralPath $gpuShortcutPath).Path
        $archivedShortcut=[IO.Path]::GetFullPath((Join-Path $backupRoot 'MAA GPU 试验版.lnk'))
        if ($resolvedShortcut -ne 'D:\Desktop\MAA GPU 试验版.lnk' -or -not $archivedShortcut.StartsWith($backupRoot+'\')) { throw 'Unsafe shortcut archive path' }
        Move-Item -LiteralPath $resolvedShortcut -Destination $archivedShortcut
    }
    $checked=$shell.CreateShortcut($shortcutPath)
    if ($checked.TargetPath -ne $appExe) { throw 'Desktop shortcut verification failed' }
    $shortcutRecord=[ordered]@{Path=$shortcutPath;Target=$checked.TargetPath;WorkingDirectory=$checked.WorkingDirectory;Icon=$checked.IconLocation}
    $shortcutRecord | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot 'runtime\desktop-shortcut.json') -Encoding utf8
    $after=@{}
    foreach ($relative in $tracked) {
        $after[$relative]=(Get-FileHash -LiteralPath (Join-Path $projectRoot $relative)).Hash
        if ($after[$relative] -ne $before[$relative]) { throw ('Account data changed: '+$relative) }
    }
    [ordered]@{At=[DateTimeOffset]::Now.ToString('o');Version='0.9.0';Health=$afterHealth;Shortcut=$shortcutRecord;DuplicateShortcutArchived=(-not (Test-Path -LiteralPath $gpuShortcutPath));Backup=$backupRoot;RecordsUnchanged=$true;Before=$before;After=$after} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $projectRoot 'runtime\deployment-web-0.9.0.json') -Encoding utf8
    $afterHealth | ConvertTo-Json
} finally {
    if ($held) { $lock.Unlock(0,1) }; $lock.Dispose()
}
# Restore a control window only if one was open before the version replacement.
$opened = Start-Process -FilePath $appExe -WorkingDirectory $projectRoot -PassThru
$hostExe=Join-Path $projectRoot 'App-0.9.0\webview\AutoStory.WebHost.exe'
$hostProcess=$null
for($attempt=0;$attempt -lt 40;$attempt++){
    $candidate=Get-CimInstance Win32_Process -Filter ('ParentProcessId = '+$opened.Id) | Where-Object {$_.ExecutablePath -eq $hostExe} | Select-Object -First 1
    if($candidate){
        $window=Get-Process -Id $candidate.ProcessId
        if($window.MainWindowHandle -ne 0 -and $window.Responding){$hostProcess=$window;break}
    }
    if($opened.HasExited){throw 'Desktop launcher exited before showing UI'}
    Start-Sleep -Milliseconds 200
}
if(-not $hostProcess){throw 'No responding WebView window detected'}
$deployPath=Join-Path $projectRoot 'runtime\deployment-web-0.9.0.json'
$deployment=Get-Content -LiteralPath $deployPath -Raw | ConvertFrom-Json
$deployment | Add-Member -NotePropertyName LauncherProcess -NotePropertyValue $opened.Id
$deployment | Add-Member -NotePropertyName UIProcess -NotePropertyValue $hostProcess.Id
$deployment | Add-Member -NotePropertyName UIWindow -NotePropertyValue $hostProcess.MainWindowHandle.ToInt64()
$deployment | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $deployPath -Encoding utf8

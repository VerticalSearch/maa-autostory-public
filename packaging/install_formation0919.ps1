$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$appExe = Join-Path $projectRoot 'App-0.9.19\MAA自动推图助手.exe'
$manifest = Get-Content -LiteralPath (Join-Path $projectRoot 'runtime\package-formation0919-manifest.json') -Raw | ConvertFrom-Json
if (-not $manifest.ok -or (Get-Item -LiteralPath $appExe).VersionInfo.ProductVersion -ne '0.9.19' -or (Get-FileHash -LiteralPath $appExe).Hash -ne $manifest.exe_sha256) { throw 'Unverified package' }
if ((Get-FileHash -LiteralPath (Join-Path $projectRoot 'web\index.html')).Hash -ne $manifest.ui_sha256) { throw 'Unverified control page' }
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
$originalIcon=$shortcut.IconLocation
if ((Split-Path -Parent (Split-Path -Parent $shortcut.TargetPath)) -ne $projectRoot) { throw 'Unexpected desktop shortcut owner' }
$backupRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot ('runtime\backups\formation0919-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))))
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
    # Keep the existing control window open; only replace the idle service.
    $oldWindows=@()
    if ($health.executable -ne $appExe) {
        Stop-Process -Id ([int]$health.pid)
        $started=Start-Process -FilePath $appExe -ArgumentList '--headless' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
        if (-not $started.WaitForExit(45000) -or $started.ExitCode -ne 0) { throw 'Unified service failed to start' }
    }
    $afterHealth=Invoke-RestMethod -Uri ($url+'/api/health') -NoProxy -TimeoutSec 5
    if ($afterHealth.version -ne '0.9.19' -or $afterHealth.executable -ne $appExe -or $afterHealth.root -ne $projectRoot) { throw 'Unified service verification failed' }
    [ordered]@{version='0.9.19';directory='App-0.9.19';sha256=$manifest.exe_sha256} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot 'active-release.json') -Encoding utf8
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'launch_unified.ps1') -Destination (Join-Path $projectRoot '启动助手.ps1') -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'legacy_gpu_redirect.ps1') -Destination (Join-Path $projectRoot '启动GPU版本.ps1') -Force
    $pwsh=(Get-Command pwsh.exe).Source
    $vbs='Set sh = CreateObject("WScript.Shell")'+[Environment]::NewLine+'rc = sh.Run("""'+$pwsh+'"" -NoProfile -File ""'+(Join-Path $projectRoot '启动助手.ps1')+'"" -Headless", 0, True)'+[Environment]::NewLine+'If rc <> 0 Then MsgBox "MAA helper could not start. Check the launcher log.", 48, "MAA"'+[Environment]::NewLine
    [IO.File]::WriteAllText((Join-Path $projectRoot '启动后台助手.vbs'),$vbs,[Text.Encoding]::Unicode)
    $shortcut.TargetPath=$appExe; $shortcut.Arguments=''; $shortcut.WorkingDirectory=$projectRoot
    $shortcut.IconLocation=$originalIcon; $shortcut.Description='MAA 自动推图助手 0.9.19'; $shortcut.Save()
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
    [ordered]@{At=[DateTimeOffset]::Now.ToString('o');Version='0.9.19';Health=$afterHealth;Shortcut=$shortcutRecord;DuplicateShortcutArchived=(-not (Test-Path -LiteralPath $gpuShortcutPath));Backup=$backupRoot;RecordsUnchanged=$true;Before=$before;After=$after} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $projectRoot 'runtime\deployment-formation0919.json') -Encoding utf8
    $afterHealth | ConvertTo-Json
} finally {
    if ($held) { $lock.Unlock(0,1) }; $lock.Dispose()
}

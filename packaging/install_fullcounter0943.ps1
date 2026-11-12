param([Parameter(Mandatory=$true)][string]$ProjectPath, [switch]$SkipPrune = $true)
$ErrorActionPreference = 'Stop'
$version = '0.9.43'
$directory = 'App-' + $version
$keepVersioned = 3
$candidateRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$projectRoot = [IO.Path]::GetFullPath($ProjectPath)
if ($projectRoot -eq $candidateRoot) { throw 'Install target must be the named live project, not the validation fixture' }
$appExe = Join-Path $projectRoot ($directory + '\MAA自动推图助手.exe')
$candidateExe = Join-Path $candidateRoot ($directory + '\MAA自动推图助手.exe')
$manifest = Get-Content -LiteralPath (Join-Path $candidateRoot 'runtime\package-fullcounter0943-manifest.json') -Raw | ConvertFrom-Json
$frozen = Get-Content -LiteralPath (Join-Path $candidateRoot 'runtime\fullcounter0943-frozen-check.json') -Raw | ConvertFrom-Json
if (-not $manifest.ok -or $manifest.version -ne $version -or (Get-Item -LiteralPath $candidateExe).VersionInfo.ProductVersion -ne $version -or (Get-FileHash -LiteralPath $candidateExe).Hash -ne $manifest.exe_sha256) { throw 'Unverified package' }
if (-not $frozen.ok -or $frozen.version -ne $version -or @($frozen.live_records_touched).Count -ne 0) { throw 'Frozen regression check missing or failed' }
if ((Get-FileHash -LiteralPath (Join-Path $projectRoot 'web\index.html')).Hash -ne $manifest.ui_sha256) { throw 'Unverified control page' }
foreach ($p in $manifest.source_sha256.PSObject.Properties) {
    
    if ((Get-FileHash -LiteralPath (Join-Path $candidateRoot $p.Name)).Hash -ne $p.Value) { throw ('Source changed after build: '+$p.Name) }
}
foreach ($p in $manifest.asset_sha256.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath (Join-Path $projectRoot $p.Name)).Hash -ne $p.Value) { throw ('Asset changed after build: '+$p.Name) }
}
$sourceCheck = Get-Content -LiteralPath (Join-Path $candidateRoot 'runtime\fullcounter0943-source-check.json') -Raw | ConvertFrom-Json
$bundleCheck = Get-Content -LiteralPath (Join-Path $candidateRoot 'runtime\package-integration.json') -Raw | ConvertFrom-Json
if (-not $sourceCheck.ok -or $sourceCheck.version -ne $version -or -not $bundleCheck.ok -or $bundleCheck.health.version -ne $version) { throw 'Source or native bundle validation missing' }
$sourceBaseline = Get-Content -LiteralPath (Join-Path $candidateRoot 'runtime\fullcounter0943-baseline-source.json') -Raw | ConvertFrom-Json
foreach ($p in $sourceBaseline.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath (Join-Path $projectRoot $p.Name)).Hash -ne $p.Value) { throw ('Live source/test changed since candidate creation: '+$p.Name) }
}
$settings = Get-Content -LiteralPath (Join-Path $projectRoot 'settings.json') -Raw | ConvertFrom-Json
$url = 'http://127.0.0.1:' + [int]$settings.port
$health = $null
$probe = New-Object System.Net.Sockets.TcpClient
try { $listening = $probe.ConnectAsync('127.0.0.1',[int]$settings.port).Wait(3000) -and $probe.Connected } catch { $listening = $false } finally { $probe.Dispose() }
if ($listening) { $health = Invoke-RestMethod -Uri ($url+'/api/health') -NoProxy -TimeoutSec 5 }
if ($health) {
    $state = Invoke-RestMethod -Uri ($url+'/api/state') -NoProxy -TimeoutSec 5
    if ($state.running) { throw 'A game task is running; no deployment performed' }
    if ($health.application -ne 'MAA-AutoStory' -or $health.root -ne $projectRoot) { throw 'Unexpected service' }
    $oldServer = Get-CimInstance Win32_Process -Filter ('ProcessId = '+[int]$health.pid)
    if ($oldServer.ExecutablePath -ne $health.executable -or (Split-Path -Parent (Split-Path -Parent $oldServer.ExecutablePath)) -ne $projectRoot -or $oldServer.CommandLine -notmatch ' --server --root ') { throw 'Service ownership mismatch' }
} else {
    # Cold start: no service on the port. Refuse if any helper process of this project still exists.
    $own = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($projectRoot+'\App',[StringComparison]::OrdinalIgnoreCase) })
    if ($own.Count) { throw ('Helper processes still running without a service: '+(($own | ForEach-Object ProcessId) -join ',')) }
}
$shortcutPath = 'D:\Desktop\MAA 自动推图助手.lnk'
$gpuShortcutPath = 'D:\Desktop\MAA GPU 试验版.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$originalIcon=$shortcut.IconLocation
if ((Split-Path -Parent (Split-Path -Parent $shortcut.TargetPath)) -ne $projectRoot) { throw 'Unexpected desktop shortcut owner' }
$backupRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot ('runtime\backups\fullcounter0943-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))))
if (-not $backupRoot.StartsWith($projectRoot+'\runtime\backups\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe backup root' }
New-Item -ItemType Directory -Path $backupRoot | Out-Null
$lockPath = Join-Path $projectRoot 'runtime\alltasks.lock'
$lock = [IO.File]::Open($lockPath,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
$held = $false
try {
    if ($lock.Length -eq 0) { $lock.WriteByte(0); $lock.Flush() }
    $lock.Lock(0,1); $held = $true
    $tracked = @('runtime\progress.json','runtime\campaign-results.json','runtime\operators.json','runtime\attempt-history.json','runtime\scan-report.json','runtime\nav-hints.json','settings.json','assets\shu.ico')
    $before = @{}
    foreach ($relative in $tracked) { $before[$relative]=(Get-FileHash -LiteralPath (Join-Path $projectRoot $relative)).Hash }
    $statusPath = Join-Path $projectRoot 'runtime\status.json'
    $statusBefore = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
    Copy-Item -LiteralPath $statusPath -Destination (Join-Path $backupRoot 'status.json')
    foreach ($name in @('启动后台助手.vbs','启动GPU版本.ps1','启动GPU版本.vbs','启动助手.ps1','active-release.json')) {
        $path=Join-Path $projectRoot $name
        if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination (Join-Path $backupRoot $name) }
    }
    Copy-Item -LiteralPath $shortcutPath -Destination (Join-Path $backupRoot 'MAA 自动推图助手.lnk')
    if ($health) {
        $still = Invoke-RestMethod -Uri ($url+'/api/state') -NoProxy -TimeoutSec 5
        if ($still.running) { throw 'Worker became active; staging refused' }
    }
    # Only the reviewed executable, source modules, tests and validation reports.
    # Account records, configuration and screenshot fixtures are never staged.
    if (Test-Path -LiteralPath (Join-Path $projectRoot $directory)) { throw 'Target bundle already exists; inspect before replacing' }
    Copy-Item -LiteralPath (Join-Path $candidateRoot $directory) -Destination (Join-Path $projectRoot $directory) -Recurse
    foreach ($file in Get-ChildItem -LiteralPath (Join-Path $candidateRoot $directory) -File -Recurse) {
        $relative = [IO.Path]::GetRelativePath($candidateRoot,$file.FullName)
        if ((Get-FileHash -LiteralPath $file.FullName).Hash -ne (Get-FileHash -LiteralPath (Join-Path $projectRoot $relative)).Hash) { throw ('Bundle staging hash mismatch: '+$relative) }
    }
    $stagePaths = @($manifest.source_sha256.PSObject.Properties.Name) + @(
        'tests\test_activity_counter0943.py','tests\test_activity_completion0943.py',
        'tests\test_collection_progress0943.py','tests\test_collection_completion0943.py','tests\test_jobs_review.py',
        'evidence\collection-progress-ocr.json',
        'packaging\build_fullcounter0943.py','packaging\verify_fullcounter0943.py','packaging\install_fullcounter0943.ps1',
        'runtime\package-fullcounter0943-manifest.json','runtime\fullcounter0943-source-check.json',
        'runtime\fullcounter0943-frozen-check.json','runtime\fullcounter0943-bundle-check.json')
    foreach ($relative in $stagePaths) {
        $target = [IO.Path]::GetFullPath((Join-Path $projectRoot $relative))
        if (-not $target.StartsWith($projectRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe staging path' }
        if (Test-Path -LiteralPath $target) {
            $backup = Join-Path $backupRoot ('source\'+$relative)
            New-Item -ItemType Directory -Path (Split-Path -Parent $backup) -Force | Out-Null
            Copy-Item -LiteralPath $target -Destination $backup
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $candidateRoot $relative) -Destination $target -Force
    }
    if (-not $health -or $health.executable -ne $appExe) {
        if ($health) { Stop-Process -Id ([int]$health.pid); Start-Sleep -Milliseconds 800 }
        $started=Start-Process -FilePath $appExe -ArgumentList '--headless' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
        if (-not $started.WaitForExit(45000) -or $started.ExitCode -ne 0) { throw 'Unified service failed to start' }
    }
    $afterHealth=Invoke-RestMethod -Uri ($url+'/api/health') -NoProxy -TimeoutSec 5
    if ($afterHealth.version -ne $version -or $afterHealth.executable -ne $appExe -or $afterHealth.root -ne $projectRoot) { throw 'Unified service verification failed' }
    [ordered]@{version=$version;directory=$directory;sha256=$manifest.exe_sha256} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot 'active-release.json') -Encoding utf8
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'launch_unified.ps1') -Destination (Join-Path $projectRoot '启动助手.ps1') -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'legacy_gpu_redirect.ps1') -Destination (Join-Path $projectRoot '启动GPU版本.ps1') -Force
    $pwsh=(Get-Command pwsh.exe).Source
    $vbs='Set sh = CreateObject("WScript.Shell")'+[Environment]::NewLine+'rc = sh.Run("""'+$pwsh+'"" -NoProfile -File ""'+(Join-Path $projectRoot '启动助手.ps1')+'"" -Headless", 0, True)'+[Environment]::NewLine+'If rc <> 0 Then MsgBox "MAA helper could not start. Check the launcher log.", 48, "MAA"'+[Environment]::NewLine
    [IO.File]::WriteAllText((Join-Path $projectRoot '启动后台助手.vbs'),$vbs,[Text.Encoding]::Unicode)
    $shortcut.TargetPath=$appExe; $shortcut.Arguments=''; $shortcut.WorkingDirectory=$projectRoot
    $shortcut.IconLocation=$originalIcon; $shortcut.Description=('MAA 自动推图助手 '+$version); $shortcut.Save()
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
    # status.json may change only by the 0.9.28 stale-run repair: same run, running -> interrupted.
    $statusAfter = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
    $statusRepair = $false
    if (($statusAfter | ConvertTo-Json -Compress) -ne ($statusBefore | ConvertTo-Json -Compress)) {
        if ($statusAfter.run_id -ne $statusBefore.run_id -or $statusBefore.state -ne 'running' -or $statusAfter.state -ne 'interrupted') { throw 'Status changed unexpectedly' }
        foreach ($p in $statusBefore.PSObject.Properties) { if ($p.Name -ne 'state' -and ($statusAfter.($p.Name) | ConvertTo-Json -Compress) -ne ($p.Value | ConvertTo-Json -Compress)) { throw ('Status field changed: '+$p.Name) } }
        $statusRepair = $true
    }
    $prune = [ordered]@{Skipped=[bool]$SkipPrune;Kept=@();Removed=@();Refused=@();FreedMB=0}
    if (-not $SkipPrune) {
        # Keep the newest versioned bundles (by numeric version) plus the active one; never the plain App folder.
        $versioned = @(Get-ChildItem -LiteralPath $projectRoot -Directory -Force | Where-Object { $_.Name -match '^App-\d+\.\d+\.\d+$' } |
            Sort-Object { [version]$_.Name.Substring(4) } -Descending)
        $keep = @(@($versioned | Select-Object -First $keepVersioned | ForEach-Object Name) + $directory | Select-Object -Unique)
        $prune.Kept = $keep
        $live = @(Get-CimInstance Win32_Process | Where-Object ExecutablePath | ForEach-Object ExecutablePath)
        foreach ($dir in $versioned) {
            if ($keep -contains $dir.Name) { continue }
            $full = [IO.Path]::GetFullPath($dir.FullName)
            $reason = $null
            if ((Split-Path -Parent $full) -ne $projectRoot -or (Split-Path -Leaf $full) -notmatch '^App-\d+\.\d+\.\d+$' -or $full -eq (Join-Path $projectRoot 'App')) { $reason = 'path check' }
            elseif ($dir.Attributes -band [IO.FileAttributes]::ReparsePoint) { $reason = 'reparse point' }
            elseif (-not (Test-Path -LiteralPath (Join-Path $full 'MAA自动推图助手.exe'))) { $reason = 'unexpected layout' }
            elseif ($live | Where-Object { $_.StartsWith($full+'\',[StringComparison]::OrdinalIgnoreCase) }) { $reason = 'in use' }
            elseif (@(Get-ChildItem -LiteralPath $full -Recurse -Force -Attributes ReparsePoint -ErrorAction SilentlyContinue).Count) { $reason = 'contains reparse point' }
            if ($reason) { $prune.Refused += [ordered]@{Name=$dir.Name;Reason=$reason}; continue }
            $mb = [math]::Round(((Get-ChildItem -LiteralPath $full -Recurse -File -Force | Measure-Object Length -Sum).Sum)/1MB,1)
            try {
                Remove-Item -LiteralPath $full -Recurse -Force
                $prune.Removed += [ordered]@{Name=$dir.Name;MB=$mb}; $prune.FreedMB += $mb
            } catch {
                $prune.Refused += [ordered]@{Name=$dir.Name;Reason=('remove failed: '+$_.Exception.Message)}
            }
        }
        $prune.FreedMB = [math]::Round($prune.FreedMB,1)
    }
    [ordered]@{At=[DateTimeOffset]::Now.ToString('o');Version=$version;ColdStart=(-not $health);Health=$afterHealth;Shortcut=$shortcutRecord;DuplicateShortcutArchived=(-not (Test-Path -LiteralPath $gpuShortcutPath));Backup=$backupRoot;RecordsUnchanged=$true;StaleStatusRepaired=$statusRepair;Before=$before;After=$after;Prune=$prune} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $projectRoot 'runtime\deployment-fullcounter0943.json') -Encoding utf8
    [ordered]@{Health=$afterHealth;StaleStatusRepaired=$statusRepair;Removed=$prune.Removed.Count;Refused=$prune.Refused;FreedMB=$prune.FreedMB;Kept=$prune.Kept} | ConvertTo-Json -Depth 4
} finally {
    if ($held) { $lock.Unlock(0,1) }; $lock.Dispose()
}

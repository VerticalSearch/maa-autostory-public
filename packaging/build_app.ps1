param([ValidatePattern('^App(?:-\d+\.\d+\.\d+)?$')][string]$AppName = 'App')
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$appTarget = [IO.Path]::GetFullPath((Join-Path $projectRoot $AppName))
$buildTarget = [IO.Path]::GetFullPath((Join-Path $projectRoot '_build'))
if ($appTarget -ne (Join-Path $projectRoot $AppName) -or $buildTarget -ne (Join-Path $projectRoot '_build')) {
    throw 'Unexpected build output path'
}
$active = Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith($appTarget + '\', [StringComparison]::OrdinalIgnoreCase)
}
if ($active) { throw 'A packaged helper process is running; stop only that helper before rebuilding.' }
$nativeArgs = @('-m','PyInstaller','--noconfirm','--distpath',$projectRoot,'--workpath',$buildTarget,(Join-Path $PSScriptRoot 'AutoStory.spec'))
$oldAppName = $env:AUTOSTORY_APP_NAME
try {
    $env:AUTOSTORY_APP_NAME = $AppName
    & python @nativeArgs 2>&1 | Tee-Object -FilePath (Join-Path $projectRoot 'runtime\package-build.log')
    $buildExit = $LASTEXITCODE
} finally { $env:AUTOSTORY_APP_NAME = $oldAppName }
if ($buildExit -ne 0) { throw "PyInstaller failed: $buildExit" }
$appExe = Join-Path $appTarget 'MAA自动推图助手.exe'
if (-not (Test-Path -LiteralPath $appExe)) { throw 'Executable was not produced' }
[pscustomobject]@{
    BuiltAt = [DateTimeOffset]::Now.ToString('o')
    Executable = $appExe
    Sha256 = (Get-FileHash -LiteralPath $appExe -Algorithm SHA256).Hash
    Bytes = (Get-ChildItem -LiteralPath $appTarget -Recurse -File | Measure-Object Length -Sum).Sum
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot 'runtime\package-manifest.json') -Encoding utf8

param([switch]$Headless,[switch]$VerifyOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$release = Get-Content -LiteralPath (Join-Path $projectRoot 'active-release.json') -Raw | ConvertFrom-Json
if ($release.version -notmatch '^\d+\.\d+\.\d+$' -or $release.directory -ne ('App-' + $release.version)) { throw 'Invalid active release' }
$appExe = Join-Path $projectRoot ($release.directory + '\MAA自动推图助手.exe')
if ((Get-Item -LiteralPath $appExe).VersionInfo.ProductVersion -ne $release.version -or (Get-FileHash -LiteralPath $appExe).Hash -ne $release.sha256) { throw 'Active release verification failed' }
if ($VerifyOnly) { [ordered]@{Version=$release.version;Executable=$appExe;Headless=[bool]$Headless} | ConvertTo-Json; return }
if ($Headless) {
    $process = Start-Process -FilePath $appExe -ArgumentList '--headless' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(45000) -or $process.ExitCode -ne 0) { throw 'Background launcher failed' }
} else {
    # This branch is invoked by the user's interactive launcher.
    Start-Process -FilePath $appExe -WorkingDirectory $projectRoot | Out-Null
}

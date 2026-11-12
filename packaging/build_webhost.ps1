$ErrorActionPreference='Stop'
$projectRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$destination=Join-Path $projectRoot '_tmp\webhost-090'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
# Reuse Microsoft's signed, installed SDK components when NuGet is unreachable.
$sdkRoot='C:\Program Files\Microsoft OfficePLUS\3.16.0.46159\addin'
$sources=@(
    (Join-Path $sdkRoot 'Microsoft.Web.WebView2.Core.dll'),
    (Join-Path $sdkRoot 'Microsoft.Web.WebView2.WinForms.dll'),
    (Join-Path $sdkRoot 'runtimes\win-x64\native\WebView2Loader.dll')
)
$provenance=@()
foreach($source in $sources){
    $signature=Get-AuthenticodeSignature -FilePath $source
    if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Microsoft Corporation'){throw 'SDK signature validation failed'}
    $target=Join-Path $destination ([IO.Path]::GetFileName($source))
    Copy-Item -LiteralPath $source -Destination $target
    $provenance += [ordered]@{Source=$source;File=[IO.Path]::GetFileName($source);Version=(Get-Item $source).VersionInfo.FileVersion;Signature='Valid';SHA256=(Get-FileHash -LiteralPath $target).Hash}
}
$compiler='C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$argsForCompiler=@('/nologo','/target:winexe','/platform:x64','/optimize+',('/out:'+(Join-Path $destination 'AutoStory.WebHost.exe')),('/win32icon:'+(Join-Path $projectRoot 'assets\shu.ico')),
    '/reference:System.dll','/reference:System.Core.dll','/reference:System.Drawing.dll','/reference:System.Windows.Forms.dll',
    ('/reference:'+(Join-Path $destination 'Microsoft.Web.WebView2.Core.dll')),('/reference:'+(Join-Path $destination 'Microsoft.Web.WebView2.WinForms.dll')),
    (Join-Path $PSScriptRoot 'WebHost.cs'))
& $compiler @argsForCompiler
if($LASTEXITCODE -ne 0){throw 'WebView host compilation failed'}
$provenance | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $destination 'SDK-PROVENANCE.json') -Encoding utf8
Get-FileHash -LiteralPath (Join-Path $destination 'AutoStory.WebHost.exe')

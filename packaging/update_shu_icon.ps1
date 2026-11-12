param([Parameter(Mandatory=$true)][string]$Source,[ValidatePattern('^[a-z0-9-]+$')][string]$AssetName='shu-desktop-v2')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
$projectRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$assetRoot=Join-Path $projectRoot 'assets'
$shortcutPath='D:\Desktop\MAA 自动推图助手.lnk'
$shell=New-Object -ComObject WScript.Shell
$shortcut=$shell.CreateShortcut($shortcutPath)
$targetBefore=$shortcut.TargetPath
$argumentsBefore=$shortcut.Arguments
$workingBefore=$shortcut.WorkingDirectory
$release=Get-Content -LiteralPath (Join-Path $projectRoot 'active-release.json') -Raw | ConvertFrom-Json
if ($targetBefore -ne (Join-Path $projectRoot ($release.directory+'\MAA自动推图助手.exe'))) { throw 'Unexpected shortcut target' }
$exeHash=(Get-FileHash -LiteralPath $targetBefore).Hash
$backupRoot=Join-Path $projectRoot ('runtime\backups\shu-icon-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backupRoot | Out-Null
foreach($name in @('shu.ico','shu.png')) { Copy-Item -LiteralPath (Join-Path $assetRoot $name) -Destination (Join-Path $backupRoot $name) }
Copy-Item -LiteralPath $shortcutPath -Destination (Join-Path $backupRoot 'MAA 自动推图助手.lnk')
$webPath=Join-Path $projectRoot 'web\index.html'
$manifestPath=Join-Path $projectRoot 'runtime\package-navigation092-manifest.json'
Copy-Item -LiteralPath $webPath -Destination (Join-Path $backupRoot 'index.html')
Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $backupRoot 'package-navigation092-manifest.json')
Copy-Item -LiteralPath $Source -Destination (Join-Path $assetRoot ($AssetName+'.png'))
$sourceImage=[Drawing.Image]::FromFile((Join-Path $assetRoot ($AssetName+'.png')))
try {
    if($sourceImage.Width -lt 1024 -or $sourceImage.Width -ne $sourceImage.Height){throw 'Expected high-resolution square source'}
    # Encode the imagegen output in Windows ICO sizes; no redraw or creative edits.
    $frames=@()
    foreach($size in @(16,20,24,32,40,48,64,96,128,256)) {
        $bitmap=[Drawing.Bitmap]::new($size,$size,[Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $graphics=[Drawing.Graphics]::FromImage($bitmap)
        $stream=[IO.MemoryStream]::new()
        try {
            $graphics.Clear([Drawing.Color]::Transparent)
            $graphics.CompositingMode=[Drawing.Drawing2D.CompositingMode]::SourceCopy
            $graphics.CompositingQuality=[Drawing.Drawing2D.CompositingQuality]::HighQuality
            $graphics.InterpolationMode=[Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.PixelOffsetMode=[Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $graphics.DrawImage($sourceImage,[Drawing.Rectangle]::new(0,0,$size,$size))
            $bitmap.Save($stream,[Drawing.Imaging.ImageFormat]::Png)
            $bytes=$stream.ToArray()
            $frames+=@{Size=$size;Bytes=$bytes}
            if($size -eq 128){[IO.File]::WriteAllBytes((Join-Path $assetRoot ($AssetName+'-128.png')),$bytes)}
            if($size -eq 256){[IO.File]::WriteAllBytes((Join-Path $assetRoot ($AssetName+'-preview.png')),$bytes)}
        } finally { $graphics.Dispose();$bitmap.Dispose();$stream.Dispose() }
    }
    $icoPath=Join-Path $assetRoot ($AssetName+'.ico')
    $icoStream=[IO.File]::Create($icoPath)
    $writer=[IO.BinaryWriter]::new($icoStream)
    try {
        $writer.Write([uint16]0);$writer.Write([uint16]1);$writer.Write([uint16]$frames.Count)
        $offset=6+16*$frames.Count
        foreach($frame in $frames) {
            $side=if($frame.Size -eq 256){0}else{$frame.Size}
            $writer.Write([byte]$side);$writer.Write([byte]$side);$writer.Write([byte]0);$writer.Write([byte]0)
            $writer.Write([uint16]1);$writer.Write([uint16]32)
            $writer.Write([uint32]$frame.Bytes.Length);$writer.Write([uint32]$offset)
            $offset+=$frame.Bytes.Length
        }
        foreach($frame in $frames){$writer.Write([byte[]]$frame.Bytes)}
    } finally { $writer.Dispose();$icoStream.Dispose() }
    foreach($size in @(16,32,48,128)) {
        $decoded=[Drawing.Icon]::new($icoPath,$size,$size)
        try {if($decoded.Width -ne $size -or $decoded.Height -ne $size){throw 'Invalid ICO frame'}}finally{$decoded.Dispose()}
    }
    # System.Drawing.Icon's selector treats a 256px directory width (encoded as
    # zero in ICO) differently across runtimes. Validate every PNG entry itself.
    $encoded=[IO.File]::ReadAllBytes($icoPath)
    if([BitConverter]::ToUInt16($encoded,4) -ne $frames.Count){throw 'Invalid icon directory'}
    for($index=0;$index -lt $frames.Count;$index++) {
        $entry=6+16*$index
        $length=[BitConverter]::ToUInt32($encoded,$entry+8)
        $position=[BitConverter]::ToUInt32($encoded,$entry+12)
        $png=[IO.MemoryStream]::new($encoded,[int]$position,[int]$length)
        $decodedImage=[Drawing.Image]::FromStream($png)
        try { if($decodedImage.Width -ne $frames[$index].Size -or $decodedImage.Height -ne $frames[$index].Size){throw 'Invalid PNG frame'} }
        finally { $decodedImage.Dispose();$png.Dispose() }
    }
    Copy-Item -LiteralPath $icoPath -Destination (Join-Path $assetRoot 'shu.ico') -Force
    [IO.File]::WriteAllBytes((Join-Path $assetRoot 'shu.png'),[byte[]]($frames|Where-Object{$_.Size -eq 48}).Bytes)
    $html=[IO.File]::ReadAllText($webPath)
    $pattern='(?<=<div class="portrait"><img src="data:image/png;base64,)[A-Za-z0-9+/=]+(?=" alt="Shu")'
    $matches=[regex]::Matches($html,$pattern)
    if($matches.Count -ne 1){throw 'Unexpected header portrait structure'}
    $thumbnail=[IO.File]::ReadAllBytes((Join-Path $assetRoot ($AssetName+'-128.png')))
    $base64=[Convert]::ToBase64String($thumbnail)
    $newHtml=[regex]::Replace($html,$pattern,$base64)
    if([regex]::Replace($newHtml,$pattern,$matches[0].Value) -cne $html){throw 'Unexpected UI change'}
    [IO.File]::WriteAllText($webPath,$newHtml,[Text.UTF8Encoding]::new($false))
    $manifest=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $oldUiHash=$manifest.ui_sha256
    $manifest.ui_sha256=(Get-FileHash -LiteralPath $webPath).Hash.ToLowerInvariant()
    $manifest | Add-Member -NotePropertyName ui_asset_revision -NotePropertyValue $AssetName -Force
    $manifest | Add-Member -NotePropertyName ui_before_asset_revision -NotePropertyValue $oldUiHash -Force
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding utf8
    Copy-Item -LiteralPath $webPath -Destination (Join-Path $manifest.snapshot 'index.html') -Force
    # A new icon filename avoids the previous cached bitmap; retain the one entry.
    $shortcut.IconLocation=$icoPath+',0';$shortcut.Save()
    $verified=$shell.CreateShortcut($shortcutPath)
    if($verified.TargetPath -ne $targetBefore -or $verified.Arguments -ne $argumentsBefore -or $verified.WorkingDirectory -ne $workingBefore -or $verified.IconLocation -ne ($icoPath+',0')){throw 'Shortcut verification failed'}
    if((Get-FileHash -LiteralPath $targetBefore).Hash -ne $exeHash){throw 'Program binary changed'}
    $report=[ordered]@{At=[DateTimeOffset]::Now.ToString('o');Source=(Join-Path $assetRoot ($AssetName+'.png'));SourceSize=@($sourceImage.Width,$sourceImage.Height);Icon=$icoPath;Sizes=@($frames|ForEach-Object{$_.Size});Shortcut=$shortcutPath;Target=$verified.TargetPath;ExecutableUnchanged=$true;UIChange='Portrait data only; other HTML byte-equivalent';Backup=$backupRoot;IconSHA256=(Get-FileHash -LiteralPath $icoPath).Hash;UISHA256=$manifest.ui_sha256}
    $report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $projectRoot ('runtime\'+$AssetName+'-install.json')) -Encoding utf8
    Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class ShuIconNotify { [DllImport("shell32.dll", CharSet=CharSet.Unicode)] public static extern void SHChangeNotify(int eventId, uint flags, string path, IntPtr item2); }'
    [ShuIconNotify]::SHChangeNotify(0x2000,0x1005,$shortcutPath,[IntPtr]::Zero)
    $report | ConvertTo-Json -Depth 5
} finally { $sourceImage.Dispose() }

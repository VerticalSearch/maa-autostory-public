param([Parameter(Mandatory)][long]$WindowHandle, [Parameter(Mandatory)][string]$Path)
$ErrorActionPreference = 'Stop'
# The bundled capture helper is DPI-unaware on this 175% desktop. Give its
# calling thread physical window coordinates before it reads GetWindowRect.
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class CaptureDpi {
    [DllImport("user32.dll")]
    public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
}
'@
$previous = [CaptureDpi]::SetThreadDpiAwarenessContext([IntPtr](-4))
try {
    & (Join-Path $env:USERPROFILE '.codex\skills\screenshot\scripts\take_screenshot.ps1') -Path $Path -WindowHandle $WindowHandle
} finally { [void][CaptureDpi]::SetThreadDpiAwarenessContext($previous) }

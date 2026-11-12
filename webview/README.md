# WebView2 host

The C# host source is [`../packaging/WebHost.cs`](../packaging/WebHost.cs). It
is compiled by [`../packaging/build_webhost.ps1`](../packaging/build_webhost.ps1)
with the .NET Framework `csc.exe`; there is no `.csproj` in the source tree.

The prebuilt `AutoStory.WebHost.exe`, `Microsoft.Web.WebView2.*.dll` and
`WebView2Loader.dll` from the working install are intentionally not part of
this source snapshot. `SDK-PROVENANCE.json` records the SDK versions and
SHA-256 values they had locally, for reference only.

The build script expects Microsoft's signed WebView2 assemblies and writes its
output to `_tmp/webhost-090`, which is ignored by Git.

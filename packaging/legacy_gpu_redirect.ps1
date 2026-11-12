param([switch]$Headless,[switch]$VerifyOnly)
# Compatibility alias: GPU is now part of the single active release.
& (Join-Path $PSScriptRoot '启动助手.ps1') -Headless:$Headless -VerifyOnly:$VerifyOnly

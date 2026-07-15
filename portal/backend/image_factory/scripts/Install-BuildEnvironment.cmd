# escape=`
# Shared Install script skeleton for Image Factory Windows hosts.
# Offline Layout / installers are supplied via RO mount paths from Catalog.

@echo off
setlocal EnableExtensions

set "LAYOUT_ROOT=%IMAGE_FACTORY_LAYOUT_ROOT%"
set "INSTALLER_ROOT=%IMAGE_FACTORY_INSTALLER_ROOT%"
set "MANIFEST=C:\ImageBuild\install-manifest.json"

if not exist "%MANIFEST%" (
  echo install-manifest.json missing
  exit /b 1
)

powershell.exe -NoProfile -NonInteractive -File C:\ImageBuild\scripts\Install-FromManifest.ps1 -ManifestPath "%MANIFEST%"
if errorlevel 1 exit /b 1

echo Install-BuildEnvironment completed
exit /b 0

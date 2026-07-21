@echo off
REM Shared Install script for Image Factory Windows hosts.
REM Offline Layout / installers are supplied via IMAGE_FACTORY_* env paths.

setlocal EnableExtensions

set "LAYOUT_ROOT=%IMAGE_FACTORY_LAYOUT_ROOT%"
set "INSTALLER_ROOT=%IMAGE_FACTORY_INSTALLER_ROOT%"
set "MANIFEST=C:\ImageBuild\install-manifest.json"

echo Install-BuildEnvironment starting
echo SCRIPT_REV=certutil-crl-20260721
echo LAYOUT_ROOT=%LAYOUT_ROOT%
echo INSTALLER_ROOT=%INSTALLER_ROOT%

if not exist "%MANIFEST%" (
  echo install-manifest.json missing
  exit /b 1
)
if not exist "%LAYOUT_ROOT%" (
  echo LAYOUT_ROOT missing: %LAYOUT_ROOT%
  exit /b 1
)
dir /b "%LAYOUT_ROOT%" 2>nul | findstr /i "vs_setup vs_BuildTools" >nul
if errorlevel 1 (
  echo WARNING: vs_setup.exe / vs_BuildTools.exe not obvious in LAYOUT_ROOT
  dir /b "%LAYOUT_ROOT%"
)

powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\ImageBuild\scripts\Install-FromManifest.ps1 -ManifestPath "%MANIFEST%"
if errorlevel 1 (
  echo Install-FromManifest.ps1 failed with errorlevel %ERRORLEVEL%
  exit /b 1
)

echo Install-BuildEnvironment completed
exit /b 0

param(
  [Parameter(Mandatory = $true)][string]$ManifestPath
)

$ErrorActionPreference = "Stop"
$manifest = Get-Content -Raw -Path $ManifestPath | ConvertFrom-Json

$layoutRoot = $env:IMAGE_FACTORY_LAYOUT_ROOT
$installerRoot = $env:IMAGE_FACTORY_INSTALLER_ROOT
if (-not $layoutRoot) { throw "IMAGE_FACTORY_LAYOUT_ROOT is not set" }
if (-not $installerRoot) { throw "IMAGE_FACTORY_INSTALLER_ROOT is not set" }

$release = [string]$manifest.visualStudio.layoutRelease
Write-Host "Installing VS Build Tools from offline layout:" $release
Write-Host "Components:" ($manifest.visualStudio.components -join ", ")

# Allow base images that already contain Build Tools (verify / incremental).
if ($env:FACTORY_SKIP_VS_INSTALL -eq "1") {
  Write-Host "FACTORY_SKIP_VS_INSTALL=1 — skipping vs_setup.exe"
} else {
  $setupCandidates = @(
    (Join-Path $layoutRoot "vs_setup.exe"),
    (Join-Path $layoutRoot "vs_BuildTools.exe"),
    (Join-Path $layoutRoot $release "vs_setup.exe"),
    (Join-Path $layoutRoot $release "vs_BuildTools.exe")
  )
  $setup = $setupCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
  if (-not $setup) {
    throw @"
vs_setup.exe / vs_BuildTools.exe not found under IMAGE_FACTORY_LAYOUT_ROOT=$layoutRoot
Expected one of:
  $layoutRoot\vs_setup.exe
  $layoutRoot\$release\vs_setup.exe
"@
  }

  $config = "C:\ImageBuild\profile.vsconfig"
  if (-not (Test-Path $config)) { throw "Missing $config" }

  Write-Host "Running" $setup
  $proc = Start-Process -FilePath $setup -ArgumentList @(
    "--quiet",
    "--norestart",
    "--wait",
    "--noUpdateInstaller",
    "--noWeb",
    "--config", $config
  ) -Wait -PassThru
  if ($proc.ExitCode -ne 0) {
    throw "VS Build Tools install failed with exit code $($proc.ExitCode)"
  }
  Write-Host "VS Build Tools install completed"
}

foreach ($sdk in @($manifest.dotnetSdks)) {
  $ver = [string]$sdk.version
  Write-Host "Looking for .NET SDK installer version" $ver
  $pattern = Join-Path $installerRoot ("*" + $ver + "*.exe")
  $sdkSetup = Get-ChildItem -Path $installerRoot -Filter "*.exe" -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match [regex]::Escape($ver) } |
    Select-Object -First 1
  if ($sdkSetup) {
    Write-Host "Installing" $sdkSetup.FullName
    $p = Start-Process -FilePath $sdkSetup.FullName -ArgumentList "/install","/quiet","/norestart" -Wait -PassThru
    if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010) {
      throw ".NET SDK install failed: $($p.ExitCode)"
    }
  } else {
    Write-Host "No matching .NET SDK installer under" $installerRoot "(skipped)"
  }
}

foreach ($tp in @($manifest.dotnetFrameworkTargetingPacks)) {
  Write-Host "Targeting Pack requested:" $tp.version "(expect present in VS layout / installer root)"
}
foreach ($ws in @($manifest.windowsSdks)) {
  Write-Host "Windows SDK requested:" $ws.version "(expect present in VS layout components)"
}

Write-Host "Install-FromManifest finished"

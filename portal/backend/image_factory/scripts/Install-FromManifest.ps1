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
  Write-Host "FACTORY_SKIP_VS_INSTALL=1 - skipping vs_setup.exe"
} else {
  $setupCandidates = @(
    (Join-Path $layoutRoot "vs_setup.exe"),
    (Join-Path $layoutRoot "vs_BuildTools.exe"),
    (Join-Path $layoutRoot (Join-Path $release "vs_setup.exe")),
    (Join-Path $layoutRoot (Join-Path $release "vs_BuildTools.exe"))
  )
  $setup = $setupCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
  if (-not $setup) {
    $expected1 = Join-Path $layoutRoot "vs_setup.exe"
    $expected2 = Join-Path $layoutRoot (Join-Path $release "vs_setup.exe")
    throw ("vs_setup.exe / vs_BuildTools.exe not found under IMAGE_FACTORY_LAYOUT_ROOT={0}. Expected one of: {1} ; {2}" -f $layoutRoot, $expected1, $expected2)
  }

  $config = "C:\ImageBuild\profile.vsconfig"
  if (-not (Test-Path $config)) { throw ("Missing {0}" -f $config) }

  # Build Tools SKU rejects VC.MFC (IDE-only); remap to ATLMFC for older Portal manifests.
  $vsconfigObj = Get-Content -Raw -Path $config | ConvertFrom-Json
  if ($vsconfigObj.components) {
    $fixed = @()
    foreach ($c in @($vsconfigObj.components)) {
      if ($c -eq "Microsoft.VisualStudio.Component.VC.MFC") {
        Write-Host "Remapping VC.MFC -> VC.ATLMFC for Build Tools"
        $fixed += "Microsoft.VisualStudio.Component.VC.ATLMFC"
      } else {
        $fixed += $c
      }
    }
    $vsconfigObj.components = @($fixed | Select-Object -Unique)
    ($vsconfigObj | ConvertTo-Json -Depth 8) | Set-Content -Path $config -Encoding UTF8
  }

  # Server Core / offline: import layout certificates or vs_installer.opc fails with 5003.
  $certDir = Join-Path $layoutRoot "certificates"
  if (Test-Path $certDir) {
    Write-Host "Importing offline layout certificates from" $certDir
    Get-ChildItem -Path $certDir -Include *.cer, *.crt -Recurse -ErrorAction SilentlyContinue |
      ForEach-Object {
        Write-Host "  cert:" $_.Name
        try {
          Import-Certificate -FilePath $_.FullName -CertStoreLocation "Cert:\LocalMachine\Root" | Out-Null
        } catch {
          Write-Host "  Root import warning:" $_.Exception.Message
        }
        try {
          Import-Certificate -FilePath $_.FullName -CertStoreLocation "Cert:\LocalMachine\CA" | Out-Null
        } catch {
          Write-Host "  CA import warning:" $_.Exception.Message
        }
      }
  } else {
    Write-Host "WARNING: no certificates folder under layout - offline install may fail with exit 5003"
  }

  Write-Host "Running" $setup
  Write-Host "vsconfig:"
  Get-Content -Raw -Path $config | Write-Host
  Write-Host "layoutRoot listing (top):"
  Get-ChildItem -Path $layoutRoot -ErrorAction SilentlyContinue |
    Select-Object -First 20 Name, Length |
    Format-Table -AutoSize |
    Out-String |
    Write-Host

  $proc = Start-Process -FilePath $setup -ArgumentList @(
    "--quiet",
    "--norestart",
    "--wait",
    "--noUpdateInstaller",
    "--noWeb",
    "--config", $config
  ) -Wait -PassThru
  if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {
    Write-Host "VS installer exit code:" $proc.ExitCode
    Get-ChildItem -Path $env:TEMP -Filter "dd_*.log" -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending |
      Select-Object -First 3 |
      ForEach-Object {
        Write-Host ("---- installer log: {0} ----" -f $_.FullName)
        Get-Content -Path $_.FullName -Tail 80 -ErrorAction SilentlyContinue
      }
    throw ("VS Build Tools install failed with exit code {0}. Offline --noWeb needs all selected components in the layout under {1}. Check profile.vsconfig." -f $proc.ExitCode, $layoutRoot)
  }
  Write-Host ("VS Build Tools install completed (exit {0})" -f $proc.ExitCode)
}

foreach ($sdk in @($manifest.dotnetSdks)) {
  $ver = [string]$sdk.version
  Write-Host "Looking for .NET SDK installer version" $ver
  $sdkSetup = Get-ChildItem -Path $installerRoot -Filter "*.exe" -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match [regex]::Escape($ver) } |
    Select-Object -First 1
  if ($sdkSetup) {
    Write-Host "Installing" $sdkSetup.FullName
    $p = Start-Process -FilePath $sdkSetup.FullName -ArgumentList "/install","/quiet","/norestart" -Wait -PassThru
    if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010) {
      throw (".NET SDK install failed: {0}" -f $p.ExitCode)
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

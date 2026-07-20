$ErrorActionPreference = "Stop"

Write-Host "Validating build environment..."

function Assert-Command($Name) {
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if (-not $cmd) { throw "Missing command: $Name" }
  Write-Host "OK" $Name "->" $cmd.Source
}

# Default: require MSBuild for real Windows factory images.
# Set IMAGE_FACTORY_STRICT=0 to skip (debug only).
$strict = $env:IMAGE_FACTORY_STRICT
if ($null -eq $strict -or $strict -eq "") { $strict = "1" }

if ($strict -eq "1") {
  $vswhere = @(
    "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe",
    "${env:ProgramFiles}\Microsoft Visual Studio\Installer\vswhere.exe"
  ) | Where-Object { Test-Path $_ } | Select-Object -First 1

  if (-not $vswhere) {
    throw "vswhere.exe not found — VS Build Tools install likely failed"
  }

  $msbuild = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -find MSBuild\**\Bin\MSBuild.exe |
    Select-Object -First 1
  if (-not $msbuild) {
    throw "MSBuild.exe not found via vswhere"
  }
  Write-Host "OK MSBuild ->" $msbuild
} else {
  Write-Host "Strict validation skipped (IMAGE_FACTORY_STRICT=$strict)"
}

Write-Host "Validate-Environment completed"

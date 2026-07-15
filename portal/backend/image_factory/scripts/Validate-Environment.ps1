$ErrorActionPreference = "Stop"

Write-Host "Validating build environment..."

function Assert-Command($Name) {
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if (-not $cmd) { throw "Missing command: $Name" }
  Write-Host "OK" $Name "->" $cmd.Source
}

# Soft checks in placeholder mode — Factory CI can set IMAGE_FACTORY_STRICT=1
if ($env:IMAGE_FACTORY_STRICT -eq "1") {
  Assert-Command "msbuild.exe"
  try {
    & vswhere.exe -latest -products * -requires Microsoft.Component.MSBuild | Out-Null
  } catch {
    throw "vswhere/MSBuild validation failed: $_"
  }
} else {
  Write-Host "Strict validation skipped (IMAGE_FACTORY_STRICT!=1)"
}

Write-Host "Validate-Environment completed"

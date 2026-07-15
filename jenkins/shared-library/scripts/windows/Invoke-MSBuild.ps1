param(
  [Parameter(Mandatory = $true)][string]$WorkDir,
  [Parameter(Mandatory = $true)][string]$SolutionPath,
  [string]$Configuration = "Release",
  [string]$Platform = "x64",
  [string]$LogDir = "C:\workspace\logs"
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$solution = Join-Path $WorkDir $SolutionPath
if (-not (Test-Path $solution)) {
  throw "Solution not found: $solution"
}

$msbuild = $null
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vswhere) {
  $msbuild = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -find "MSBuild\**\Bin\MSBuild.exe" |
    Select-Object -First 1
}
if (-not $msbuild) {
  $msbuild = Get-Command msbuild.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
}
if (-not $msbuild) {
  throw "MSBuild.exe not found in image"
}

$binlog = Join-Path $LogDir ("build-" + [DateTime]::UtcNow.ToString("yyyyMMddHHmmss") + ".binlog")
& $msbuild $solution `
  /m `
  /restore:false `
  /p:Configuration=$Configuration `
  /p:Platform=$Platform `
  /bl:$binlog

if ($LASTEXITCODE -ne 0) {
  throw "MSBuild failed with exit code $LASTEXITCODE (binlog=$binlog)"
}
Write-Host "MSBuild succeeded. binlog=$binlog"

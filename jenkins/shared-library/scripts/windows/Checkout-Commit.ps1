param(
  [Parameter(Mandatory = $true)][string]$Repository,
  [Parameter(Mandatory = $true)][string]$Commit,
  [Parameter(Mandatory = $true)][string]$GitUrlTemplate,
  [string]$WorkDir = "C:\workspace\src"
)

$ErrorActionPreference = "Stop"
if ($Commit -like "resolved:*") {
  throw "Commit is placeholder ($Commit). Portal must resolve an exact SHA before project build."
}
if ($Commit -notmatch '^[0-9a-fA-F]{40}$') {
  throw "Commit must be a 40-char SHA: $Commit"
}

$repoUrl = $GitUrlTemplate.Replace("{repository}", $Repository)
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
if (Test-Path (Join-Path $WorkDir ".git")) {
  Push-Location $WorkDir
  git fetch --all --prune
  git checkout --force $Commit
  Pop-Location
} else {
  git clone $repoUrl $WorkDir
  Push-Location $WorkDir
  git checkout --force $Commit
  Pop-Location
}

Write-Host "Checked out $Repository @ $Commit into $WorkDir"

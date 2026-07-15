param(
  [Parameter(Mandatory = $true)][string]$ManifestPath
)

$ErrorActionPreference = "Stop"
$manifest = Get-Content -Raw -Path $ManifestPath | ConvertFrom-Json

Write-Host "Installing VS layout release:" $manifest.visualStudio.layoutRelease
Write-Host "Components:" ($manifest.visualStudio.components -join ", ")

# Placeholder steps — real Factory host maps RO layout and runs vs_setup.exe:
# & "$env:IMAGE_FACTORY_LAYOUT_ROOT\vs_setup.exe" --quiet --norestart --wait `
#     --noUpdateInstaller --noWeb --config C:\ImageBuild\profile.vsconfig

foreach ($sdk in @($manifest.dotnetSdks)) {
  Write-Host "Would install .NET SDK" $sdk.version "sha" $sdk.installerSha256
}
foreach ($tp in @($manifest.dotnetFrameworkTargetingPacks)) {
  Write-Host "Would install Targeting Pack" $tp.version
}
foreach ($ws in @($manifest.windowsSdks)) {
  Write-Host "Would install Windows SDK" $ws.version
}

Write-Host "Install-FromManifest placeholder finished"

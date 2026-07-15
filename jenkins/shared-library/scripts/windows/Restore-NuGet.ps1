param(
  [Parameter(Mandatory = $true)][string]$WorkDir,
  [Parameter(Mandatory = $true)][string]$SolutionPath,
  [ValidateSet("repo-packages", "internal-feed", "repo-packages-and-internal-feed")]
  [string]$NugetMode = "repo-packages-and-internal-feed",
  [string]$InternalFeedUrl = "",
  [string]$NugetConfigPath = ""
)

$ErrorActionPreference = "Stop"

function Escape-Xml([string]$value) {
  if ($null -eq $value) { return "" }
  return ($value -replace '&', '&amp;' -replace '<', '&lt;' -replace '>', '&gt;' -replace '"', '&quot;' -replace "'", '&apos;')
}

Push-Location $WorkDir
try {
  $solution = Join-Path $WorkDir $SolutionPath
  if (-not (Test-Path $solution)) {
    throw "Solution not found: $solution"
  }

  if ($NugetMode -ne "repo-packages" -and -not $InternalFeedUrl -and -not $NugetConfigPath) {
    throw "internal feed URL or NuGet.config path required for mode $NugetMode"
  }

  if ($NugetConfigPath) {
    Copy-Item -Force $NugetConfigPath (Join-Path $WorkDir "NuGet.config")
  } elseif ($InternalFeedUrl) {
    if ($InternalFeedUrl -notmatch '^https?://') {
      throw "InternalFeedUrl must be http(s) URL"
    }
    $safeUrl = Escape-Xml $InternalFeedUrl
    @"
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="internal" value="$safeUrl" />
  </packageSources>
</configuration>
"@ | Set-Content -Encoding UTF8 (Join-Path $WorkDir "NuGet.config")
  }

  if (Get-Command dotnet -ErrorAction SilentlyContinue) {
    dotnet restore $solution --verbosity minimal
  } elseif (Get-Command nuget -ErrorAction SilentlyContinue) {
    nuget restore $solution -NonInteractive
  } else {
    throw "Neither dotnet nor nuget is available in the builder image"
  }
}
finally {
  Pop-Location
}

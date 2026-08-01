$ErrorActionPreference = "Stop"
$guiProject = Join-Path $PSScriptRoot "TheTower.ControlSurface.csproj"
$hostProject = Join-Path $PSScriptRoot "..\TheTower.TunnelHost\TheTower.TunnelHost.csproj"
$output = Join-Path $PSScriptRoot "publish\win-x64"
$staging = Join-Path $PSScriptRoot "publish\.win-x64-staging"

if (Test-Path -LiteralPath $staging) {
  Remove-Item -LiteralPath $staging -Recurse -Force
}
New-Item -ItemType Directory -Path $staging | Out-Null

foreach ($project in @($hostProject, $guiProject)) {
  dotnet publish $project `
    --configuration Release `
    --runtime win-x64 `
    --self-contained true `
    --output $staging `
    -p:PublishSingleFile=true `
    -p:IncludeNativeLibrariesForSelfExtract=true `
    -p:EnableCompressionInSingleFile=true

  if ($LASTEXITCODE -ne 0) {
    throw "dotnet publish failed for $project with exit code $LASTEXITCODE. The existing package was not changed."
  }
}

$requiredFiles = @(
  "TheTower.ControlSurface.exe",
  "TheTower.TunnelHost.exe"
)
foreach ($file in $requiredFiles) {
  $path = Join-Path $staging $file
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "Publish completed without creating the required package file: $path"
  }
}

if (Test-Path -LiteralPath $output) {
  Remove-Item -LiteralPath $output -Recurse -Force
}
Move-Item -LiteralPath $staging -Destination $output

Write-Host "Published complete standalone Control Surface package to:"
Write-Host $output
foreach ($file in $requiredFiles) {
  Write-Host "  $file"
}

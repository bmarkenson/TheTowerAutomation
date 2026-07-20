$ErrorActionPreference = "Stop"
$project = Join-Path $PSScriptRoot "TheTower.ControlSurface.csproj"
$output = Join-Path $PSScriptRoot "publish\win-x64"

dotnet publish $project `
  --configuration Release `
  --runtime win-x64 `
  --self-contained true `
  --output $output `
  -p:PublishSingleFile=true `
  -p:IncludeNativeLibrariesForSelfExtract=true `
  -p:EnableCompressionInSingleFile=true

if ($LASTEXITCODE -ne 0) {
  throw "dotnet publish failed with exit code $LASTEXITCODE. No executable was produced."
}

$executable = Join-Path $output "TheTower.ControlSurface.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
  throw "dotnet publish completed without creating the expected executable: $executable"
}

Write-Host "Published standalone application to:"
Write-Host $executable

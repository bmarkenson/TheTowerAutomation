$ErrorActionPreference = "Stop"
$guiProject = Join-Path $PSScriptRoot "TheTower.ControlSurface.csproj"
$hostProject = Join-Path $PSScriptRoot "..\TheTower.TunnelHost\TheTower.TunnelHost.csproj"
$publishRoot = Join-Path $PSScriptRoot "publish"
$output = Join-Path $publishRoot "win-x64"
$history = Join-Path $publishRoot "previous"
$nonce = [guid]::NewGuid().ToString("N")
$staging = Join-Path $publishRoot ".win-x64-staging-$nonce"
$nextHistory = Join-Path $publishRoot ".previous-staging-$nonce"
$currentBackup = Join-Path $publishRoot ".win-x64-backup-$nonce"
$historyBackup = Join-Path $publishRoot ".previous-backup-$nonce"
$lockPath = Join-Path $publishRoot ".publish.lock"
$lockStream = $null
$requiredFiles = @(
  "TheTower.ControlSurface.exe",
  "TheTower.TunnelHost.exe"
)

function Assert-CompletePackage {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Description
  )

  if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    throw "$Description is not a package directory: $Path"
  }
  foreach ($file in $requiredFiles) {
    $filePath = Join-Path $Path $file
    if (-not (Test-Path -LiteralPath $filePath -PathType Leaf) -or
        (Get-Item -LiteralPath $filePath).Length -le 0) {
      throw "$Description is missing nonempty $file`: $Path"
    }
  }
}

New-Item -ItemType Directory -Path $publishRoot -Force | Out-Null
try {
  $lockStream = [System.IO.File]::Open(
    $lockPath,
    [System.IO.FileMode]::OpenOrCreate,
    [System.IO.FileAccess]::ReadWrite,
    [System.IO.FileShare]::None)
}
catch {
  throw "Another Windows package publication is already in progress. $($_.Exception.Message)"
}

$hadCurrent = $false
$hadHistory = $false
$installedCurrent = $false
$installedHistory = $false
$swapComplete = $false

try {
  New-Item -ItemType Directory -Path $staging | Out-Null
  New-Item -ItemType Directory -Path $nextHistory | Out-Null

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

  Assert-CompletePackage $staging "Newly built package"

  if ((Test-Path -LiteralPath $output) -and
      -not (Test-Path -LiteralPath $output -PathType Container)) {
    throw "The current package path is not a directory: $output"
  }
  if ((Test-Path -LiteralPath $history) -and
      -not (Test-Path -LiteralPath $history -PathType Container)) {
    throw "The prior-package path is not a directory: $history"
  }
  if ((Test-Path -LiteralPath $history -PathType Container) -and
      -not (Test-Path -LiteralPath $output -PathType Container)) {
    throw "Prior packages exist without a current package; refusing rotation."
  }
  if (Test-Path -LiteralPath $history -PathType Container) {
    $unexpected = Get-ChildItem -LiteralPath $history -Force |
      Where-Object { $_.Name -notin @("1", "2") } |
      Select-Object -First 1
    if ($null -ne $unexpected) {
      throw "Unexpected entry in managed prior-package directory: $($unexpected.FullName)"
    }
  }
  foreach ($slot in @(1, 2)) {
    $slotPath = Join-Path $history $slot
    if ((Test-Path -LiteralPath $slotPath) -and
        -not (Test-Path -LiteralPath $slotPath -PathType Container)) {
      throw "Prior-package slot $slot is not a directory; refusing rotation."
    }
  }
  $historyOne = Join-Path $history "1"
  $historyTwo = Join-Path $history "2"
  if ((Test-Path -LiteralPath $historyTwo) -and
      -not (Test-Path -LiteralPath $historyOne -PathType Container)) {
    throw "Prior-package slot 2 exists without slot 1; refusing rotation."
  }

  if (Test-Path -LiteralPath $output -PathType Container) {
    Assert-CompletePackage $output "Current package"
    $nextOne = Join-Path $nextHistory "1"
    Copy-Item -LiteralPath $output -Destination $nextOne -Recurse
    Assert-CompletePackage $nextOne "Staged prior package 1"
  }
  if (Test-Path -LiteralPath $historyOne -PathType Container) {
    Assert-CompletePackage $historyOne "Prior package 1"
    $nextTwo = Join-Path $nextHistory "2"
    Copy-Item -LiteralPath $historyOne -Destination $nextTwo -Recurse
    Assert-CompletePackage $nextTwo "Staged prior package 2"
  }
  if (Test-Path -LiteralPath $historyTwo -PathType Container) {
    Assert-CompletePackage $historyTwo "Prior package 2"
  }

  try {
    if (Test-Path -LiteralPath $output -PathType Container) {
      $hadCurrent = $true
      Move-Item -LiteralPath $output -Destination $currentBackup
    }
    if (Test-Path -LiteralPath $history -PathType Container) {
      $hadHistory = $true
      Move-Item -LiteralPath $history -Destination $historyBackup
    }

    $installedCurrent = $true
    Move-Item -LiteralPath $staging -Destination $output
    if ($hadCurrent) {
      $installedHistory = $true
      Move-Item -LiteralPath $nextHistory -Destination $history
    }
    Assert-CompletePackage $output "Installed current package"
    if ($installedHistory) {
      Assert-CompletePackage (Join-Path $history "1") "Installed prior package 1"
      if (Test-Path -LiteralPath (Join-Path $history "2") -PathType Container) {
        Assert-CompletePackage (Join-Path $history "2") "Installed prior package 2"
      }
    }
    $swapComplete = $true
  }
  catch {
    if ($installedHistory -and (Test-Path -LiteralPath $history)) {
      Remove-Item -LiteralPath $history -Recurse -Force
    }
    if ($hadHistory -and (Test-Path -LiteralPath $historyBackup)) {
      Move-Item -LiteralPath $historyBackup -Destination $history
    }
    if ($installedCurrent -and (Test-Path -LiteralPath $output)) {
      Remove-Item -LiteralPath $output -Recurse -Force
    }
    if ($hadCurrent -and (Test-Path -LiteralPath $currentBackup)) {
      Move-Item -LiteralPath $currentBackup -Destination $output
    }
    throw
  }

  Write-Host "Published complete standalone Control Surface package to:"
  Write-Host $output
  foreach ($file in $requiredFiles) {
    Write-Host "  $file"
  }
  if (Test-Path -LiteralPath (Join-Path $history "1") -PathType Container) {
    Write-Host "Retained prior complete packages (newest first):"
    Write-Host "  $(Join-Path $history '1')"
    if (Test-Path -LiteralPath (Join-Path $history "2") -PathType Container) {
      Write-Host "  $(Join-Path $history '2')"
    }
  }
}
finally {
  $cleanupPaths = @($staging, $nextHistory)
  if ($swapComplete) {
    $cleanupPaths += @($currentBackup, $historyBackup)
  }
  foreach ($path in $cleanupPaths) {
    if (Test-Path -LiteralPath $path) {
      Remove-Item -LiteralPath $path -Recurse -Force
    }
  }
  if ($null -ne $lockStream) {
    $lockStream.Dispose()
  }
}

if (-not $swapComplete) {
  throw "Publication did not complete its guarded package rotation."
}

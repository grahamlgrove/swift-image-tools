param([switch]$SkipDependencies)
$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Version = "1.0.0"
$AppName = "Grove Swift Image Tools"
$BuildStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$MagickSource = "C:\Program Files\ImageMagick-7.1.1-Q16-HDRI"
$MagickBundle = Join-Path $ProjectDir "ImageMagick"
$Assets = Join-Path $ProjectDir "assets"
$ReleaseBuild = Join-Path $ProjectDir "ReleaseBuild-$Version-$BuildStamp"
$AppFolder = Join-Path $ReleaseBuild $AppName
$Release = Join-Path $ProjectDir "Release"
$Wix = Join-Path $ProjectDir ".tools\wix314"

Set-Location $ProjectDir
if (-not $SkipDependencies) { python -m pip install --target .vendor --upgrade -r requirements.txt }
if (-not (Test-Path (Join-Path $MagickSource "magick.exe"))) { throw "ImageMagick was not found at $MagickSource" }
if (-not (Test-Path (Join-Path $MagickBundle "magick.exe"))) { Copy-Item -LiteralPath $MagickSource -Destination $MagickBundle -Recurse }
if (-not (Test-Path (Join-Path $Wix "candle.exe"))) { throw "WiX 3.14.1 portable tools are missing from $Wix" }

New-Item -ItemType Directory -Force -Path $ReleaseBuild, $Release | Out-Null
$env:PYTHONPATH = Join-Path $ProjectDir ".vendor"
python -m PyInstaller --noconfirm --clean --windowed --name $AppName `
    --paths ".vendor" --collect-all tkinterdnd2 --icon (Join-Path $Assets "grove-swift-image-tools.ico") `
    --add-data "$MagickBundle;ImageMagick" --add-data "$Assets;assets" `
    --version-file (Join-Path $ProjectDir "version_info.txt") --distpath $ReleaseBuild --workpath "build-release-$Version-$BuildStamp" `
    --specpath "build-release-$Version-$BuildStamp" (Join-Path $ProjectDir "image_manipulator.py")
if ($LASTEXITCODE -ne 0) { throw "Application packaging failed with exit code $LASTEXITCODE" }

Copy-Item -LiteralPath "LICENSE" -Destination $AppFolder -Force
Copy-Item -LiteralPath "THIRD_PARTY_NOTICES.md" -Destination $AppFolder -Force
$Portable = Join-Path $Release "Grove-Swift-Image-Tools-$Version-Portable.zip"
Compress-Archive -LiteralPath $AppFolder -DestinationPath $Portable -CompressionLevel Optimal -Force

& (Join-Path $Wix "heat.exe") dir $AppFolder -nologo -ag -sfrag -srd -sreg -dr INSTALLFOLDER -cg AppFiles -var "var.AppSource" -out "app-files.wxs"
if ($LASTEXITCODE -ne 0) { throw "WiX file harvesting failed with exit code $LASTEXITCODE" }
& (Join-Path $Wix "candle.exe") -nologo -arch x64 -dAppSource="$AppFolder" installer.wxs app-files.wxs
if ($LASTEXITCODE -ne 0) { throw "MSI compilation failed with exit code $LASTEXITCODE" }
$Msi = Join-Path $Release "Grove-Swift-Image-Tools-$Version-x64.msi"
& (Join-Path $Wix "light.exe") -nologo -sice:ICE61 -out $Msi installer.wixobj app-files.wixobj
if ($LASTEXITCODE -ne 0) { throw "MSI linking failed with exit code $LASTEXITCODE" }

Get-FileHash -Algorithm SHA256 $Portable, $Msi | ForEach-Object { "{0}  {1}" -f $_.Hash.ToLowerInvariant(), (Split-Path -Leaf $_.Path) } | Set-Content -Encoding ascii (Join-Path $Release "SHA256SUMS.txt")
Write-Host ""
Write-Host "Portable: $Portable"
Write-Host "MSI:      $Msi"

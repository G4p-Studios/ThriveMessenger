param(
    [string]$OutputDir = "dist-windows"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

python versionfile.py
python -m PyInstaller --clean --noconfirm --windowed --version-file=vdata.txt --name "thrive_messenger" --distpath $OutputDir main.py

$AppDir = Join-Path $Root (Join-Path $OutputDir "thrive_messenger")
if (!(Test-Path $AppDir)) {
    throw "PyInstaller output not found: $AppDir"
}

New-Item -ItemType Directory -Force -Path (Join-Path $AppDir "sounds") | Out-Null
Copy-Item -Recurse -Force "sounds\*" (Join-Path $AppDir "sounds")
Copy-Item -Force "client.conf" (Join-Path $AppDir "client.conf")

if (Test-Path "assets") {
    Copy-Item -Recurse -Force "assets" (Join-Path $AppDir "assets")
}

Copy-Item -Force "README.md" (Join-Path $AppDir "README.md")

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$PortableZip = Join-Path $OutputDir "thrive_messenger.zip"
if (Test-Path $PortableZip) {
    Remove-Item -Force $PortableZip
}
Compress-Archive -Path $AppDir -DestinationPath $PortableZip -Force

Add-Type -AssemblyName System.IO.Compression.FileSystem
$Archive = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path $PortableZip))
try {
    $RequiredEntry = "thrive_messenger/thrive_messenger.exe"
    $HasRequiredEntry = $Archive.Entries | Where-Object {
        $_.FullName.Replace("\", "/") -eq $RequiredEntry
    }
    if (!$HasRequiredEntry) {
        throw "Portable ZIP is incompatible with older updaters: missing $RequiredEntry"
    }
}
finally {
    $Archive.Dispose()
}

Write-Host "Portable package created: $PortableZip"

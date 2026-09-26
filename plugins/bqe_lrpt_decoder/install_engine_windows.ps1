$ErrorActionPreference = 'Stop'
$vendorDirectory = Join-Path $PSScriptRoot 'vendor'
New-Item -ItemType Directory -Force -Path $vendorDirectory | Out-Null
$archivePath = Join-Path $vendorDirectory 'SatDump-1.2.2.zip'
Invoke-WebRequest -Uri 'https://github.com/SatDump/SatDump/releases/download/1.2.2/SatDump-Windows_x64_Portable.zip' -OutFile $archivePath
$expectedHash = '54580DD3C14E198381A31AF07B7D12AF53C7F1AB07CF4EC84299BBF20F046FBD'
if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash -ne $expectedHash) {
    throw 'SatDump archive checksum does not match the tested official 1.2.2 build.'
}
Expand-Archive -LiteralPath $archivePath -DestinationPath (Join-Path $vendorDirectory 'satdump') -Force
Write-Host 'Portable SatDump 1.2.2 installed. Run start_windows.bat to open BQE LRPT.'

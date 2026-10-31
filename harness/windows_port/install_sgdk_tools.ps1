param(
    [string]$InstallDirectory = (Join-Path $env:USERPROFILE 'toolchains/cavc-m68k')
)

$ErrorActionPreference = 'Stop'
$releaseUrl = 'https://github.com/Stephane-D/SGDK/releases/download/v2.11/sgdk211.7z'
$expectedSha256 = '5CC704B7E3A15183C33E721A1D7F84C067CF78808754556D03BB14764DF51437'
$tempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
$archive = Join-Path $tempRoot "cavc-sgdk-v2.11-$([guid]::NewGuid().ToString('N')).7z"
$destination = [IO.Path]::GetFullPath($InstallDirectory)
$sevenZip = Get-Command 7z.exe -ErrorAction SilentlyContinue
if (-not $sevenZip) {
    $default7z = Join-Path $env:ProgramFiles '7-Zip/7z.exe'
    if (Test-Path $default7z) { $sevenZipPath = $default7z }
    else { throw '7-Zip is required to extract the official SGDK v2.11 Windows toolchain archive.' }
} else {
    $sevenZipPath = $sevenZip.Source
}

try {
    & curl.exe --location --fail --silent --show-error --output $archive $releaseUrl
    if ($LASTEXITCODE -ne 0) { throw "SGDK toolchain download failed ($LASTEXITCODE)" }
    $actualSha256 = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    if ($actualSha256 -ne $expectedSha256) {
        throw "SGDK archive SHA-256 mismatch: expected $expectedSha256, got $actualSha256"
    }

    New-Item -ItemType Directory -Force $destination | Out-Null
    & $sevenZipPath x $archive "-o$destination" 'bin\*' 'license.txt' 'COPYING.RUNTIME' -y | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "SGDK toolchain extraction failed ($LASTEXITCODE)" }
    $bin = Join-Path $destination 'bin'
    foreach ($name in @('as', 'ld', 'objcopy', 'nm', 'size', 'objdump')) {
        $source = Join-Path $bin "$name.exe"
        $target = Join-Path $bin "m68k-elf-$name.exe"
        if (-not (Test-Path $source)) { throw "SGDK toolchain is missing $source" }
        Copy-Item -LiteralPath $source -Destination $target -Force
        $version = (& $target --version | Select-Object -First 1)
        if ($LASTEXITCODE -ne 0 -or $version -notmatch '2\.40') {
            throw "Unexpected $name version from SGDK archive: $version"
        }
        Write-Output "$name`: $version"
    }
    $prefix = (Resolve-Path $bin).Path.TrimEnd('\') + '\m68k-elf-'
    Write-Output "Tool prefix: $prefix"
    Write-Output "Set for this PowerShell session: `$env:M68K_PREFIX = '$prefix'"
}
finally {
    $resolvedArchive = [IO.Path]::GetFullPath($archive)
    if (-not $resolvedArchive.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove temporary path outside TEMP: $resolvedArchive"
    }
    Remove-Item -LiteralPath $resolvedArchive -Force -ErrorAction SilentlyContinue
}

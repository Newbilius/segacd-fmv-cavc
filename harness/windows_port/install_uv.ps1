param(
    [string]$InstallDirectory = (Join-Path $env:USERPROFILE 'toolchains/cavc-uv/0.11.29/bin')
)

$ErrorActionPreference = 'Stop'
$version = '0.11.29'
$url = "https://releases.astral.sh/github/uv/releases/download/$version/uv-x86_64-pc-windows-msvc.zip"
$expectedSha256 = 'A047D55651BC3E0CA24595B25EC4CFCB10F9DCA9FB56514E661269B37D4FAE68'
$curl = Get-Command curl.exe -ErrorAction SilentlyContinue
if (-not $curl) { throw 'curl.exe is required to download the pinned uv release.' }

$workRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
$work = Join-Path $workRoot ("cavc-uv-$([guid]::NewGuid().ToString('N'))")
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $archive = Join-Path $work 'uv.zip'
    & $curl.Source --location --fail --silent --show-error --output $archive $url
    if ($LASTEXITCODE -ne 0) { throw "uv download failed ($LASTEXITCODE)" }
    $actualSha256 = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    if ($actualSha256 -ne $expectedSha256) {
        throw "uv archive SHA-256 mismatch: expected $expectedSha256, got $actualSha256"
    }

    $expanded = Join-Path $work 'expanded'
    Expand-Archive -LiteralPath $archive -DestinationPath $expanded
    $destination = [IO.Path]::GetFullPath($InstallDirectory)
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    foreach ($name in @('uv.exe', 'uvx.exe', 'uvw.exe')) {
        $source = Join-Path $expanded $name
        if (-not (Test-Path -LiteralPath $source)) { throw "uv archive is missing $name" }
        Copy-Item -LiteralPath $source -Destination (Join-Path $destination $name) -Force
    }
    $versionOutput = (& (Join-Path $destination 'uv.exe') --version).Trim()
    if ($LASTEXITCODE -ne 0 -or $versionOutput -notmatch "^uv $([regex]::Escape($version))(\s|$)") {
        throw "Unexpected uv version: $versionOutput"
    }
    Write-Output "uv $version installed: $destination"
    Write-Output "Archive SHA-256 verified: $actualSha256"
}
finally {
    $resolvedWork = [IO.Path]::GetFullPath($work)
    if (-not $resolvedWork.StartsWith($workRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove temporary path outside TEMP: $resolvedWork"
    }
    Remove-Item -LiteralPath $resolvedWork -Recurse -Force
}

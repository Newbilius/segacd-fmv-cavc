param(
    [ValidateSet('--cpu', '--gpu', '--all')]
    [string]$Mode = '--cpu',
    [string]$EnvironmentRoot
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) { throw "uv 0.11.29 is required. Install it first, then rerun this script." }
$uvVersion = (& uv --version).Trim().Split(' ')[1]
if ($uvVersion -ne '0.11.29') {
    throw "This project requires uv 0.11.29; found $uvVersion. Update it with: uv self update 0.11.29"
}

function Sync-Environment([string]$directory, [string]$versionFile, [string[]]$extra) {
    $version = (Get-Content -Raw (Join-Path $root $versionFile)).Trim()
    & uv python install $version
    if ($LASTEXITCODE -ne 0) { throw "uv python install failed ($LASTEXITCODE)" }
    $environmentPath = if ($EnvironmentRoot) {
        Join-Path ([IO.Path]::GetFullPath($EnvironmentRoot)) (Split-Path $directory -Leaf)
    } else {
        Join-Path $root $directory
    }
    $uvArgs = @('sync', '--project', $root, '--managed-python', '--python', $version, '--locked') + $extra
    $previousEnvironment = [Environment]::GetEnvironmentVariable('UV_PROJECT_ENVIRONMENT', 'Process')
    try {
        $env:UV_PROJECT_ENVIRONMENT = $environmentPath
        & uv @uvArgs
        if ($LASTEXITCODE -ne 0) { throw "uv sync failed ($LASTEXITCODE)" }
    } finally {
        if ($null -eq $previousEnvironment) {
            Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable('UV_PROJECT_ENVIRONMENT', $previousEnvironment, 'Process')
        }
    }
}

switch ($Mode) {
    '--cpu' { Sync-Environment '.venv' '.python-version' @() }
    '--gpu' { Sync-Environment '.venv-gpu' '.python-version-gpu' @('--extra', 'gpu') }
    '--all' {
        Sync-Environment '.venv' '.python-version' @()
        Sync-Environment '.venv-gpu' '.python-version-gpu' @('--extra', 'gpu')
    }
}

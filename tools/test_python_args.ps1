$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'python_args.ps1')

$inputArgs = @(
    '--workspace-root', 'C:\workspace with spaces',
    '--device', 'cuda',
    'tools/cavc_cli.py', 'convert', 'profile.toml', '--device', 'cpu',
    '--array-storage', 'memory'
)
$parsed = Get-CavcPythonArguments -InputArguments $inputArgs -GpuEnvironmentAvailable $true
if (-not $parsed.Gpu -or $parsed.Device -ne 'cuda') {
    throw 'launcher device selection before the script was not retained'
}
if ($parsed.Runtime['--workspace-root'] -ne 'C:\workspace with spaces') {
    throw 'launcher runtime option was not parsed'
}
if (($parsed.Arguments -join '|') -ne 'tools/cavc_cli.py|convert|profile.toml|--device|cpu|--array-storage|memory') {
    throw 'arguments after the script were changed by the launcher'
}

$childArgs = @('tools/sim.py', '--device', 'cuda', '--workspace-root', 'child-owned')
$parsed = Get-CavcPythonArguments -InputArguments $childArgs -GpuEnvironmentAvailable $false
if ($parsed.Gpu -or $null -ne $parsed.Device -or ($parsed.Arguments -join '|') -ne ($childArgs -join '|')) {
    throw 'script arguments beginning with launcher option names were intercepted'
}

$parsed = Get-CavcPythonArguments -InputArguments @('--device','auto','tools/sim.py') -GpuEnvironmentAvailable $false
if ($parsed.Gpu -or $parsed.Device -ne 'auto') {
    throw 'auto did not select the CPU environment when GPU environment is absent'
}

Write-Output 'Windows Python launcher argument checks passed'

& (Join-Path $PSScriptRoot 'python.ps1') -c 'raise SystemExit(37)'
if ($LASTEXITCODE -ne 37) {
    throw "Python launcher changed the child exit code to $LASTEXITCODE"
}
Write-Output 'Windows Python launcher exit-code check passed'

& (Join-Path $PSScriptRoot 'python.ps1') -c 'import sys; expected=chr(117)+chr(116)+chr(102)+chr(45)+chr(56); assert sys.stdout.encoding.lower() == expected; assert sys.stderr.encoding.lower() == expected; assert sys.flags.utf8_mode == 1'
if ($LASTEXITCODE -ne 0) {
    throw 'Python launcher did not force UTF-8 text handling'
}
Write-Output 'Windows Python launcher UTF-8 stream and file defaults check passed'

$argumentProbe = 'import sys; assert sys.argv[1:] == [bytes.fromhex("76616c756520776974682022656d6265646465642071756f74657322").decode(), bytes.fromhex("433a5c70617468207769746820747261696c696e675c").decode()]'
& (Join-Path $PSScriptRoot 'python.ps1') -c $argumentProbe 'value with "embedded quotes"' 'C:\path with trailing\'
if ($LASTEXITCODE -ne 0) {
    throw 'Python launcher changed quoted, spaced, or trailing-backslash arguments'
}
$emptyArgumentProbe = 'import sys; assert len(sys.argv) == 2 and sys.argv[1] == ""'
& (Join-Path $PSScriptRoot 'python.ps1') -c $emptyArgumentProbe ''
if ($LASTEXITCODE -ne 0) {
    throw 'Python launcher did not preserve an empty argument'
}
Write-Output 'Windows Python launcher child argument-boundary checks passed'

$savedEnvironment = @{}
foreach ($name in @('CAVC_WORKSPACE_ROOT','CAVC_SCRATCH_ROOT','CAVC_ARRAY_STORAGE','SEGACD_TMPFS_ROOT','TMPDIR','TMP','TEMP')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    $env:CAVC_WORKSPACE_ROOT = Join-Path $PWD 'tmp/launcher-env/workspace'
    $env:CAVC_SCRATCH_ROOT = Join-Path $PWD 'tmp/launcher-env/scratch'
    $env:CAVC_ARRAY_STORAGE = 'memory'
    $env:SEGACD_TMPFS_ROOT = Join-Path $PWD 'tmp/launcher-legacy/workspace'
    $env:TMPDIR = Join-Path $PWD 'tmp/launcher-tmpdir'
    $env:TMP = $env:TMPDIR
    $env:TEMP = $env:TMPDIR

    $cliWorkspace = Join-Path $PWD 'tmp/launcher-cli/work space'
    $cliScratch = Join-Path $PWD 'tmp/launcher-cli/scratch space'
    $runtimeProbe = 'import json, os; print(json.dumps({key: os.environ.get(key) for key in ("CAVC_WORKSPACE_ROOT", "CAVC_SCRATCH_ROOT", "CAVC_ARRAY_STORAGE", "TMPDIR", "TMP", "TEMP", "CBRSIM_DEVICE", "PYTHONPATH", "PYTHONIOENCODING", "PYTHONUNBUFFERED", "PYTHONUTF8")}))'
    $probeLines = & (Join-Path $PSScriptRoot 'python.ps1') --device cpu --workspace-root $cliWorkspace --scratch-root $cliScratch --array-storage disk -c $runtimeProbe
    if ($LASTEXITCODE -ne 0) { throw 'Python launcher rejected explicit runtime options' }
    $probe = ($probeLines -join "`n") | ConvertFrom-Json
    $pythonPathParts = $probe.PYTHONPATH -split [regex]::Escape([IO.Path]::PathSeparator)
    $expectedPythonTools = [IO.Path]::GetFullPath($PSScriptRoot)
    $expectedPythonRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    if ($probe.CAVC_WORKSPACE_ROOT -ne [IO.Path]::GetFullPath($cliWorkspace) -or
        $probe.CAVC_SCRATCH_ROOT -ne [IO.Path]::GetFullPath($cliScratch) -or
        $probe.CAVC_ARRAY_STORAGE -ne 'disk' -or
        $probe.TMPDIR -ne [IO.Path]::GetFullPath($cliScratch) -or
        $probe.TMP -ne [IO.Path]::GetFullPath($cliScratch) -or
        $probe.TEMP -ne [IO.Path]::GetFullPath($cliScratch) -or
        $probe.CBRSIM_DEVICE -ne 'cpu' -or
        $probe.PYTHONIOENCODING -ne 'utf-8' -or
        $probe.PYTHONUNBUFFERED -ne '1' -or
        $probe.PYTHONUTF8 -ne '1' -or
        $pythonPathParts.Count -lt 2 -or
        $pythonPathParts[0] -ne $expectedPythonTools -or
        $pythonPathParts[1] -ne $expectedPythonRoot) {
        throw 'CLI runtime options did not override the environment or propagate to child temp paths'
    }
    foreach ($name in @('CBRSIM_DEVICE','PYTHONPATH','PYTHONIOENCODING','PYTHONUNBUFFERED','PYTHONUTF8')) {
        if ([Environment]::GetEnvironmentVariable($name, 'Process') -ne $savedEnvironment[$name]) {
            throw "Python launcher leaked $name into the PowerShell session"
        }
    }

    $probeLines = & (Join-Path $PSScriptRoot 'python.ps1') -c $runtimeProbe
    if ($LASTEXITCODE -ne 0) { throw 'Python launcher rejected environment runtime options' }
    $probe = ($probeLines -join "`n") | ConvertFrom-Json
    if ($probe.CAVC_WORKSPACE_ROOT -ne [IO.Path]::GetFullPath($env:CAVC_WORKSPACE_ROOT) -or
        $probe.CAVC_SCRATCH_ROOT -ne [IO.Path]::GetFullPath($env:CAVC_SCRATCH_ROOT) -or
        $probe.CAVC_ARRAY_STORAGE -ne 'memory' -or
        $probe.TMPDIR -ne [IO.Path]::GetFullPath($env:CAVC_SCRATCH_ROOT) -or
        $probe.TMP -ne [IO.Path]::GetFullPath($env:CAVC_SCRATCH_ROOT) -or
        $probe.TEMP -ne [IO.Path]::GetFullPath($env:CAVC_SCRATCH_ROOT)) {
        throw 'Python launcher did not preserve environment runtime defaults'
    }

    Remove-Item Env:CAVC_WORKSPACE_ROOT,Env:CAVC_SCRATCH_ROOT -ErrorAction SilentlyContinue
    $env:TMPDIR = Join-Path $PWD 'tmp/launcher-legacy/scratch'
    $env:TMP = $env:TMPDIR
    $env:TEMP = $env:TMPDIR
    $probeLines = & (Join-Path $PSScriptRoot 'python.ps1') -c $runtimeProbe
    if ($LASTEXITCODE -ne 0) { throw 'Python launcher rejected legacy runtime defaults' }
    $probe = ($probeLines -join "`n") | ConvertFrom-Json
    if ($probe.CAVC_WORKSPACE_ROOT -ne [IO.Path]::GetFullPath($env:SEGACD_TMPFS_ROOT) -or
        $probe.CAVC_SCRATCH_ROOT -ne [IO.Path]::GetFullPath($env:TMPDIR) -or
        $probe.CAVC_ARRAY_STORAGE -ne 'memory' -or
        $probe.TMP -ne [IO.Path]::GetFullPath($env:TMPDIR) -or
        $probe.TEMP -ne [IO.Path]::GetFullPath($env:TMPDIR)) {
        throw 'Python launcher did not preserve legacy workspace and TMPDIR defaults'
    }
} finally {
    foreach ($name in $savedEnvironment.Keys) {
        if ($null -eq $savedEnvironment[$name]) {
            Remove-Item "Env:$name" -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
        }
    }
}
Write-Output 'Windows Python launcher CLI/environment precedence and temp-path propagation checks passed'

$savedDevice = [Environment]::GetEnvironmentVariable('CBRSIM_DEVICE', 'Process')
try {
    $env:CBRSIM_DEVICE = 'launcher-parent-sentinel'
    $doctorOutput = & (Join-Path $PSScriptRoot 'cavc.ps1') doctor 2>&1
    if (($doctorOutput -join "`n") -notmatch '(?m)^Python: ' -or
        ($doctorOutput -join "`n") -notmatch '(?m)^workspace: writable ' -or
        ($doctorOutput -join "`n") -notmatch '(?m)^scratch: writable ' -or
        ($doctorOutput -join "`n") -match 'unrecognized arguments') {
        throw 'CAVC doctor did not preserve the action or verify selected storage'
    }
    if ($env:CBRSIM_DEVICE -ne 'launcher-parent-sentinel') {
        throw 'CAVC launcher leaked its selected device into the PowerShell session'
    }
} finally {
    if ($null -eq $savedDevice) {
        Remove-Item Env:CBRSIM_DEVICE -ErrorAction SilentlyContinue
    } else {
        [Environment]::SetEnvironmentVariable('CBRSIM_DEVICE', $savedDevice, 'Process')
    }
}
$global:LASTEXITCODE = 0
Write-Output 'Windows CAVC launcher single-action check passed'

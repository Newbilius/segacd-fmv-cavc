$Arguments = @($args)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$runtimeArguments = @()
$i = 0
while ($i -lt $Arguments.Count -and
    $Arguments[$i] -in @('--workspace-root','--scratch-root','--array-storage')) {
    $runtimeArguments += $Arguments[$i]
    if (++$i -ge $Arguments.Count) {
        throw "$($runtimeArguments[-1]) requires a value"
    }
    $runtimeArguments += $Arguments[$i]
    $i++
}
$cliArguments = if ($i -lt $Arguments.Count) { @($Arguments[$i..($Arguments.Count - 1)]) } else { @() }
$action = if ($i -lt $Arguments.Count) { [string]$Arguments[$i] } else { '' }
$device = 'auto'
for ($j=0; $j -lt $cliArguments.Count; $j++) {
    if ($cliArguments[$j] -eq '--device' -and $j+1 -lt $cliArguments.Count) {
        $device = $cliArguments[$j+1]
    }
}
$gpuEnvironment = Test-Path (Join-Path $root '.venv-gpu/Lib/site-packages/cupy')
$pythonOptions = if ($action -in @('encode','convert','multipart') -and
    ($device -eq 'cuda' -or ($device -eq 'auto' -and $gpuEnvironment))) {
    @('--gpu')
} elseif ($action -eq 'doctor' -and
    $gpuEnvironment) {
    # Route the inspection into the CUDA environment so it can report the
    # installed device instead of the CPU environment's expected no-CuPy.
    @('--device','cuda')
} else {
    @()
}
$launchArguments = @()
$launchArguments += $pythonOptions
$launchArguments += $runtimeArguments
$launchArguments += (Join-Path $root 'tools/cavc_cli.py')
$launchArguments += $cliArguments
$previousDevice = [Environment]::GetEnvironmentVariable('CBRSIM_DEVICE', 'Process')
try {
    $env:CBRSIM_DEVICE = $device
    & (Join-Path $root 'tools/python.ps1') @launchArguments
    $childExitCode = $LASTEXITCODE
} finally {
    if ($null -eq $previousDevice) {
        Remove-Item Env:CBRSIM_DEVICE -ErrorAction SilentlyContinue
    } else {
        [Environment]::SetEnvironmentVariable('CBRSIM_DEVICE', $previousDevice, 'Process')
    }
}
exit $childExitCode

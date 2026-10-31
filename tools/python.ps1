$Arguments = $args
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$childEnvironment = @{}
$processEnvironment = [Environment]::GetEnvironmentVariables([EnvironmentVariableTarget]::Process)
foreach ($name in $processEnvironment.Keys) {
    $childEnvironment[[string]$name] = [string]$processEnvironment[$name]
}
$gpuAvailable = Test-Path (Join-Path $root '.venv-gpu/Scripts/python.exe')
. (Join-Path $PSScriptRoot 'python_args.ps1')
$parsed = Get-CavcPythonArguments -InputArguments $Arguments -GpuEnvironmentAvailable $gpuAvailable
if ($parsed.Help) { Write-Output 'Options: --gpu --device cpu|auto|cuda --workspace-root DIR --scratch-root DIR --array-storage memory|disk'; exit 0 }
$gpu = $parsed.Gpu
$runtime = $parsed.Runtime
$argsList = $parsed.Arguments
if ($null -ne $parsed.Device) { $childEnvironment['CBRSIM_DEVICE'] = $parsed.Device }
$envDir = if ($gpu) { '.venv-gpu' } else { '.venv' }
$versionFile = if ($gpu) { '.python-version-gpu' } else { '.python-version' }
$python = Join-Path $root "$envDir/Scripts/python.exe"
if (-not (Test-Path $python)) { throw "Missing $envDir. Run tools\bootstrap_python.ps1 $(if($gpu){'--gpu'}else{'--cpu'})." }
$expected = (Get-Content -Raw (Join-Path $root $versionFile)).Trim()
$actual = (& $python -c 'import platform; print(platform.python_version())').Trim()
if ($LASTEXITCODE -ne 0 -or $actual -ne $expected) { throw "Expected Python $expected in $envDir, got $actual" }
$cfg = Get-Content (Join-Path $root "$envDir/pyvenv.cfg") -Raw
if ($cfg -match '(?im)^include-system-site-packages\s*=\s*true') { throw "Unsafe environment inherits system site-packages: $envDir" }
$childEnvironment['PYTHONPATH'] = (@((Join-Path $root 'tools'),$root,$childEnvironment['PYTHONPATH']) | Where-Object { $_ }) -join [IO.Path]::PathSeparator
if (-not $runtime.ContainsKey('--workspace-root')) { $runtime['--workspace-root'] = if($childEnvironment['CAVC_WORKSPACE_ROOT']){$childEnvironment['CAVC_WORKSPACE_ROOT']}elseif($childEnvironment['SEGACD_TMPFS_ROOT']){$childEnvironment['SEGACD_TMPFS_ROOT']}else{Join-Path $root 'tmp/workspace'} }
if (-not $runtime.ContainsKey('--scratch-root')) { $runtime['--scratch-root'] = if($childEnvironment['CAVC_SCRATCH_ROOT']){$childEnvironment['CAVC_SCRATCH_ROOT']}elseif($childEnvironment['TMPDIR']){$childEnvironment['TMPDIR']}else{Join-Path $runtime['--workspace-root'] 'scratch'} }
if (-not $runtime.ContainsKey('--array-storage')) { $runtime['--array-storage'] = if($childEnvironment['CAVC_ARRAY_STORAGE']){$childEnvironment['CAVC_ARRAY_STORAGE']}else{'memory'} }
$childEnvironment['CAVC_WORKSPACE_ROOT'] = [IO.Path]::GetFullPath($runtime['--workspace-root'])
$childEnvironment['CAVC_SCRATCH_ROOT'] = [IO.Path]::GetFullPath($runtime['--scratch-root'])
$childEnvironment['CAVC_ARRAY_STORAGE'] = $runtime['--array-storage']
if ($childEnvironment['CAVC_ARRAY_STORAGE'] -notin @('memory','disk')) { throw 'array storage must be memory or disk' }
New-Item -ItemType Directory -Force $childEnvironment['CAVC_WORKSPACE_ROOT'],$childEnvironment['CAVC_SCRATCH_ROOT'] | Out-Null
$childEnvironment['TMPDIR'] = $childEnvironment['CAVC_SCRATCH_ROOT']
$childEnvironment['TMP'] = $childEnvironment['CAVC_SCRATCH_ROOT']
$childEnvironment['TEMP'] = $childEnvironment['CAVC_SCRATCH_ROOT']
$childEnvironment['PYTHONIOENCODING'] = 'utf-8'
$childEnvironment['PYTHONUNBUFFERED'] = '1'
# Python's default text-file encoding follows the Windows locale unless UTF-8
# mode is enabled. Project reports and metadata contain non-ASCII text too.
$childEnvironment['PYTHONUTF8'] = '1'

function ConvertTo-WindowsCommandLineArgument {
    param([AllowEmptyString()][string]$Value)

    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }

    $quoted = [Text.StringBuilder]::new()
    [void]$quoted.Append([char]'"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]'\') {
            $backslashes++
            continue
        }
        if ($character -eq [char]'"') {
            [void]$quoted.Append([char]'\', (2 * $backslashes) + 1)
            [void]$quoted.Append([char]'"')
        } else {
            [void]$quoted.Append([char]'\', $backslashes)
            [void]$quoted.Append($character)
        }
        $backslashes = 0
    }
    [void]$quoted.Append([char]'\', 2 * $backslashes)
    [void]$quoted.Append([char]'"')
    return $quoted.ToString()
}

# Windows PowerShell 5.1 strips quotes from arguments passed with `&` and
# splatting. CreateProcess takes one command-line string, so quote each argv
# value using the Windows CRT parsing rules and launch without a shell.
$startInfo = [Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $python
$startInfo.UseShellExecute = $false
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$startInfo.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
$startInfo.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
foreach ($name in $childEnvironment.Keys) {
    $startInfo.EnvironmentVariables[[string]$name] = [string]$childEnvironment[$name]
}
$location = Get-Location
if ($location.Provider.Name -eq 'FileSystem') {
    $startInfo.WorkingDirectory = $location.ProviderPath
}
$quotedArguments = foreach ($argument in $argsList) {
    ConvertTo-WindowsCommandLineArgument ([string]$argument)
}
$startInfo.Arguments = $quotedArguments -join ' '
$process = [Diagnostics.Process]::new()
$process.StartInfo = $startInfo
$processStarted = $false
try {
    if (-not $process.Start()) { throw 'Could not start the selected Python interpreter' }
    $processStarted = $true
    $stdoutOpen = $true
    $stderrOpen = $true
    $stdoutTask = $process.StandardOutput.ReadLineAsync()
    $stderrTask = $process.StandardError.ReadLineAsync()
    while ($stdoutOpen -or $stderrOpen) {
        $receivedLine = $false
        if ($stdoutOpen -and $stdoutTask.IsCompleted) {
            $line = $stdoutTask.GetAwaiter().GetResult()
            if ($null -eq $line) {
                $stdoutOpen = $false
            } else {
                Write-Output -NoEnumerate $line
                $stdoutTask = $process.StandardOutput.ReadLineAsync()
            }
            $receivedLine = $true
        }
        if ($stderrOpen -and $stderrTask.IsCompleted) {
            $line = $stderrTask.GetAwaiter().GetResult()
            if ($null -eq $line) {
                $stderrOpen = $false
            } else {
                # Write-Error turns normal child diagnostics (including test
                # progress on stderr) into PowerShell error records. Preserve
                # the child's stream without reclassifying it as a failure.
                [Console]::Error.WriteLine($line)
                $stderrTask = $process.StandardError.ReadLineAsync()
            }
            $receivedLine = $true
        }
        if (-not $receivedLine) { Start-Sleep -Milliseconds 20 }
    }
    $process.WaitForExit()
    $pythonExitCode = $process.ExitCode
} finally {
    if ($processStarted -and -not $process.HasExited) {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $process.Id /T /F *> $null
        $process.WaitForExit()
    }
    $process.Dispose()
}
exit $pythonExitCode

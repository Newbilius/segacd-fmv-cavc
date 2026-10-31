param(
    [Parameter(Mandatory=$true)][string]$CommandPattern,
    [Parameter(Mandatory=$true)][string]$WorkspaceRoot,
    [Parameter(Mandatory=$true)][string]$ScratchRoot,
    [Parameter(Mandatory=$true)][string]$OutputTsv
)

$ErrorActionPreference = 'Stop'
$culture = [Globalization.CultureInfo]::InvariantCulture
$outputPath = [IO.Path]::GetFullPath($OutputTsv)
$workspacePath = [IO.Path]::GetFullPath($WorkspaceRoot)
$scratchPath = [IO.Path]::GetFullPath($ScratchRoot)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($outputPath)) | Out-Null
[IO.File]::WriteAllText(
    $outputPath,
    "utc`tprocess_count`tworking_set_bytes`tprivate_bytes`tcpu_seconds`tgpu_used_mib`tworkspace_bytes`tscratch_bytes`n",
    [Text.UTF8Encoding]::new($false))

function Get-DirectoryBytes([string]$Path) {
    if (-not [IO.Directory]::Exists($Path)) { return [int64]0 }
    $total = [int64]0
    foreach ($file in [IO.Directory]::EnumerateFiles(
            $Path, '*', [IO.SearchOption]::AllDirectories)) {
        try { $total += ([IO.FileInfo]::new($file)).Length }
        catch [IO.IOException] { }
        catch [UnauthorizedAccessException] { }
    }
    return $total
}

$rootPid = 0
$seenRoot = $false
$lastDiskSample = [DateTime]::MinValue
$workspaceBytes = [int64]0
$scratchBytes = [int64]0
$maxWorkingSet = [int64]0
$maxPrivate = [int64]0
$maxGpu = $null
$gpuSamples = 0
$maxWorkspace = [int64]0
$maxScratch = [int64]0
$maxProcesses = 0
$samples = 0
$started = [DateTime]::UtcNow

while ($true) {
    $processes = @(Get-CimInstance Win32_Process)
    if (-not $seenRoot) {
        $root = $processes |
            Where-Object { $_.Name -eq 'python.exe' -and
                $_.CommandLine -match $CommandPattern } |
            Sort-Object CreationDate |
            Select-Object -First 1
        if ($root) {
            $rootPid = [int]$root.ProcessId
            $seenRoot = $true
        } else {
            Start-Sleep -Seconds 1
            continue
        }
    }

    $ids = [System.Collections.Generic.HashSet[int]]::new()
    [void]$ids.Add($rootPid)
    do {
        $changed = $false
        foreach ($process in $processes) {
            if ($ids.Contains([int]$process.ParentProcessId) -and
                $ids.Add([int]$process.ProcessId)) {
                $changed = $true
            }
        }
    } while ($changed)
    $tree = @($processes | Where-Object {
        $ids.Contains([int]$_.ProcessId)
    })
    if (-not $tree.Count) { break }

    $workingSet = [int64](($tree |
        Measure-Object WorkingSetSize -Sum).Sum)
    $liveProcesses = @()
    $cpuSeconds = 0.0
    foreach ($process in $tree) {
        $live = Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
        if ($live) {
            $liveProcesses += $live
            $cpuSeconds += [double]$live.CPU
        }
    }
    $workingSet = [int64](($liveProcesses |
        Measure-Object WorkingSet64 -Sum).Sum)
    $privateBytes = [int64](($liveProcesses |
        Measure-Object PrivateMemorySize64 -Sum).Sum)

    $gpuUsed = ''
    $gpuLines = @(& nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>$null)
    $gpuMatch = if ($LASTEXITCODE -eq 0 -and $gpuLines.Count -gt 0) {
        [regex]::Match([string]$gpuLines[0], '^\s*(\d+)\s*$')
    } else { $null }
    if ($gpuMatch -and $gpuMatch.Success) {
        $gpuUsed = [int64]$gpuMatch.Groups[1].Value
        $gpuSamples++
        if ($null -eq $maxGpu -or $gpuUsed -gt $maxGpu) { $maxGpu = $gpuUsed }
    }

    $now = [DateTime]::UtcNow
    if (($now - $lastDiskSample).TotalSeconds -ge 30) {
        $workspaceBytes = Get-DirectoryBytes $workspacePath
        $scratchBytes = Get-DirectoryBytes $scratchPath
        $lastDiskSample = $now
        if ($workspaceBytes -gt $maxWorkspace) { $maxWorkspace = $workspaceBytes }
        if ($scratchBytes -gt $maxScratch) { $maxScratch = $scratchBytes }
    }

    if ($workingSet -gt $maxWorkingSet) { $maxWorkingSet = $workingSet }
    if ($privateBytes -gt $maxPrivate) { $maxPrivate = $privateBytes }
    if ($tree.Count -gt $maxProcesses) { $maxProcesses = $tree.Count }
    $samples++
    $row = @(
        $now.ToString('o', $culture),
        $tree.Count.ToString($culture),
        $workingSet.ToString($culture),
        $privateBytes.ToString($culture),
        $cpuSeconds.ToString('F2', $culture),
        [string]$gpuUsed,
        $workspaceBytes.ToString($culture),
        $scratchBytes.ToString($culture)
    ) -join "`t"
    [IO.File]::AppendAllText($outputPath, $row + "`n", [Text.UTF8Encoding]::new($false))
    Start-Sleep -Seconds 5
}

$summary = [ordered]@{
    command_pattern = $CommandPattern
    root_pid = $rootPid
    started_at_utc = $started.ToString('o', $culture)
    ended_at_utc = [DateTime]::UtcNow.ToString('o', $culture)
    sample_interval_seconds = 5
    samples = $samples
    process_tree_working_set_peak_bytes = $maxWorkingSet
    process_tree_private_memory_size_peak_bytes = $maxPrivate
    process_tree_count_peak = $maxProcesses
    device_wide_gpu_memory_peak_mib = $maxGpu
    device_wide_gpu_sample_count = $gpuSamples
    workspace_bytes_peak_sampled = $maxWorkspace
    scratch_bytes_peak_sampled = $maxScratch
    tsv = $outputPath
}
$jsonPath = [IO.Path]::ChangeExtension($outputPath, '.json')
[IO.File]::WriteAllText(
    $jsonPath,
    ($summary | ConvertTo-Json -Depth 4) + "`n",
    [Text.UTF8Encoding]::new($false))
Write-Output "Windows run sampling complete: $jsonPath"

param(
    [Parameter(Mandatory=$true)][int]$ProcessId,
    [Parameter(Mandatory=$true)][string]$OutputTsv
)

$ErrorActionPreference = 'Stop'
$culture = [Globalization.CultureInfo]::InvariantCulture
$outputPath = [IO.Path]::GetFullPath($OutputTsv)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($outputPath)) | Out-Null
[IO.File]::WriteAllText($outputPath, "utc`tdevice_wide_gpu_memory_mib`tutilization_percent`n", [Text.UTF8Encoding]::new($false))
$peak = $null
$samples = 0
while (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
    $raw = @(& nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader,nounits 2>$null)
    if ($LASTEXITCODE -eq 0 -and $raw.Count -gt 0 -and
        [string]$raw[0] -match '^\s*(\d+)\s*,\s*(\d+)\s*$') {
        $memory = [int64]$Matches[1]
        $utilization = [int]$Matches[2]
        if ($null -eq $peak -or $memory -gt $peak) { $peak = $memory }
        $line = @([DateTime]::UtcNow.ToString('o',$culture),$memory,$utilization) -join "`t"
        [IO.File]::AppendAllText($outputPath,$line+"`n",[Text.UTF8Encoding]::new($false))
        $samples++
    }
    Start-Sleep -Seconds 5
}
$summary = [ordered]@{ process_id=$ProcessId; samples=$samples; device_wide_gpu_memory_peak_mib=$peak; tsv=$outputPath }
[IO.File]::WriteAllText([IO.Path]::ChangeExtension($outputPath,'.json'),($summary|ConvertTo-Json -Depth 3)+"`n",[Text.UTF8Encoding]::new($false))

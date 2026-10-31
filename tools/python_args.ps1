function Get-CavcPythonArguments {
    param(
        [string[]]$InputArguments,
        [bool]$GpuEnvironmentAvailable
    )

    $gpu = $false
    $device = $null
    $runtime = @{}
    $forward = [System.Collections.Generic.List[string]]::new()
    for ($i=0; $i -lt $InputArguments.Count; $i++) {
        $arg = $InputArguments[$i]
        if ($arg -eq '--gpu') { $gpu = $true; continue }
        if ($arg -eq '--device') {
            if (++$i -ge $InputArguments.Count) { throw '--device requires cpu, auto, or cuda' }
            if ($InputArguments[$i] -notin @('cpu','auto','cuda')) { throw 'device must be cpu, auto, or cuda' }
            $device = $InputArguments[$i]
            $gpu = $device -eq 'cuda' -or ($device -eq 'auto' -and $GpuEnvironmentAvailable)
            continue
        }
        if ($arg -in @('--workspace-root','--scratch-root','--array-storage')) {
            if (++$i -ge $InputArguments.Count) { throw "$arg requires a value" }
            $runtime[$arg] = $InputArguments[$i]
            continue
        }
        if ($arg -eq '--help-runtime') {
            return [pscustomobject]@{ Help=$true; Gpu=$gpu; Device=$device; Runtime=$runtime; Arguments=@() }
        }

        # Wrapper options end at the command; preserve every child argument.
        for ($rest=$i; $rest -lt $InputArguments.Count; $rest++) {
            $forward.Add($InputArguments[$rest])
        }
        break
    }
    return [pscustomobject]@{
        Help=$false; Gpu=$gpu; Device=$device; Runtime=$runtime
        Arguments=$forward.ToArray()
    }
}

param(
    [ValidateSet("Install", "Remove", "Status")]
    [string]$Mode = "Install",
    [ValidatePattern("^([01]?\d|2[0-3]):[0-5]\d$")]
    [string]$At = "07:00"
)

$ErrorActionPreference = "Stop"
$taskName = "JohnySkore-Daily-Scout"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$runnerPath = Join-Path $repositoryRoot "Spustit_Patraci_Agenty.bat"

if ($Mode -eq "Status") {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Write-Host "[INFO] Uloha $taskName neni nainstalovana."
        exit 1
    }
    Get-ScheduledTaskInfo -TaskName $taskName | Format-List
    exit 0
}

if ($Mode -eq "Remove") {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    Write-Host "[OK] Uloha $taskName byla odstranena."
    exit 0
}

if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) {
    throw "Runner nebyl nalezen: $runnerPath"
}

$currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction `
    -Execute $env:ComSpec `
    -Argument "/d /c `"$runnerPath`"" `
    -WorkingDirectory $repositoryRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentIdentity `
    -LogonType Interactive `
    -RunLevel Limited
$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "JohnySkore daily SEC filing scout; read-only data collection"

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
Write-Host "[OK] $taskName pobezi denne v $At a dobehne po zmeskanem startu."

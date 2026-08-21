param(
    [ValidateSet("Install", "Remove", "Status")]
    [string]$Mode = "Install",
    [ValidatePattern("^([01]?\d|2[0-3]):[0-5]\d$")]
    [string]$At = "06:30"
)

$ErrorActionPreference = "Stop"
$taskName = "JohnySkore-Weekly-Shadow"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$runnerPath = Join-Path $repositoryRoot "Spustit_Tydenni_Shadow.bat"

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
    if ($null -eq $task) {
        Write-Host "[INFO] Uloha $taskName uz neexistuje."
        exit 0
    }
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
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
$trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday -At $At
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6)
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentIdentity `
    -LogonType Interactive `
    -RunLevel Limited
$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "JohnySkore v2.1 persistent weekly Stage 4 shadow collection"

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
$registered = Get-ScheduledTask -TaskName $taskName
if ($registered.State -eq "Disabled") {
    Enable-ScheduledTask -TaskName $taskName | Out-Null
}
Write-Host "[OK] $taskName pobezi kazde pondeli v $At a dobehne po zmeskanem startu."

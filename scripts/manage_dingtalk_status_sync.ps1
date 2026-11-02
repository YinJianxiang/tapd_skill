[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet("Install", "Status", "Pause", "Resume", "Remove")]
    [string]$Action = "Install",
    [ValidatePattern('^([01][0-9]|2[0-3]):[0-5][0-9]$')]
    [string]$DailyAt = "19:00",
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$hash = [System.Security.Cryptography.SHA256]::Create()
try {
    $rootBytes = [System.Text.Encoding]::UTF8.GetBytes($projectRoot.ToLowerInvariant())
    $suffix = ([BitConverter]::ToString($hash.ComputeHash($rootBytes))).Replace("-", "").Substring(0, 10)
} finally { $hash.Dispose() }
$taskName = "TAPD-DingTalk-StatusSync-$suffix"

switch ($Action) {
    "Install" {
        $runner = Join-Path $PSScriptRoot "run_dingtalk_status_sync.ps1"
        $config = Join-Path $projectRoot ".tapd\dingtalk_status_sync.ps1"
        if (-not (Test-Path -LiteralPath $config -PathType Leaf)) {
            throw "Configure .tapd/dingtalk_status_sync.ps1 before installing the task."
        }
        $pythonCommand = Get-Command $PythonExecutable -CommandType Application -ErrorAction Stop | Select-Object -First 1
        $pythonPath = (& $pythonCommand.Source -c "import sys; print(sys.executable)").Trim()
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
            throw "Cannot resolve Python interpreter."
        }
        Get-Command dws -ErrorAction Stop | Out-Null
        if ($runner.Contains('"') -or $pythonPath.Contains('"')) {
            throw "Paths cannot contain quote characters."
        }
        $timezone = [TimeZoneInfo]::Local
        if ($timezone.BaseUtcOffset -ne [TimeSpan]::FromHours(8) -or $timezone.SupportsDaylightSavingTime) {
            throw "This task requires Windows time zone UTC+08:00 without daylight saving, matching Asia/Shanghai. No system time zone was changed."
        }
        if ($PSCmdlet.ShouldProcess($taskName, "Install daily TAPD updates at $DailyAt (Asia/Shanghai); DingTalk stays read-only")) {
            $account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
            $powershellPath = Join-Path $PSHOME "powershell.exe"
            if (-not (Test-Path -LiteralPath $powershellPath)) {
                $powershellPath = Join-Path $PSHOME "pwsh.exe"
            }
            $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Apply -PythonExecutable "{1}"' -f $runner, $pythonPath
            $taskAction = New-ScheduledTaskAction -Execute $powershellPath -Argument $arguments -WorkingDirectory $projectRoot
            $runAt = [DateTime]::Today.Add([TimeSpan]::Parse($DailyAt))
            $trigger = New-ScheduledTaskTrigger -Daily -At $runAt
            $principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
            $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)
            Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $trigger -Principal $principal -Settings $settings -Description "Read configured user's DingTalk records; create/update TAPD QA Stories only." -Force | Out-Null
            Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
            Write-Output "Installed. Runs daily at $DailyAt (Asia/Shanghai) while this user is logged in. Missed runs are not caught up."
            Write-Output "Latest result: .tapd/dingtalk_status_sync_last_result.json"
        }
    }
    "Status" {
        Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
        Get-ScheduledTaskInfo -TaskName $taskName | Select-Object LastRunTime, LastTaskResult, NextRunTime
    }
    "Pause" {
        if ($PSCmdlet.ShouldProcess($taskName, "Disable future runs (an active run may finish)")) {
            Disable-ScheduledTask -TaskName $taskName | Out-Null
            Write-Output "Paused future runs. An active sync may still finish."
        }
    }
    "Resume" {
        if ($PSCmdlet.ShouldProcess($taskName, "Enable automatic TAPD status synchronization")) {
            Enable-ScheduledTask -TaskName $taskName | Out-Null
            Write-Output "Resumed."
        }
    }
    "Remove" {
        if ($PSCmdlet.ShouldProcess($taskName, "Remove scheduled task (an active run may finish)")) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
            Write-Output "Removed scheduled task. Local configuration and results are preserved."
        }
    }
}

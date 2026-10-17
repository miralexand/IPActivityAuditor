[CmdletBinding()]
param(
    [string]$ExePath = (Join-Path $PSScriptRoot "dist\IPActivityAuditor.exe"),
    [string]$ConfigPath,
    [string]$TaskName = "IPActivityAuditor",
    [string]$Time = "23:30"
)

$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$windowsPrincipal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $windowsPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script as Administrator."
}

if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "Executable not found: $ExePath"
}
$exe = (Resolve-Path -LiteralPath $ExePath).Path
$exeDir = Split-Path -Parent $exe

if (-not $ConfigPath) {
    $localConfig = Join-Path $exeDir "config.ini"
    $installedConfig = Join-Path $env:ProgramData "IPActivityAuditor\config.ini"
    if (Test-Path -LiteralPath $localConfig) {
        $ConfigPath = $localConfig
    }
    elseif (Test-Path -LiteralPath $installedConfig) {
        $ConfigPath = $installedConfig
    }
    else {
        $ConfigPath = $localConfig
    }
}
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Config file not found: $ConfigPath. Copy config.ini.example to config.ini first."
}
$config = (Resolve-Path -LiteralPath $ConfigPath).Path

$offset = [TimeSpan]::Zero
if (-not [TimeSpan]::TryParse($Time, [ref]$offset)) {
    throw "Invalid time: $Time. Use HH:mm, for example 23:30."
}
$at = (Get-Date).Date.Add($offset)

$action = New-ScheduledTaskAction `
    -Execute $exe `
    -Argument "--config `"$config`"" `
    -WorkingDirectory $exeDir

$trigger = New-ScheduledTaskTrigger -Daily -At $at

$taskPrincipal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $taskPrincipal `
    -Settings $settings `
    -Description "Daily intranet IP activity audit (ARP scan)." `
    -Force | Out-Null

Write-Host "Scheduled task '$TaskName' created."
Write-Host "Runs daily at $Time with highest privileges."
Write-Host "Executable: $exe"
Write-Host "Config:     $config"

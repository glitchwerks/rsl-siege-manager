#Requires -Version 5.1
<#
.SYNOPSIS
    Read-only, sanitized inspection of the isolated rehearsal SMB share.
.DESCRIPTION
    Run in an elevated 64-bit PowerShell session on the Windows NAS.
    Prints one JSON report without paths, account details, credentials, or ACLs.
#>
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$')]
    [string]$ShareName,
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_-]{0,19}$')]
    [string]$AccountName,
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$')]
    [string]$ProductionShareName,
    [switch]$ConfirmReadOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-AccountName {
    param([string]$Identity, [string]$Expected, [string]$LocalComputer)
    $parts = $Identity -split '\\'
    return ($parts.Count -eq 2 -and $parts[0] -ieq $LocalComputer -and
            $parts[1] -ieq $Expected)
}

function Test-BroadPrincipal {
    param([string]$Identity)
    $name = ($Identity -split '\\')[-1]
    return $name -in @('Everyone', 'Authenticated Users', 'Users', 'Guests', 'Guest',
                       'S-1-1-0', 'S-1-5-11', 'S-1-5-32-545', 'S-1-5-32-546')
}

function Test-SeparateDirectory {
    param([string]$RehearsalPath, [string]$ProductionPath)
    $separators = [char[]]@('\', '/')
    $rehearsal = [System.IO.Path]::GetFullPath($RehearsalPath).TrimEnd($separators)
    $production = [System.IO.Path]::GetFullPath($ProductionPath).TrimEnd($separators)
    $comparison = [System.StringComparison]::OrdinalIgnoreCase
    return -not (
        $rehearsal.Equals($production, $comparison) -or
        $rehearsal.StartsWith($production + '\', $comparison) -or
        $production.StartsWith($rehearsal + '\', $comparison)
    )
}

function Invoke-RehearsalNasInspection {
    param([string]$RehearsalShare, [string]$RehearsalAccount, [string]$ProductionShare)
    $checks = [ordered]@{
        distinct_names = $RehearsalShare -ine $ProductionShare
        share_exists = $false
        production_share_exists = $false
        backing_directories_exist = $false
        backing_directories_separate = $false
        share_encryption_required = $false
        local_account_exists_enabled = $false
        account_password_required = $false
        direct_rehearsal_share_write = $false
        no_rehearsal_account_on_production_share = $false
        no_broad_rehearsal_share_grant = $false
        no_broad_production_share_grant = $false
        rehearsal_ntfs_read_write = $false
    }
    try {
        if (-not $checks.distinct_names) { throw 'same_share' }
        $share = Get-SmbShare -Name $RehearsalShare -ErrorAction Stop
        $production = Get-SmbShare -Name $ProductionShare -ErrorAction Stop
        $checks.share_exists = $null -ne $share
        $checks.production_share_exists = $null -ne $production
        if (-not ($checks.share_exists -and $checks.production_share_exists)) { throw 'missing_share' }
        $checks.backing_directories_exist = (
            (Test-Path -LiteralPath $share.Path -PathType Container) -and
            (Test-Path -LiteralPath $production.Path -PathType Container)
        )
        $checks.backing_directories_separate = Test-SeparateDirectory $share.Path $production.Path
        $checks.share_encryption_required = $share.EncryptData -eq $true
        $account = Get-LocalUser -Name $RehearsalAccount -ErrorAction Stop
        $checks.local_account_exists_enabled = $null -ne $account -and $account.Enabled -eq $true
        $checks.account_password_required = $null -ne $account -and $account.PasswordRequired -eq $true

        $shareAccess = @(Get-SmbShareAccess -Name $RehearsalShare -ErrorAction Stop)
        $productionAccess = @(Get-SmbShareAccess -Name $ProductionShare -ErrorAction Stop)
        $checks.direct_rehearsal_share_write = @($shareAccess | Where-Object {
            $_.AccessControlType -eq 'Allow' -and
            $_.AccessRight -in @('Change', 'Full') -and
            (Test-AccountName ([string]$_.AccountName) $RehearsalAccount $env:COMPUTERNAME)
        }).Count -gt 0
        $checks.no_rehearsal_account_on_production_share = @($productionAccess | Where-Object {
            Test-AccountName ([string]$_.AccountName) $RehearsalAccount $env:COMPUTERNAME
        }).Count -eq 0
        $checks.no_broad_rehearsal_share_grant = @($shareAccess | Where-Object {
            $_.AccessControlType -eq 'Allow' -and
            (Test-BroadPrincipal ([string]$_.AccountName))
        }).Count -eq 0
        $checks.no_broad_production_share_grant = @($productionAccess | Where-Object {
            $_.AccessControlType -eq 'Allow' -and
            (Test-BroadPrincipal ([string]$_.AccountName))
        }).Count -eq 0

        if ($checks.backing_directories_exist) {
            $acl = Get-Acl -LiteralPath $share.Path -ErrorAction Stop
            $read = [int][System.Security.AccessControl.FileSystemRights]::ReadData
            $write = [int][System.Security.AccessControl.FileSystemRights]::WriteData
            $checks.rehearsal_ntfs_read_write = @($acl.Access | Where-Object {
                $rights = [int]$_.FileSystemRights
                $_.AccessControlType -eq 'Allow' -and
                (Test-AccountName ([string]$_.IdentityReference) $RehearsalAccount $env:COMPUTERNAME) -and
                ($rights -band $read) -ne 0 -and ($rights -band $write) -ne 0
            }).Count -gt 0
        }
    } catch {
        $checks.inspection_completed = $false
    }
    $passed = @($checks.Values | Where-Object { $_ -ne $true }).Count -eq 0
    return [ordered]@{
        schema = 1
        phase = 'rehearsal-nas-readonly'
        status = $(if ($passed) { 'PASS' } else { 'STOP' })
        checks = $checks
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    if (-not $ConfirmReadOnly) {
        $report = @{ schema = 1; phase = 'rehearsal-nas-readonly'; status = 'STOP';
                     checks = @{ explicit_readonly_confirmation = $false } }
    } else {
        $report = Invoke-RehearsalNasInspection $ShareName $AccountName $ProductionShareName
    }
    $report | ConvertTo-Json -Compress -Depth 5
    if ($report.status -ne 'PASS') { exit 1 }
}

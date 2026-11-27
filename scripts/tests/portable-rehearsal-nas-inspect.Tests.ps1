#Requires -Version 5.1
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }

BeforeAll {
    $script:OriginalComputerName = $env:COMPUTERNAME
    $env:COMPUTERNAME = 'SERVER-NAS'
    . (Join-Path $PSScriptRoot '..\portable-rehearsal-nas-inspect.ps1') `
        -ShareName rsl_rehearsal -AccountName rsl_rehearsal `
        -ProductionShareName share
}

AfterAll {
    $env:COMPUTERNAME = $script:OriginalComputerName
}

Describe 'Rehearsal NAS read-only inspection' {
    It 'recognizes a direct local-account grant' {
        Test-AccountName 'SERVER-NAS\rsl_rehearsal' 'rsl_rehearsal' 'SERVER-NAS' | Should -BeTrue
        Test-AccountName 'SERVER-NAS\production_account' 'rsl_rehearsal' 'SERVER-NAS' | Should -BeFalse
        Test-AccountName 'OTHER\rsl_rehearsal' 'rsl_rehearsal' 'SERVER-NAS' | Should -BeFalse
    }

    It 'recognizes broad share principals' {
        Test-BroadPrincipal 'BUILTIN\Users' | Should -BeTrue
        Test-BroadPrincipal 'Everyone' | Should -BeTrue
        Test-BroadPrincipal 'SERVER-NAS\Administrators' | Should -BeFalse
    }

    It 'rejects an overlapping production backing directory' {
        Test-SeparateDirectory 'C:\data\rehearsal' 'C:\data\production' | Should -BeTrue
        Test-SeparateDirectory 'C:\data\production\rehearsal' 'C:\data\production' | Should -BeFalse
    }

    It 'reports PASS only for a separate encrypted share and dedicated account' {
        Mock Get-SmbShare {
            if ($Name -eq 'rsl_rehearsal') {
                [pscustomobject]@{ Path = 'C:\data\rehearsal'; EncryptData = $true }
            } else {
                [pscustomobject]@{ Path = 'C:\data\production'; EncryptData = $true }
            }
        }
        Mock Get-LocalUser {
            [pscustomobject]@{ Enabled = $true; PasswordRequired = $true }
        }
        Mock Get-SmbShareAccess {
            if ($Name -eq 'rsl_rehearsal') {
                [pscustomobject]@{ AccountName = 'SERVER-NAS\rsl_rehearsal';
                                   AccessControlType = 'Allow'; AccessRight = 'Change' }
            } else {
                [pscustomobject]@{ AccountName = 'SERVER-NAS\production_account';
                                   AccessControlType = 'Allow'; AccessRight = 'Change' }
            }
        }
        Mock Test-Path { $true }
        Mock Get-Acl {
            [pscustomobject]@{ Access = @([pscustomobject]@{
                IdentityReference = 'SERVER-NAS\rsl_rehearsal'
                AccessControlType = 'Allow'
                FileSystemRights = ([System.Security.AccessControl.FileSystemRights]::ReadData -bor
                                    [System.Security.AccessControl.FileSystemRights]::WriteData)
            }) }
        }
        $report = Invoke-RehearsalNasInspection rsl_rehearsal rsl_rehearsal share
        $report.status | Should -Be 'PASS'
        ($report | ConvertTo-Json -Compress -Depth 5) | Should -Not -Match 'C:\\data'
    }

    It 'stops when the account can write to the production share' {
        Mock Get-SmbShare {
            if ($Name -eq 'rsl_rehearsal') {
                [pscustomobject]@{ Path = 'C:\data\rehearsal'; EncryptData = $true }
            } else {
                [pscustomobject]@{ Path = 'C:\data\production'; EncryptData = $true }
            }
        }
        Mock Get-LocalUser {
            [pscustomobject]@{ Enabled = $true; PasswordRequired = $true }
        }
        Mock Get-SmbShareAccess {
            [pscustomobject]@{ AccountName = 'SERVER-NAS\rsl_rehearsal';
                               AccessControlType = 'Allow'; AccessRight = 'Change' }
        }
        Mock Test-Path { $true }
        Mock Get-Acl {
            [pscustomobject]@{ Access = @([pscustomobject]@{
                IdentityReference = 'SERVER-NAS\rsl_rehearsal'
                AccessControlType = 'Allow'
                FileSystemRights = ([System.Security.AccessControl.FileSystemRights]::ReadData -bor
                                    [System.Security.AccessControl.FileSystemRights]::WriteData)
            }) }
        }
        $report = Invoke-RehearsalNasInspection rsl_rehearsal rsl_rehearsal share
        $report.status | Should -Be 'STOP'
        $report.checks.no_rehearsal_account_on_production_share | Should -BeFalse
    }

    It 'stops when the production share allows broad access' {
        Mock Get-SmbShare {
            if ($Name -eq 'rsl_rehearsal') {
                [pscustomobject]@{ Path = 'C:\data\rehearsal'; EncryptData = $true }
            } else {
                [pscustomobject]@{ Path = 'C:\data\production'; EncryptData = $true }
            }
        }
        Mock Get-LocalUser {
            [pscustomobject]@{ Enabled = $true; PasswordRequired = $true }
        }
        Mock Get-SmbShareAccess {
            if ($Name -eq 'rsl_rehearsal') {
                [pscustomobject]@{ AccountName = 'SERVER-NAS\rsl_rehearsal';
                                   AccessControlType = 'Allow'; AccessRight = 'Change' }
            } else {
                [pscustomobject]@{ AccountName = 'Everyone';
                                   AccessControlType = 'Allow'; AccessRight = 'Read' }
            }
        }
        Mock Test-Path { $true }
        Mock Get-Acl {
            [pscustomobject]@{ Access = @([pscustomobject]@{
                IdentityReference = 'SERVER-NAS\rsl_rehearsal'
                AccessControlType = 'Allow'
                FileSystemRights = ([System.Security.AccessControl.FileSystemRights]::ReadData -bor
                                    [System.Security.AccessControl.FileSystemRights]::WriteData)
            }) }
        }
        $report = Invoke-RehearsalNasInspection rsl_rehearsal rsl_rehearsal share
        $report.status | Should -Be 'STOP'
        $report.checks.no_broad_production_share_grant | Should -BeFalse
    }
}

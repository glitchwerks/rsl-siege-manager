#Requires -Version 5.1
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }

BeforeAll {
    $script:OriginalComputerName = $env:COMPUTERNAME
    $env:COMPUTERNAME = 'SERVER-NAS'
    . (Join-Path $PSScriptRoot '..\portable-rehearsal-nas-inspect.ps1') `
        -ShareName rsl_rehearsal -AccountName rsl_rehearsal `
        -ProductionAccountName production_account `
        -ProductionShareName share
}

AfterAll {
    $env:COMPUTERNAME = $script:OriginalComputerName
}

Describe 'Rehearsal NAS read-only inspection' {
    BeforeEach {
        Mock Resolve-PrincipalSid {
            switch ($Identity) {
                'Everyone' { 'S-1-1-0' }
                'Jeder' { 'S-1-1-0' }
                'BUILTIN\Users' { 'S-1-5-32-545' }
                'SERVER-NAS\Administrators' { 'S-1-5-32-544' }
                'SERVER-NAS\rsl_rehearsal' { 'S-1-5-21-1-2-3-1001' }
                'SERVER-NAS\production_account' { 'S-1-5-21-1-2-3-1002' }
                default { throw 'identity_resolution_failed' }
            }
        }
        Mock Get-Item {
            [pscustomobject]@{ Attributes = [System.IO.FileAttributes]::Directory }
        }
    }

    It 'accepts an unused separate backing path before setup' {
        Mock Get-SmbShare {
            if ($Name -eq 'share') { [pscustomobject]@{ Path = 'C:\data\production' } }
        }
        Mock Get-LocalUser { $null }
        Mock Get-SmbShareAccess {
            [pscustomobject]@{ AccountName = 'SERVER-NAS\production_account';
                               AccessControlType = 'Allow'; AccessRight = 'Change' }
        }
        Mock Test-Path {
            if ($LiteralPath -eq 'C:\data\rehearsal') { return $false }
            return $true
        }
        $report = Invoke-RehearsalNasBaseline rsl_rehearsal rsl_rehearsal share production_account 'C:\data\rehearsal'
        $report.status | Should -Be 'PASS'
        ($report | ConvertTo-Json -Compress -Depth 5) | Should -Not -Match 'C:\\data'
    }

    It 'stops when the proposed backing path overlaps production' {
        Mock Get-SmbShare {
            if ($Name -eq 'share') { [pscustomobject]@{ Path = 'C:\data\production' } }
        }
        Mock Get-LocalUser { $null }
        Mock Get-SmbShareAccess { @() }
        Mock Test-Path { $true }
        $report = Invoke-RehearsalNasBaseline rsl_rehearsal rsl_rehearsal share production_account 'C:\data\production\rehearsal'
        $report.status | Should -Be 'STOP'
        $report.checks.backing_directories_separate | Should -BeFalse
    }

    It 'stops when the proposed share name is already in use' {
        Mock Get-SmbShare {
            if ($Name -eq 'share') {
                [pscustomobject]@{ Path = 'C:\data\production' }
            } else {
                [pscustomobject]@{ Path = 'C:\data\other' }
            }
        }
        Mock Get-LocalUser { $null }
        Mock Get-SmbShareAccess { @() }
        Mock Test-Path {
            if ($LiteralPath -eq 'C:\data\rehearsal') { return $false }
            return $true
        }
        $report = Invoke-RehearsalNasBaseline rsl_rehearsal rsl_rehearsal share production_account 'C:\data\rehearsal'
        $report.status | Should -Be 'STOP'
        $report.checks.rehearsal_share_unused | Should -BeFalse
    }

    It 'stops on a drive-relative proposed backing path' {
        $report = Invoke-RehearsalNasBaseline rsl_rehearsal rsl_rehearsal share production_account 'C:rehearsal'
        $report.status | Should -Be 'STOP'
        $report.checks.proposed_path_absolute | Should -BeFalse
    }

    It 'stops when a proposed backing path traverses a junction' {
        Mock Get-SmbShare {
            if ($Name -eq 'share') { [pscustomobject]@{ Path = 'C:\data\production' } }
        }
        Mock Get-LocalUser { $null }
        Mock Get-SmbShareAccess { @() }
        Mock Test-Path {
            if ($LiteralPath -eq 'C:\alias\rehearsal') { return $false }
            return $true
        }
        Mock Get-Item {
            if ($LiteralPath -eq 'C:\alias') {
                [pscustomobject]@{ Attributes = [System.IO.FileAttributes]::ReparsePoint }
            } else {
                [pscustomobject]@{ Attributes = [System.IO.FileAttributes]::Directory }
            }
        }
        $report = Invoke-RehearsalNasBaseline rsl_rehearsal rsl_rehearsal share production_account 'C:\alias\rehearsal'
        $report.status | Should -Be 'STOP'
        $report.checks.backing_paths_no_reparse | Should -BeFalse
    }

    It 'recognizes a direct local-account grant' {
        Test-AccountName 'SERVER-NAS\rsl_rehearsal' 'rsl_rehearsal' 'SERVER-NAS' | Should -BeTrue
        Test-AccountName 'SERVER-NAS\production_account' 'rsl_rehearsal' 'SERVER-NAS' | Should -BeFalse
        Test-AccountName 'OTHER\rsl_rehearsal' 'rsl_rehearsal' 'SERVER-NAS' | Should -BeFalse
    }

    It 'recognizes broad share principals' {
        Test-BroadPrincipal 'BUILTIN\Users' | Should -BeTrue
        Test-BroadPrincipal 'Everyone' | Should -BeTrue
        Test-BroadPrincipal 'Jeder' | Should -BeTrue
        Test-BroadPrincipal 'SERVER-NAS\Administrators' | Should -BeFalse
    }

    It 'stops when a share principal cannot be resolved to a SID' {
        Mock Get-SmbShare {
            if ($Name -eq 'share') { [pscustomobject]@{ Path = 'C:\data\production' } }
        }
        Mock Get-LocalUser { $null }
        Mock Get-SmbShareAccess {
            [pscustomobject]@{ AccountName = 'UNKNOWN\group';
                               AccessControlType = 'Allow'; AccessRight = 'Read' }
        }
        Mock Test-Path {
            if ($LiteralPath -eq 'C:\data\rehearsal') { return $false }
            return $true
        }
        $report = Invoke-RehearsalNasBaseline rsl_rehearsal rsl_rehearsal share production_account 'C:\data\rehearsal'
        $report.status | Should -Be 'STOP'
        $report.checks.inspection_completed | Should -BeFalse
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
        $report = Invoke-RehearsalNasInspection rsl_rehearsal rsl_rehearsal share production_account
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
        $report = Invoke-RehearsalNasInspection rsl_rehearsal rsl_rehearsal share production_account
        $report.status | Should -Be 'STOP'
        $report.checks.no_rehearsal_account_on_production_share | Should -BeFalse
    }

    It 'stops when the production account has direct rehearsal share access' {
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
                @([pscustomobject]@{ AccountName = 'SERVER-NAS\rsl_rehearsal';
                                     AccessControlType = 'Allow'; AccessRight = 'Change' },
                  [pscustomobject]@{ AccountName = 'SERVER-NAS\production_account';
                                     AccessControlType = 'Allow'; AccessRight = 'Read' })
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
        $report = Invoke-RehearsalNasInspection rsl_rehearsal rsl_rehearsal share production_account
        $report.status | Should -Be 'STOP'
        $report.checks.no_production_account_on_rehearsal_share | Should -BeFalse
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
        $report = Invoke-RehearsalNasInspection rsl_rehearsal rsl_rehearsal share production_account
        $report.status | Should -Be 'STOP'
        $report.checks.no_broad_production_share_grant | Should -BeFalse
    }
}

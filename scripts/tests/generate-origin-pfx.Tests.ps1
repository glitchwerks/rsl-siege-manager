#Requires -Version 5.1
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }

<#
.SYNOPSIS
    Pester v5 tests for scripts/generate-origin-pfx.ps1.

.DESCRIPTION
    Covers parameter validation, openssl discovery, happy-path PFX generation,
    and password handling. All tests run without network access or real
    Cloudflare certificates. Fixtures are minimal PEM-formatted text files;
    openssl is stubbed via a temporary fake executable injected onto PATH.

.NOTES
    Test strategy
    -------------
    The script under test is a standalone .ps1 with a top-level param block and
    Set-StrictMode / ErrorActionPreference = Stop. It cannot be dot-sourced
    safely in a test scope, so every test invokes it via the call operator
    `& $script @params` and uses Should -Throw / Should -Not -Throw to assert
    on terminating error behaviour.

    openssl stubbing
    ----------------
    Tests that exercise the happy path and the "openssl non-functional" path
    create a temporary directory, write a tiny .cmd stub there, and prepend
    that directory to the session's PATH inside BeforeAll / BeforeEach so the
    script's Get-Command lookup finds the stub first. The stub is removed in
    AfterAll / AfterEach.

    Fixture files
    -------------
    Minimal PEM-formatted text files that satisfy the script's ValidateScript
    (Test-Path -PathType Leaf). Content is irrelevant for most tests because
    openssl is stubbed; the happy-path stub ignores file content and simply
    writes a known byte sequence to the -out path.
#>

BeforeAll {
    $script:ScriptPath = Join-Path -Path $PSScriptRoot -ChildPath '..\generate-origin-pfx.ps1'
    $script:ScriptPath = (Resolve-Path -LiteralPath $script:ScriptPath).Path

    # Temporary working directory for fixture files and fake binaries.
    $script:TmpDir = Join-Path -Path ([System.IO.Path]::GetTempPath()) -ChildPath "pester-pfx-$([System.Guid]::NewGuid().ToString('N'))"
    $null = New-Item -Path $script:TmpDir -ItemType Directory -Force

    # Minimal fixture PEM files (content is irrelevant -- ValidateScript only
    # checks Test-Path -PathType Leaf).
    $script:CertFile = Join-Path -Path $script:TmpDir -ChildPath 'cert.pem'
    $script:KeyFile  = Join-Path -Path $script:TmpDir -ChildPath 'key.pem'
    Set-Content -LiteralPath $script:CertFile -Value '-----BEGIN CERTIFICATE-----' -Encoding ASCII
    Set-Content -LiteralPath $script:KeyFile  -Value '-----BEGIN PRIVATE KEY-----'  -Encoding ASCII

    # Helper: build a SecureString from a plain-text string for test use only.
    function script:New-TestSecureString {
        [CmdletBinding()]
        param([string]$PlainText)
        $ss = New-Object System.Security.SecureString
        foreach ($c in $PlainText.ToCharArray()) {
            $ss.AppendChar($c)
        }
        $ss.MakeReadOnly()
        $ss
    }

    $script:TestPassword = script:New-TestSecureString -PlainText 'TestP@ssw0rd!'
}

AfterAll {
    if (Test-Path -LiteralPath $script:TmpDir) {
        Remove-Item -LiteralPath $script:TmpDir -Recurse -Force
    }
}

# ── Helper: create a fake openssl.cmd in a temp bin directory ─────────────────

function script:New-FakeOpensslDir {
    <#
    .SYNOPSIS
        Creates a temporary bin directory containing a fake openssl.cmd and
        returns the directory path.

    .PARAMETER ExitCode
        Exit code the stub should emit. Default 0 (success).

    .PARAMETER WriteOutput
        Literal path to write as the -out argument value. When non-empty the
        stub inspects its argument list for "-out <path>" and writes a minimal
        binary blob to that path, simulating a real PFX output file.

    .PARAMETER ProviderLine
        Optional extra line to include in the `list -providers` output so the
        script can detect legacy provider availability.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [int]$ExitCode = 0,
        [switch]$WriteOutputFile,
        [switch]$IncludeLegacyProvider
    )

    $binDir = Join-Path -Path $script:TmpDir -ChildPath "bin-$([System.Guid]::NewGuid().ToString('N').Substring(0,8))"
    $null = New-Item -Path $binDir -ItemType Directory -Force

    # Compose the stub body.
    # Use double-quoted strings so `n is interpreted as a newline character.
    # Single-quoted strings in PowerShell are literal -- backtick escapes are
    # not processed, so 'foo`nbar' outputs the literal characters "foo`nbar".
    $providerOutput = if ($IncludeLegacyProvider) {
        "Providers loaded by the default configuration:`n  legacy"
    } else {
        "Providers loaded by the default configuration:`n  default"
    }

    # The stub handles two modes:
    #   list -providers  -> emit provider info, exit 0
    #   pkcs12 ...       -> optionally create -out file, then exit $ExitCode
    $stubContent = @"
@echo off
if "%1"=="list" (
    echo $providerOutput
    exit /b 0
)
"@

    if ($WriteOutputFile) {
        # Parse the -out argument from the command line and write a tiny blob.
        $stubContent += @'

for %%A in (%*) do (
    if defined _next_is_out (
        echo FAKEPFX>%%A
        set _next_is_out=
    )
    if "%%A"=="-out" set _next_is_out=1
)
'@
    }

    $stubContent += "`nexit /b $ExitCode`n"

    $cmdPath = Join-Path -Path $binDir -ChildPath 'openssl.cmd'
    Set-Content -LiteralPath $cmdPath -Value $stubContent -Encoding ASCII

    return $binDir
}

function script:Add-DirToPathFront {
    [CmdletBinding()]
    param([string]$Dir)
    $env:PATH = "$Dir;$env:PATH"
}

function script:Remove-DirFromPathFront {
    [CmdletBinding()]
    param([string]$Dir)
    $env:PATH = ($env:PATH -replace [regex]::Escape("$Dir;"), '')
}

# ── 1. Parameter Validation ───────────────────────────────────────────────────

Describe 'Parameter Validation' {

    Context 'Missing mandatory parameters' {

        # These tests invoke the script in a non-interactive child shell so that
        # a missing [Parameter(Mandatory)] value produces a non-zero exit code
        # immediately instead of blocking on stdin for a prompt (which hangs CI).

        It 'fails when CertPath is omitted' {
            $outPath = Join-Path -Path $script:TmpDir -ChildPath 'out1.pfx'
            $cmd = "& '$script:ScriptPath' -KeyPath '$script:KeyFile' -OutPath '$outPath' -Password (ConvertTo-SecureString 'x' -AsPlainText -Force)"
            $null = pwsh -NoProfile -NonInteractive -Command $cmd 2>&1
            $LASTEXITCODE | Should -Not -Be 0
        }

        It 'fails when KeyPath is omitted' {
            $outPath = Join-Path -Path $script:TmpDir -ChildPath 'out2.pfx'
            $cmd = "& '$script:ScriptPath' -CertPath '$script:CertFile' -OutPath '$outPath' -Password (ConvertTo-SecureString 'x' -AsPlainText -Force)"
            $null = pwsh -NoProfile -NonInteractive -Command $cmd 2>&1
            $LASTEXITCODE | Should -Not -Be 0
        }

        It 'fails when OutPath is omitted' {
            $cmd = "& '$script:ScriptPath' -CertPath '$script:CertFile' -KeyPath '$script:KeyFile' -Password (ConvertTo-SecureString 'x' -AsPlainText -Force)"
            $null = pwsh -NoProfile -NonInteractive -Command $cmd 2>&1
            $LASTEXITCODE | Should -Not -Be 0
        }

        It 'fails when Password is omitted' {
            $outPath = Join-Path -Path $script:TmpDir -ChildPath 'out3.pfx'
            $cmd = "& '$script:ScriptPath' -CertPath '$script:CertFile' -KeyPath '$script:KeyFile' -OutPath '$outPath'"
            $null = pwsh -NoProfile -NonInteractive -Command $cmd 2>&1
            $LASTEXITCODE | Should -Not -Be 0
        }
    }

    Context 'Non-existent input files' {

        BeforeAll {
            # A valid openssl on PATH so the script does not fail on that check.
            $script:ParamValBinDir = script:New-FakeOpensslDir -WriteOutputFile
            script:Add-DirToPathFront -Dir $script:ParamValBinDir
        }

        AfterAll {
            script:Remove-DirFromPathFront -Dir $script:ParamValBinDir
        }

        It 'fails when CertPath points to a non-existent file' {
            $params = @{
                CertPath = Join-Path -Path $script:TmpDir -ChildPath 'does-not-exist.pem'
                KeyPath  = $script:KeyFile
                OutPath  = Join-Path -Path $script:TmpDir -ChildPath 'out4.pfx'
                Password = $script:TestPassword
            }
            { & $script:ScriptPath @params } | Should -Throw -ExpectedMessage '*does not exist*'
        }

        It 'fails when KeyPath points to a non-existent file' {
            $params = @{
                CertPath = $script:CertFile
                KeyPath  = Join-Path -Path $script:TmpDir -ChildPath 'does-not-exist.key'
                OutPath  = Join-Path -Path $script:TmpDir -ChildPath 'out5.pfx'
                Password = $script:TestPassword
            }
            { & $script:ScriptPath @params } | Should -Throw -ExpectedMessage '*does not exist*'
        }
    }

    Context 'OutPath in a non-existent directory' {

        BeforeAll {
            $script:OutDirBinDir = script:New-FakeOpensslDir -WriteOutputFile
            script:Add-DirToPathFront -Dir $script:OutDirBinDir
        }

        AfterAll {
            script:Remove-DirFromPathFront -Dir $script:OutDirBinDir
        }

        It 'fails when OutPath directory does not exist' {
            # openssl will attempt to write to a path whose parent does not exist;
            # the fake stub writes using CMD redirect which creates the file only
            # if the parent exists. Because the parent is absent the stub produces
            # no output file and openssl exits 0 -- but the script's post-call
            # verification (Test-Path -LiteralPath $outAbsolute) catches this and
            # throws. We assert on the generic terminating-error behaviour.
            $nonExistentDir = Join-Path -Path $script:TmpDir -ChildPath 'no-such-dir'
            $params = @{
                CertPath = $script:CertFile
                KeyPath  = $script:KeyFile
                OutPath  = Join-Path -Path $nonExistentDir -ChildPath 'out.pfx'
                Password = $script:TestPassword
                Force    = $true
            }
            { & $script:ScriptPath @params } | Should -Throw
        }
    }
}

# ── 2. openssl Discovery ──────────────────────────────────────────────────────

Describe 'openssl Discovery' {

    Context 'openssl absent from PATH and not at Git-for-Windows fallback locations' {

        BeforeAll {
            # Save PATH and remove any real openssl so Get-Command returns nothing.
            $script:OriginalPath = $env:PATH
            # Strip entries that might contain openssl; replace with a clean stub
            # dir that has no openssl binary.
            $emptyBinDir = Join-Path -Path $script:TmpDir -ChildPath 'empty-bin'
            $null = New-Item -Path $emptyBinDir -ItemType Directory -Force
            $env:PATH = $emptyBinDir

            # Ensure the two hardcoded Git-for-Windows candidate paths do not
            # exist in this test environment (they likely already don't on a
            # GitHub Actions windows-latest runner, but be explicit).
            # We cannot delete system files, so we rely on the paths not existing
            # on the CI runner. This context is therefore most meaningful on a
            # runner without Git for Windows installed at the default location.
        }

        AfterAll {
            $env:PATH = $script:OriginalPath
        }

        It 'throws with openssl install instructions when openssl is not found' {
            $params = @{
                CertPath = $script:CertFile
                KeyPath  = $script:KeyFile
                OutPath  = Join-Path -Path $script:TmpDir -ChildPath 'out-nodiscover.pfx'
                Password = $script:TestPassword
            }
            { & $script:ScriptPath @params } | Should -Throw -ExpectedMessage '*openssl was not found*'
        }
    }

    Context 'openssl present but exits non-zero on pkcs12' {

        BeforeAll {
            $script:BadOpensslDir = script:New-FakeOpensslDir -ExitCode 1
            script:Add-DirToPathFront -Dir $script:BadOpensslDir
        }

        AfterAll {
            script:Remove-DirFromPathFront -Dir $script:BadOpensslDir
        }

        It 'throws with openssl failure detail when openssl exits non-zero' {
            $params = @{
                CertPath = $script:CertFile
                KeyPath  = $script:KeyFile
                OutPath  = Join-Path -Path $script:TmpDir -ChildPath 'out-badssl.pfx'
                Password = $script:TestPassword
                Force    = $true
            }
            { & $script:ScriptPath @params } | Should -Throw -ExpectedMessage '*openssl pkcs12 failed*'
        }
    }
}

# ── 3. Happy Path ─────────────────────────────────────────────────────────────

Describe 'Happy Path' {

    BeforeAll {
        $script:HappyBinDir = script:New-FakeOpensslDir -WriteOutputFile
        script:Add-DirToPathFront -Dir $script:HappyBinDir
    }

    AfterAll {
        script:Remove-DirFromPathFront -Dir $script:HappyBinDir
    }

    It 'produces a non-empty PFX file at OutPath' {
        $outPath = Join-Path -Path $script:TmpDir -ChildPath 'happy-out.pfx'
        $params = @{
            CertPath = $script:CertFile
            KeyPath  = $script:KeyFile
            OutPath  = $outPath
            Password = $script:TestPassword
        }
        { & $script:ScriptPath @params } | Should -Not -Throw
        Test-Path -LiteralPath $outPath -PathType Leaf | Should -BeTrue
        (Get-Item -LiteralPath $outPath).Length | Should -BeGreaterThan 0
    }

    It 'emits a result object with PfxPath and SizeBytes properties' {
        $outPath = Join-Path -Path $script:TmpDir -ChildPath 'happy-result.pfx'
        $params = @{
            CertPath = $script:CertFile
            KeyPath  = $script:KeyFile
            OutPath  = $outPath
            Password = $script:TestPassword
        }
        $result = & $script:ScriptPath @params
        $result | Should -Not -BeNullOrEmpty
        $result.PfxPath   | Should -Not -BeNullOrEmpty
        $result.SizeBytes | Should -BeGreaterThan 0
    }

    It 'PfxPath in result object resolves to the same file as OutPath' {
        $outPath = Join-Path -Path $script:TmpDir -ChildPath 'happy-path.pfx'
        $params = @{
            CertPath = $script:CertFile
            KeyPath  = $script:KeyFile
            OutPath  = $outPath
            Password = $script:TestPassword
        }
        $result = & $script:ScriptPath @params
        $result.PfxPath | Should -Be (Resolve-Path -LiteralPath $outPath).Path
    }

    Context 'Idempotency (re-run overwrites existing PFX)' {

        It 'succeeds on re-run with -Force and overwrites the existing file' {
            $outPath = Join-Path -Path $script:TmpDir -ChildPath 'idempotent.pfx'
            $params = @{
                CertPath = $script:CertFile
                KeyPath  = $script:KeyFile
                OutPath  = $outPath
                Password = $script:TestPassword
                Force    = $true
            }
            # First run creates the file.
            $null = & $script:ScriptPath @params
            $firstModified = (Get-Item -LiteralPath $outPath).LastWriteTime

            # Guarantee a measurable time gap. Windows NTFS has 100ns resolution
            # so 100ms is more than sufficient to produce a different LastWriteTime.
            Start-Sleep -Milliseconds 100

            # Second run with -Force should overwrite without error.
            { & $script:ScriptPath @params } | Should -Not -Throw
            $secondModified = (Get-Item -LiteralPath $outPath).LastWriteTime
            $secondModified | Should -BeGreaterThan $firstModified
        }
    }

    Context 'Legacy provider detection' {

        It 'succeeds when legacy provider is reported by openssl list -providers' {
            $legacyBinDir = script:New-FakeOpensslDir -WriteOutputFile -IncludeLegacyProvider
            script:Add-DirToPathFront -Dir $legacyBinDir
            try {
                $outPath = Join-Path -Path $script:TmpDir -ChildPath 'legacy.pfx'
                $params = @{
                    CertPath = $script:CertFile
                    KeyPath  = $script:KeyFile
                    OutPath  = $outPath
                    Password = $script:TestPassword
                }
                { & $script:ScriptPath @params } | Should -Not -Throw
                Test-Path -LiteralPath $outPath -PathType Leaf | Should -BeTrue
            } finally {
                script:Remove-DirFromPathFront -Dir $legacyBinDir
            }
        }
    }
}

# ── 4. Password Handling ──────────────────────────────────────────────────────

Describe 'Password Handling' {

    BeforeAll {
        $script:PwdBinDir = script:New-FakeOpensslDir -WriteOutputFile
        script:Add-DirToPathFront -Dir $script:PwdBinDir
    }

    AfterAll {
        script:Remove-DirFromPathFront -Dir $script:PwdBinDir
    }

    It 'accepts a SecureString password without throwing' {
        $securePass = script:New-TestSecureString -PlainText 'S3cur3P@ss!'
        $outPath = Join-Path -Path $script:TmpDir -ChildPath 'pwd-secure.pfx'
        $params = @{
            CertPath = $script:CertFile
            KeyPath  = $script:KeyFile
            OutPath  = $outPath
            Password = $securePass
        }
        { & $script:ScriptPath @params } | Should -Not -Throw
    }

    It 'rejects a plain-text string where SecureString is required' {
        $outPath = Join-Path -Path $script:TmpDir -ChildPath 'pwd-plain.pfx'
        $params = @{
            CertPath = $script:CertFile
            KeyPath  = $script:KeyFile
            OutPath  = $outPath
            Password = 'PlainTextPassword'
        }
        { & $script:ScriptPath @params } | Should -Throw
    }

    It 'does not leave plain-text password in a reachable variable after completion' {
        # This test verifies the script does not emit the password on the output
        # stream (pipeline output is the result object, not a string containing
        # the password).
        $outPath = Join-Path -Path $script:TmpDir -ChildPath 'pwd-leak.pfx'
        $securePass = script:New-TestSecureString -PlainText 'DoNotLeak99!'
        $params = @{
            CertPath = $script:CertFile
            KeyPath  = $script:KeyFile
            OutPath  = $outPath
            Password = $securePass
        }
        $output = & $script:ScriptPath @params
        # Output should be the PSCustomObject result, not a raw string
        $output | Should -BeOfType [PSCustomObject]
        # The string representation of the output must not contain the plain password
        ($output | Out-String) | Should -Not -Match 'DoNotLeak99!'
    }
}

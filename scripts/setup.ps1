#Requires -RunAsAdministrator
<#
.SYNOPSIS
    CDL Development Environment Setup Script for Windows
    Contextual Dynamics Laboratory, Dartmouth College

.DESCRIPTION
    This script sets up a complete development environment for CDL research.
    It is idempotent - safe to run multiple times.

.NOTES
    Run in PowerShell as Administrator:
    irm https://raw.githubusercontent.com/ContextLab/lab-manual/master/scripts/setup.ps1 | iex

    Or locally (the default execution policy blocks scripts, so bypass it):
    powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1

    Setting CDL_SETUP_NO_AUTORUN=1 loads the functions without running Main.
    The Windows CI workflow uses this to exercise them individually.
#>

$ErrorActionPreference = "Stop"

# TLS 1.2 is not the default in Windows PowerShell 5.1, and raw.githubusercontent.com
# and repo.anaconda.com both refuse anything older, so every download below fails
# without this.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Log file
$LogFile = "$env:USERPROFILE\.cdl-setup.log"

# Steps that did not complete. Show-Summary reports these instead of claiming
# an unconditional success.
$script:FailedSteps = @()

# ============================================================================
# Utility Functions
# ============================================================================

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[CDL Setup] $Message" -ForegroundColor Cyan
    Add-Content -Path $LogFile -Value "[$timestamp] $Message"
}

function Write-LogSuccess {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[CDL Setup] $Message" -ForegroundColor Green
    Add-Content -Path $LogFile -Value "[$timestamp] SUCCESS: $Message"
}

function Write-LogWarning {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[CDL Setup] $Message" -ForegroundColor Yellow
    Add-Content -Path $LogFile -Value "[$timestamp] WARNING: $Message"
}

function Write-LogError {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[CDL Setup] $Message" -ForegroundColor Red
    Add-Content -Path $LogFile -Value "[$timestamp] ERROR: $Message"
}

function Add-FailedStep {
    param([string]$Name)
    $script:FailedSteps += $Name
}

function Get-ExitCode {
    <#
    .SYNOPSIS
        0 if every step completed, 1 otherwise.
    .DESCRIPTION
        Deliberately independent of $LASTEXITCODE. Native commands here fail
        routinely without meaning anything -- `winget list` returns nonzero
        when a package simply is not installed yet -- so a run scored on the
        last native exit code reports failure after a perfectly clean setup.
    #>
    return [int](@($script:FailedSteps).Count -gt 0)
}

function Test-Command {
    param([string]$Command)
    return [bool](Get-Command -Name $Command -ErrorAction SilentlyContinue)
}

function Test-Administrator {
    <#
    .SYNOPSIS
        Is this session elevated?
    .DESCRIPTION
        The #Requires -RunAsAdministrator directive at the top of this file is
        only honoured when PowerShell invokes it as a SCRIPT. The documented
        install path pipes the file into Invoke-Expression, where the directive
        is an inert comment, so the requirement has to be re-checked here.
    #>
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-Native {
    <#
    .SYNOPSIS
        Run a native executable and capture its merged output and exit code.
    .DESCRIPTION
        Two Windows PowerShell behaviours make the naive `$x = foo 2>&1` form
        unsafe here:

        1. With $ErrorActionPreference = 'Stop', anything a native command
           writes to stderr becomes a NativeCommandError and TERMINATES the
           script. winget writes progress to stderr routinely, so a perfectly
           normal `winget list` would abort the whole setup.
        2. $LASTEXITCODE is left over from the previous native command if the
           current one never runs, so checking it without knowing whether the
           command executed reads a stale value.

        This runs the command with the preference relaxed, then restores it,
        and always reports the exit code of THIS command.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Arguments = @()
    )

    if (-not (Test-Command $Command)) {
        return [PSCustomObject]@{ Output = ""; ExitCode = -1; Ran = $false }
    }

    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $global:LASTEXITCODE = 0
        $output = & $Command @Arguments 2>&1 | Out-String
        return [PSCustomObject]@{
            Output   = $output
            ExitCode = $LASTEXITCODE
            Ran      = $true
        }
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

function Update-SessionPath {
    <#
    .SYNOPSIS
        Pick up PATH entries added by an installer in this same session.
    .DESCRIPTION
        Rebuilding from Machine + User alone discards process-only additions
        this script made earlier (the conda directories, for one), so the
        existing $env:Path is merged in and duplicates dropped.
    #>
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $combined = "$machine;$user;$env:Path" -split ';' |
        Where-Object { $_ } |
        Select-Object -Unique
    $env:Path = $combined -join ';'
}

function Invoke-Download {
    <#
    .SYNOPSIS
        Download a file. -UseBasicParsing keeps this working on a fresh Windows
        image where Internet Explorer's first-launch configuration has never
        been completed, which otherwise makes Invoke-WebRequest throw.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile
    )
    Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing
}

# ============================================================================
# Package Manager (Winget)
# ============================================================================

function Install-Winget {
    Write-Log "Checking for winget..."

    if (Test-Command "winget") {
        Write-LogSuccess "winget already installed"
        return
    }

    Write-Log "Installing winget (App Installer)..."

    # On Windows 11, winget should be pre-installed
    # On Windows 10, we need to install it from Microsoft Store or GitHub

    try {
        # Try to get it from Microsoft Store
        Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe
        Write-LogSuccess "winget installed via Microsoft Store"
    }
    catch {
        Write-LogWarning "Could not install winget automatically. Please install 'App Installer' from Microsoft Store."
        Write-LogWarning "URL: https://www.microsoft.com/p/app-installer/9nblggh4nns1"
        Add-FailedStep "winget"
    }
}

function Install-WingetPackage {
    <#
    .SYNOPSIS
        Install one winget package, reporting failure instead of aborting.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if (-not (Test-Command "winget")) {
        Write-LogWarning "$Name : winget unavailable, skipping"
        Add-FailedStep $Name
        return $false
    }

    $result = Invoke-Native "winget" @(
        "install", "--id", $Id,
        "--accept-source-agreements", "--accept-package-agreements"
    )

    # winget returns 0x8A15002B (-1978335189) when the package is already
    # installed, which is a success for our purposes.
    if ($result.ExitCode -eq 0 -or $result.ExitCode -eq -1978335189) {
        Write-LogSuccess "$Name installed"
        return $true
    }

    Write-LogWarning "$Name : winget exited with $($result.ExitCode)"
    Add-FailedStep $Name
    return $false
}

function Test-WingetInstalled {
    param([Parameter(Mandatory = $true)][string]$Id)
    $result = Invoke-Native "winget" @("list", "--id", $Id)
    return $result.Ran -and $result.Output -match [regex]::Escape($Id)
}

# ============================================================================
# Application Installation
# ============================================================================

function Install-Git {
    if (Test-Command "git") {
        $version = (Invoke-Native "git" @("--version")).Output.Trim()
        Write-LogSuccess "Git already installed: $version"
        return
    }

    Write-Log "Installing Git..."
    if (Install-WingetPackage -Id "Git.Git" -Name "Git") {
        Update-SessionPath
    }
}

function Install-Slack {
    Write-Log "Checking Slack installation..."

    $slackPath = "$env:LOCALAPPDATA\slack\slack.exe"
    if (Test-Path $slackPath) {
        Write-LogSuccess "Slack already installed"
        return
    }

    if (Test-WingetInstalled -Id "SlackTechnologies.Slack") {
        Write-LogSuccess "Slack already installed"
        return
    }

    Write-Log "Installing Slack..."
    Install-WingetPackage -Id "SlackTechnologies.Slack" -Name "Slack" | Out-Null
}

function Install-VSCode {
    Write-Log "Checking VS Code installation..."

    if (Test-Command "code") {
        Write-LogSuccess "VS Code already installed"
        return
    }

    if (Test-WingetInstalled -Id "Microsoft.VisualStudioCode") {
        Write-LogSuccess "VS Code already installed"
        return
    }

    Write-Log "Installing VS Code..."
    if (Install-WingetPackage -Id "Microsoft.VisualStudioCode" -Name "VS Code") {
        Update-SessionPath
    }
}

function Install-LaTeX {
    Write-Log "Checking LaTeX installation..."

    if (Test-Command "pdflatex") {
        Write-LogSuccess "LaTeX already installed"
        return
    }

    # Check common MiKTeX locations
    $miktexPaths = @(
        "$env:LOCALAPPDATA\Programs\MiKTeX\miktex\bin\x64\pdflatex.exe",
        "C:\Program Files\MiKTeX\miktex\bin\x64\pdflatex.exe"
    )

    foreach ($path in $miktexPaths) {
        if (Test-Path $path) {
            Write-LogSuccess "LaTeX (MiKTeX) found at $path"
            return
        }
    }

    Write-Log "Installing MiKTeX..."
    if (Install-WingetPackage -Id "MiKTeX.MiKTeX" -Name "MiKTeX") {
        Update-SessionPath
    }
}

function Install-Dropbox {
    Write-Log "Checking Dropbox installation..."

    $dropboxPaths = @(
        "$env:LOCALAPPDATA\Dropbox\Dropbox.exe",
        "${env:ProgramFiles(x86)}\Dropbox\Client\Dropbox.exe",
        "$env:ProgramFiles\Dropbox\Client\Dropbox.exe"
    )

    foreach ($path in $dropboxPaths) {
        if (Test-Path $path) {
            Write-LogSuccess "Dropbox already installed"
            return
        }
    }

    Write-Log "Installing Dropbox..."
    Install-WingetPackage -Id "Dropbox.Dropbox" -Name "Dropbox" | Out-Null
}

# ============================================================================
# Conda Installation
# ============================================================================

function Get-CondaPath {
    <#
    .SYNOPSIS
        Locate conda.exe, whether or not it is on PATH.
    #>
    $command = Get-Command -Name "conda" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $condaPaths = @(
        "$env:USERPROFILE\miniconda3\Scripts\conda.exe",
        "$env:USERPROFILE\anaconda3\Scripts\conda.exe",
        "C:\ProgramData\miniconda3\Scripts\conda.exe",
        "C:\ProgramData\anaconda3\Scripts\conda.exe"
    )

    foreach ($path in $condaPaths) {
        if (Test-Path $path) {
            return $path
        }
    }

    return $null
}

function Add-CondaToPath {
    param([Parameter(Mandatory = $true)][string]$CondaExe)
    $condaDir = Split-Path -Parent (Split-Path -Parent $CondaExe)
    $env:Path = "$condaDir;$condaDir\Scripts;$condaDir\Library\bin;$env:Path"
}

function Install-Conda {
    Write-Log "Checking Conda installation..."

    $existing = Get-CondaPath
    if ($existing) {
        Add-CondaToPath -CondaExe $existing
        $version = (Invoke-Native "conda" @("--version")).Output.Trim()
        Write-LogSuccess "Conda already installed: $version"
        return
    }

    Write-Log "Installing Miniconda..."

    $installerUrl = "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"
    $installerPath = "$env:TEMP\Miniconda3-latest-Windows-x86_64.exe"
    $installDir = "$env:USERPROFILE\miniconda3"

    try {
        Write-Log "Downloading Miniconda installer..."
        Invoke-Download -Uri $installerUrl -OutFile $installerPath

        Write-Log "Running Miniconda installer (this may take a few minutes)..."
        # The NSIS installer requires /D= to be the LAST argument and to be
        # unquoted -- it takes the rest of the command line verbatim, which is
        # also how it copes with a user profile path containing spaces. Passing
        # the arguments as separate array elements lets PowerShell quote
        # "/D=C:\Users\Jane Doe\miniconda3", which NSIS then rejects.
        $process = Start-Process -FilePath $installerPath `
            -ArgumentList "/S /D=$installDir" -Wait -PassThru

        if ($process.ExitCode -ne 0) {
            Write-LogError "Miniconda installer exited with $($process.ExitCode)"
            Add-FailedStep "Miniconda"
            return
        }
    }
    finally {
        # Runs even if the download or install threw, so a half-downloaded
        # installer is not left in TEMP.
        if (Test-Path $installerPath) {
            Remove-Item $installerPath -Force -ErrorAction SilentlyContinue
        }
    }

    Add-CondaToPath -CondaExe "$installDir\Scripts\conda.exe"

    # Initialize conda for PowerShell (affects future sessions, not this one)
    Invoke-Native "$installDir\Scripts\conda.exe" @("init", "powershell") | Out-Null

    Write-LogSuccess "Miniconda installed"
}

# ============================================================================
# CDL Environment Setup
# ============================================================================

function Install-CDLEnvironment {
    Write-Log "Setting up CDL conda environment..."

    $condaExe = Get-CondaPath
    if (-not $condaExe) {
        Write-LogError "Conda not found. Please restart PowerShell and run this script again."
        Add-FailedStep "CDL environment"
        return
    }
    Add-CondaToPath -CondaExe $condaExe

    if (Test-CDLEnvironment) {
        Write-Log "CDL environment already exists, updating..."
        $updateEnv = $true
    }
    else {
        Write-Log "Creating CDL environment..."
        $updateEnv = $false
    }

    $envFile = "$env:TEMP\cdl-environment.yml"

    try {
        Invoke-Download `
            -Uri "https://raw.githubusercontent.com/ContextLab/lab-manual/master/scripts/cdl-environment.yml" `
            -OutFile $envFile

        if ($updateEnv) {
            $result = Invoke-Native "conda" @("env", "update", "-n", "cdl", "-f", $envFile, "--prune")
        }
        else {
            $result = Invoke-Native "conda" @("env", "create", "-f", $envFile)
        }

        if ($result.ExitCode -ne 0) {
            Write-LogError "conda env setup failed (exit $($result.ExitCode))"
            Write-Host $result.Output
            Add-FailedStep "CDL environment"
            return
        }
    }
    finally {
        if (Test-Path $envFile) {
            Remove-Item $envFile -Force -ErrorAction SilentlyContinue
        }
    }

    Write-LogSuccess "CDL environment configured"
}

function Test-CDLEnvironment {
    <#
    .SYNOPSIS
        Does a conda environment named exactly 'cdl' exist?
    #>
    $result = Invoke-Native "conda" @("env", "list")
    if (-not $result.Ran) {
        return $false
    }
    foreach ($line in $result.Output -split "`r?`n") {
        if ($line -match '^\s*cdl\s') {
            return $true
        }
    }
    return $false
}

# ============================================================================
# Verification
# ============================================================================

# Import every package in one interpreter and report a version for each.
#
# This is a single-quoted here-string on purpose: PowerShell performs NO
# interpolation inside it, so the Python source arrives byte for byte. The
# previous version built the equivalent command with "import $pkg; print(f'
# $pkg: {$pkg.__version__}')" inside a DOUBLE-quoted string, where PowerShell
# reads "$pkg:" as a drive-qualified variable (the $env:PATH form) and refuses
# to parse the file at all -- which is issue #14. Nothing in this string is a
# PowerShell variable, so that cannot recur.
#
# getattr(..., '__version__', ...) rather than m.__version__ because not every
# package here exposes one, and a missing attribute would otherwise be reported
# as an import failure.
$script:PackageCheckSource = @'
import importlib
import sys

PACKAGES = ["numpy", "pandas", "torch", "sklearn", "numba", "umap", "hypertools"]

failed = []
for name in PACKAGES:
    try:
        module = importlib.import_module(name)
    except Exception as exc:
        failed.append(name)
        print("  {}: FAILED ({}: {})".format(name, type(exc).__name__, exc))
        continue
    version = getattr(module, "__version__", None)
    if version is None:
        try:
            from importlib.metadata import version as pkg_version
            version = pkg_version(name)
        except Exception:
            version = "installed (version unknown)"
    print("  {}: {}".format(name, version))

if failed:
    print("FAILED_PACKAGES=" + ",".join(failed))
    sys.exit(1)
sys.exit(0)
'@

function Test-CDLPackages {
    <#
    .SYNOPSIS
        Import each key package inside the cdl environment.
    .DESCRIPTION
        Uses `conda run -n cdl` rather than `conda activate cdl`. Activation is
        a shell function installed by `conda init powershell` into the user's
        profile; in a non-interactive run -- and in the very session that just
        installed conda -- that profile has not been loaded, so `conda activate`
        fails and the `python` that follows is whichever one happens to be on
        PATH. Every package check after it would then be reporting on the wrong
        interpreter. `conda run` needs no profile and targets the environment
        explicitly.
    #>
    $result = Invoke-Native "conda" @(
        "run", "-n", "cdl", "--no-capture-output", "python", "-c", $script:PackageCheckSource
    )

    if (-not $result.Ran) {
        Write-LogWarning "conda not available, cannot verify packages"
        return $false
    }

    Write-Host $result.Output.TrimEnd()

    if ($result.ExitCode -eq 0) {
        Write-LogSuccess "All key packages import correctly"
        return $true
    }

    Write-LogWarning "Some packages failed to import (exit $($result.ExitCode))"
    return $false
}

function Test-Installation {
    Write-Log "Verifying installation..."

    # Check Git
    if (Test-Command "git") {
        $version = (Invoke-Native "git" @("--version")).Output.Trim()
        Write-LogSuccess "Git: $version"
    }
    else {
        Write-LogError "Git: NOT INSTALLED"
        Add-FailedStep "Git"
    }

    # Check Slack
    $slackPath = "$env:LOCALAPPDATA\slack\slack.exe"
    if ((Test-Path $slackPath) -or (Test-Path "$env:ProgramFiles\Slack\Slack.exe")) {
        Write-LogSuccess "Slack: Installed"
    }
    else {
        Write-LogWarning "Slack: Not found"
    }

    # Check VS Code
    if (Test-Command "code") {
        Write-LogSuccess "VS Code: Installed"
    }
    else {
        Write-LogWarning "VS Code: Not in PATH (may require restart)"
    }

    # Check LaTeX
    if (Test-Command "pdflatex") {
        Write-LogSuccess "LaTeX: Installed"
    }
    else {
        Write-LogWarning "LaTeX: Not in PATH (may require restart)"
    }

    # Check Conda
    if (-not (Test-Command "conda")) {
        Write-LogWarning "Conda: Not in PATH (restart PowerShell to activate)"
        Write-LogWarning "CDL environment: Cannot check without conda"
        return
    }

    $version = (Invoke-Native "conda" @("--version")).Output.Trim()
    Write-LogSuccess "Conda: $version"

    # Check CDL environment
    if (-not (Test-CDLEnvironment)) {
        Write-LogWarning "CDL environment: Not found"
        Add-FailedStep "CDL environment"
        return
    }

    Write-LogSuccess "CDL environment: Created"
    Write-Log "Testing Python packages in CDL environment..."
    if (-not (Test-CDLPackages)) {
        Add-FailedStep "CDL packages"
    }
}

# ============================================================================
# Summary
# ============================================================================

function Show-Summary {
    $failed = $script:FailedSteps | Select-Object -Unique
    $ok = $failed.Count -eq 0

    Write-Host ""
    Write-Host "============================================================" -ForegroundColor $(if ($ok) { "Green" } else { "Yellow" })
    if ($ok) {
        Write-Host "CDL Development Environment Setup Complete!" -ForegroundColor Green
    }
    else {
        Write-Host "CDL Development Environment Setup INCOMPLETE" -ForegroundColor Yellow
    }
    Write-Host "============================================================" -ForegroundColor $(if ($ok) { "Green" } else { "Yellow" })
    Write-Host ""

    if (-not $ok) {
        Write-Host "These steps did not complete:" -ForegroundColor Yellow
        foreach ($step in $failed) {
            Write-Host "  - $step" -ForegroundColor Yellow
        }
        Write-Host ""
        Write-Host "Re-running this script is safe and will retry them." -ForegroundColor Yellow
        Write-Host ""
    }

    Write-Host "Components handled by this script:"
    Write-Host "  - Git"
    Write-Host "  - Slack"
    Write-Host "  - VS Code"
    Write-Host "  - LaTeX (MiKTeX)"
    Write-Host "  - Dropbox"
    Write-Host "  - Miniconda"
    Write-Host "  - CDL conda environment"
    Write-Host ""
    Write-Host "To activate the CDL environment:"
    Write-Host "  conda activate cdl"
    Write-Host ""
    Write-Host "Log file: $LogFile"
    Write-Host ""
    Write-Host "Getting help:"
    Write-Host "  - Software/hardware issues: help@dartmouth.edu"
    Write-Host "  - Lab-specific issues: Slack or email Jeremy"
    Write-Host "  - General questions: https://github.com/ContextLab/lab-manual/issues"
    Write-Host ""
    Write-Host "NOTE: You may need to restart PowerShell for all changes to take effect." -ForegroundColor Yellow
    Write-Host ""

    return $ok
}

# ============================================================================
# Main
# ============================================================================

function Main {
    Write-Host ""
    Write-Host "============================================================"
    Write-Host "CDL Development Environment Setup"
    Write-Host "Contextual Dynamics Laboratory, Dartmouth College"
    Write-Host "============================================================"
    Write-Host ""

    if (-not (Test-Administrator)) {
        Write-Host "This script needs an elevated PowerShell session." -ForegroundColor Red
        Write-Host "Close this window, right-click PowerShell, choose" -ForegroundColor Red
        Write-Host "'Run as administrator', and run the command again." -ForegroundColor Red
        Write-Host ""
        Add-FailedStep "elevation"
        return
    }

    # Initialize log file
    Set-Content -Path $LogFile -Value "CDL Setup Log - $(Get-Date)"
    Add-Content -Path $LogFile -Value "============================================================"

    Write-Log "Detected platform: Windows ($env:PROCESSOR_ARCHITECTURE)"

    # Install package manager
    Install-Winget

    # Install applications
    Install-Git
    Install-Slack
    Install-VSCode
    Install-LaTeX
    Install-Dropbox

    # Install Conda and set up environment
    Install-Conda
    Install-CDLEnvironment

    # Verify installation
    Test-Installation

    # Print summary
    Show-Summary | Out-Null
}

# Run main. CDL_SETUP_NO_AUTORUN lets the Windows CI workflow load these
# functions and exercise them one at a time without installing anything.
if (-not $env:CDL_SETUP_NO_AUTORUN) {
    Main
    exit (Get-ExitCode)
}

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

    Or locally:
    .\scripts\setup.ps1
#>

$ErrorActionPreference = "Stop"

# Log file
$LogFile = "$env:USERPROFILE\.cdl-setup.log"

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

function Test-Command {
    param([string]$Command)
    return [bool](Get-Command -Name $Command -ErrorAction SilentlyContinue)
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
    }
}

# ============================================================================
# Application Installation
# ============================================================================

function Install-Git {
    if (Test-Command "git") {
        $version = git --version
        Write-LogSuccess "Git already installed: $version"
        return
    }

    Write-Log "Installing Git..."
    winget install --id Git.Git --accept-source-agreements --accept-package-agreements
    Write-LogSuccess "Git installed"

    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}

function Install-Slack {
    Write-Log "Checking Slack installation..."

    $slackPath = "$env:LOCALAPPDATA\slack\slack.exe"
    if (Test-Path $slackPath) {
        Write-LogSuccess "Slack already installed"
        return
    }

    # Check if installed via winget
    $installed = winget list --id SlackTechnologies.Slack 2>&1
    if ($installed -match "SlackTechnologies.Slack") {
        Write-LogSuccess "Slack already installed"
        return
    }

    Write-Log "Installing Slack..."
    winget install --id SlackTechnologies.Slack --accept-source-agreements --accept-package-agreements
    Write-LogSuccess "Slack installed"
}

function Install-VSCode {
    Write-Log "Checking VS Code installation..."

    if (Test-Command "code") {
        Write-LogSuccess "VS Code already installed"
        return
    }

    # Check if installed via winget
    $installed = winget list --id Microsoft.VisualStudioCode 2>&1
    if ($installed -match "Microsoft.VisualStudioCode") {
        Write-LogSuccess "VS Code already installed"
        return
    }

    Write-Log "Installing VS Code..."
    winget install --id Microsoft.VisualStudioCode --accept-source-agreements --accept-package-agreements
    Write-LogSuccess "VS Code installed"

    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
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
    winget install --id MiKTeX.MiKTeX --accept-source-agreements --accept-package-agreements
    Write-LogSuccess "MiKTeX installed"

    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}

function Install-Dropbox {
    Write-Log "Checking Dropbox installation..."

    $dropboxPath = "$env:LOCALAPPDATA\Dropbox\Dropbox.exe"
    if (Test-Path $dropboxPath) {
        Write-LogSuccess "Dropbox already installed"
        return
    }

    # Also check Program Files
    if (Test-Path "C:\Program Files (x86)\Dropbox\Client\Dropbox.exe") {
        Write-LogSuccess "Dropbox already installed"
        return
    }

    Write-Log "Installing Dropbox..."
    winget install --id Dropbox.Dropbox --accept-source-agreements --accept-package-agreements
    Write-LogSuccess "Dropbox installed"
}

# ============================================================================
# Conda Installation
# ============================================================================

function Install-Conda {
    Write-Log "Checking Conda installation..."

    # Check if conda is in PATH
    if (Test-Command "conda") {
        $version = conda --version
        Write-LogSuccess "Conda already installed: $version"
        return
    }

    # Check common conda locations
    $condaPaths = @(
        "$env:USERPROFILE\miniconda3\Scripts\conda.exe",
        "$env:USERPROFILE\anaconda3\Scripts\conda.exe",
        "C:\ProgramData\miniconda3\Scripts\conda.exe",
        "C:\ProgramData\anaconda3\Scripts\conda.exe"
    )

    foreach ($path in $condaPaths) {
        if (Test-Path $path) {
            Write-LogSuccess "Conda found at $path"
            # Add to PATH for this session
            $condaDir = Split-Path -Parent (Split-Path -Parent $path)
            $env:Path = "$condaDir;$condaDir\Scripts;$condaDir\Library\bin;$env:Path"
            return
        }
    }

    Write-Log "Installing Miniconda..."

    $installerUrl = "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"
    $installerPath = "$env:TEMP\Miniconda3-latest-Windows-x86_64.exe"

    # Download installer
    Write-Log "Downloading Miniconda installer..."
    Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath

    # Run silent install
    Write-Log "Running Miniconda installer (this may take a few minutes)..."
    Start-Process -FilePath $installerPath -ArgumentList "/S", "/D=$env:USERPROFILE\miniconda3" -Wait

    # Clean up
    Remove-Item $installerPath -Force

    # Add to PATH for this session
    $env:Path = "$env:USERPROFILE\miniconda3;$env:USERPROFILE\miniconda3\Scripts;$env:USERPROFILE\miniconda3\Library\bin;$env:Path"

    # Initialize conda for PowerShell
    & "$env:USERPROFILE\miniconda3\Scripts\conda.exe" init powershell

    Write-LogSuccess "Miniconda installed"
}

# ============================================================================
# CDL Environment Setup
# ============================================================================

function Setup-CDLEnvironment {
    Write-Log "Setting up CDL conda environment..."

    # Ensure conda is available
    if (-not (Test-Command "conda")) {
        # Try to find conda
        $condaPath = "$env:USERPROFILE\miniconda3\Scripts\conda.exe"
        if (Test-Path $condaPath) {
            $env:Path = "$env:USERPROFILE\miniconda3;$env:USERPROFILE\miniconda3\Scripts;$env:USERPROFILE\miniconda3\Library\bin;$env:Path"
        }
        else {
            Write-LogError "Conda not found. Please restart PowerShell and run this script again."
            return
        }
    }

    # Check if cdl environment exists
    $envList = conda env list 2>&1
    if ($envList -match "^cdl\s") {
        Write-Log "CDL environment already exists, updating..."
        $updateEnv = $true
    }
    else {
        Write-Log "Creating CDL environment..."
        $updateEnv = $false
    }

    # Download environment file
    $envFile = "$env:TEMP\cdl-environment.yml"
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/ContextLab/lab-manual/master/scripts/cdl-environment.yml" -OutFile $envFile

    if ($updateEnv) {
        conda env update -n cdl -f $envFile --prune
    }
    else {
        conda env create -f $envFile
    }

    # Clean up
    Remove-Item $envFile -Force

    Write-LogSuccess "CDL environment configured"
}

# ============================================================================
# Verification
# ============================================================================

function Test-Installation {
    Write-Log "Verifying installation..."

    # Check Git
    if (Test-Command "git") {
        $version = git --version
        Write-LogSuccess "Git: $version"
    }
    else {
        Write-LogError "Git: NOT INSTALLED"
    }

    # Check Slack
    $slackPath = "$env:LOCALAPPDATA\slack\slack.exe"
    if ((Test-Path $slackPath) -or (Test-Path "C:\Program Files\Slack\Slack.exe")) {
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
    if (Test-Command "conda") {
        $version = conda --version
        Write-LogSuccess "Conda: $version"
    }
    else {
        Write-LogWarning "Conda: Not in PATH (restart PowerShell to activate)"
    }

    # Check CDL environment
    $envList = conda env list 2>&1
    if ($envList -match "^cdl\s") {
        Write-LogSuccess "CDL environment: Created"

        # Test key packages
        Write-Log "Testing Python packages in CDL environment..."

        conda activate cdl

        $packages = @("numpy", "pandas", "torch", "sklearn", "hypertools")
        foreach ($pkg in $packages) {
            try {
                $result = python -c "import $pkg; print(f'  $pkg: {$pkg.__version__}')" 2>&1
                if ($LASTEXITCODE -eq 0) {
                    Write-LogSuccess "  $pkg: OK"
                }
                else {
                    Write-LogWarning "  $pkg: FAILED"
                }
            }
            catch {
                Write-LogWarning "  $pkg: FAILED"
            }
        }

        conda deactivate
    }
    else {
        Write-LogWarning "CDL environment: Not found"
    }
}

# ============================================================================
# Summary
# ============================================================================

function Show-Summary {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "CDL Development Environment Setup Complete!" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Installed components:"
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
    Setup-CDLEnvironment

    # Verify installation
    Test-Installation

    # Print summary
    Show-Summary
}

# Run main
Main

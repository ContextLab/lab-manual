#!/bin/bash
#
# CDL Development Environment Setup Script
# Contextual Dynamics Laboratory, Dartmouth College
#
# This script sets up a complete development environment for CDL research.
# It is idempotent - safe to run multiple times.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/ContextLab/lab-manual/master/scripts/setup.sh | bash
#
# Or locally:
#   ./scripts/setup.sh
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Log file
LOG_FILE="${HOME}/.cdl-setup.log"

# ============================================================================
# Utility Functions
# ============================================================================

log() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${BLUE}[CDL Setup]${NC} $1"
    echo "[$timestamp] $1" >> "$LOG_FILE"
}

log_success() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${GREEN}[CDL Setup]${NC} $1"
    echo "[$timestamp] SUCCESS: $1" >> "$LOG_FILE"
}

log_warning() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${YELLOW}[CDL Setup]${NC} $1"
    echo "[$timestamp] WARNING: $1" >> "$LOG_FILE"
}

log_error() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${RED}[CDL Setup]${NC} $1"
    echo "[$timestamp] ERROR: $1" >> "$LOG_FILE"
}

command_exists() {
    command -v "$1" &> /dev/null
}

# ============================================================================
# Platform Detection
# ============================================================================

detect_platform() {
    log "Detecting platform..."

    OS="unknown"
    ARCH=$(uname -m)

    case "$(uname -s)" in
        Darwin)
            OS="macos"
            ;;
        Linux)
            if [ -f /etc/os-release ]; then
                . /etc/os-release
                if [[ "$ID" == "ubuntu" ]] || [[ "$ID_LIKE" == *"ubuntu"* ]] || [[ "$ID_LIKE" == *"debian"* ]]; then
                    OS="ubuntu"
                else
                    OS="linux"
                fi
            else
                OS="linux"
            fi
            ;;
        MINGW*|MSYS*|CYGWIN*)
            OS="windows"
            ;;
    esac

    log_success "Detected platform: $OS ($ARCH)"

    if [[ "$OS" == "unknown" ]]; then
        log_error "Unsupported operating system"
        exit 1
    fi
}

# ============================================================================
# Package Manager Installation
# ============================================================================

install_homebrew() {
    if [[ "$OS" != "macos" ]]; then
        return
    fi

    if command_exists brew; then
        log "Homebrew already installed, updating..."
        brew update || log_warning "Homebrew update failed, continuing..."
    else
        log "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

        # Add Homebrew to PATH for Apple Silicon
        if [[ "$ARCH" == "arm64" ]]; then
            echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
            eval "$(/opt/homebrew/bin/brew shellenv)"
        fi

        log_success "Homebrew installed"
    fi
}

update_apt() {
    if [[ "$OS" != "ubuntu" ]]; then
        return
    fi

    log "Updating apt package lists..."
    sudo apt update
    log_success "apt updated"
}

# ============================================================================
# Application Installation
# ============================================================================

install_git() {
    if command_exists git; then
        log_success "Git already installed: $(git --version)"
        return
    fi

    log "Installing Git..."

    case "$OS" in
        macos)
            brew install git
            ;;
        ubuntu)
            sudo apt install -y git
            ;;
    esac

    log_success "Git installed: $(git --version)"
}

install_slack() {
    log "Checking Slack installation..."

    case "$OS" in
        macos)
            if [ -d "/Applications/Slack.app" ]; then
                log_success "Slack already installed"
                return
            fi
            log "Installing Slack..."
            brew install --cask slack
            log_success "Slack installed"
            ;;
        ubuntu)
            if command_exists slack; then
                log_success "Slack already installed"
                return
            fi
            log "Installing Slack via snap..."
            sudo snap install slack --classic
            log_success "Slack installed"
            ;;
    esac
}

install_vscode() {
    log "Checking VS Code installation..."

    case "$OS" in
        macos)
            if [ -d "/Applications/Visual Studio Code.app" ] || command_exists code; then
                log_success "VS Code already installed"
                return
            fi
            log "Installing VS Code..."
            brew install --cask visual-studio-code
            log_success "VS Code installed"
            ;;
        ubuntu)
            if command_exists code; then
                log_success "VS Code already installed"
                return
            fi
            log "Installing VS Code via snap..."
            sudo snap install code --classic
            log_success "VS Code installed"
            ;;
    esac
}

install_latex() {
    log "Checking LaTeX installation..."

    case "$OS" in
        macos)
            if command_exists pdflatex; then
                log_success "LaTeX already installed"
                return
            fi
            log "Installing MacTeX (this may take a while)..."
            brew install --cask mactex-no-gui
            # Add TeX to PATH
            eval "$(/usr/libexec/path_helper)"
            log_success "MacTeX installed"
            ;;
        ubuntu)
            if command_exists pdflatex; then
                log_success "LaTeX already installed"
                return
            fi
            log "Installing TeX Live (this may take a while)..."
            sudo apt install -y texlive-full
            log_success "TeX Live installed"
            ;;
    esac
}

install_dropbox() {
    log "Checking Dropbox installation..."

    case "$OS" in
        macos)
            if [ -d "/Applications/Dropbox.app" ]; then
                log_success "Dropbox already installed"
                return
            fi
            log "Installing Dropbox..."
            brew install --cask dropbox
            log_success "Dropbox installed"
            ;;
        ubuntu)
            if command_exists dropbox; then
                log_success "Dropbox already installed"
                return
            fi
            log "Installing Dropbox..."
            # Install via official method
            cd ~ && wget -O - "https://www.dropbox.com/download?plat=lnx.x86_64" | tar xzf -
            log_success "Dropbox installed (run ~/.dropbox-dist/dropboxd to start)"
            ;;
    esac
}

# ============================================================================
# Conda Installation
# ============================================================================

install_conda() {
    log "Checking Conda installation..."

    # Check if conda is already installed
    if command_exists conda; then
        log_success "Conda already installed: $(conda --version)"
        return
    fi

    # Check common conda locations
    for conda_path in "$HOME/miniconda3/bin/conda" "$HOME/anaconda3/bin/conda" "/opt/miniconda3/bin/conda" "/opt/anaconda3/bin/conda"; do
        if [ -f "$conda_path" ]; then
            log_success "Conda found at $conda_path"
            # Initialize conda for the current shell
            eval "$($conda_path shell.bash hook)"
            return
        fi
    done

    log "Installing Miniconda..."

    # Determine installer URL based on platform
    case "$OS" in
        macos)
            if [[ "$ARCH" == "arm64" ]]; then
                MINICONDA_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-arm64.sh"
            else
                MINICONDA_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-x86_64.sh"
            fi
            ;;
        ubuntu|linux)
            MINICONDA_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh"
            ;;
    esac

    # Download and install
    INSTALLER="/tmp/miniconda_installer.sh"
    curl -fsSL "$MINICONDA_URL" -o "$INSTALLER"
    chmod +x "$INSTALLER"

    # Install in batch mode
    bash "$INSTALLER" -b -p "$HOME/miniconda3"

    # Clean up
    rm -f "$INSTALLER"

    # Initialize conda
    "$HOME/miniconda3/bin/conda" init bash
    "$HOME/miniconda3/bin/conda" init zsh 2>/dev/null || true

    # Source for current session
    eval "$($HOME/miniconda3/bin/conda shell.bash hook)"

    log_success "Miniconda installed"
}

# ============================================================================
# CDL Environment Setup
# ============================================================================

setup_cdl_environment() {
    log "Setting up CDL conda environment..."

    # Ensure conda is available in this session
    if ! command_exists conda; then
        # Try to source from common locations
        for conda_path in "$HOME/miniconda3/bin/conda" "$HOME/anaconda3/bin/conda"; do
            if [ -f "$conda_path" ]; then
                eval "$($conda_path shell.bash hook)"
                break
            fi
        done
    fi

    if ! command_exists conda; then
        log_error "Conda not found after installation. Please restart your shell and run this script again."
        exit 1
    fi

    # Check if cdl environment already exists
    if conda env list | grep -q "^cdl "; then
        log "CDL environment already exists, updating..."
        UPDATE_ENV=true
    else
        log "Creating CDL environment..."
        UPDATE_ENV=false
    fi

    # Get the directory where this script is located
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    ENV_FILE="$SCRIPT_DIR/cdl-environment.yml"

    # If running from curl, download the environment file
    if [ ! -f "$ENV_FILE" ]; then
        ENV_FILE="/tmp/cdl-environment.yml"
        curl -fsSL "https://raw.githubusercontent.com/ContextLab/lab-manual/master/scripts/cdl-environment.yml" -o "$ENV_FILE"
    fi

    if [ "$UPDATE_ENV" = true ]; then
        conda env update -n cdl -f "$ENV_FILE" --prune
    else
        conda env create -f "$ENV_FILE"
    fi

    log_success "CDL environment configured"

    # Clean up if we downloaded the file
    if [[ "$ENV_FILE" == "/tmp/cdl-environment.yml" ]]; then
        rm -f "$ENV_FILE"
    fi
}

# ============================================================================
# Verification
# ============================================================================

verify_installation() {
    log "Verifying installation..."

    local all_good=true

    # Check Git
    if command_exists git; then
        log_success "Git: $(git --version)"
    else
        log_error "Git: NOT INSTALLED"
        all_good=false
    fi

    # Check Slack
    case "$OS" in
        macos)
            if [ -d "/Applications/Slack.app" ]; then
                log_success "Slack: Installed"
            else
                log_warning "Slack: Not found in /Applications"
            fi
            ;;
        ubuntu)
            if command_exists slack; then
                log_success "Slack: Installed"
            else
                log_warning "Slack: Not found"
            fi
            ;;
    esac

    # Check VS Code
    if command_exists code; then
        log_success "VS Code: Installed"
    else
        log_warning "VS Code: Not in PATH (may require shell restart)"
    fi

    # Check LaTeX
    if command_exists pdflatex; then
        log_success "LaTeX: Installed"
    else
        log_warning "LaTeX: Not found (may require shell restart)"
    fi

    # Check Conda
    if command_exists conda; then
        log_success "Conda: $(conda --version)"
    else
        log_warning "Conda: Not in PATH (restart shell to activate)"
    fi

    # Check CDL environment
    if conda env list 2>/dev/null | grep -q "^cdl "; then
        log_success "CDL environment: Created"

        # Test key packages
        log "Testing Python packages in CDL environment..."

        # Activate the environment and test imports
        eval "$(conda shell.bash hook)"
        conda activate cdl

        python -c "import numpy; print(f'  numpy: {numpy.__version__}')" 2>/dev/null && log_success "  numpy: OK" || log_warning "  numpy: FAILED"
        python -c "import pandas; print(f'  pandas: {pandas.__version__}')" 2>/dev/null && log_success "  pandas: OK" || log_warning "  pandas: FAILED"
        python -c "import torch; print(f'  pytorch: {torch.__version__}')" 2>/dev/null && log_success "  pytorch: OK" || log_warning "  pytorch: FAILED"
        python -c "import sklearn; print(f'  scikit-learn: {sklearn.__version__}')" 2>/dev/null && log_success "  scikit-learn: OK" || log_warning "  scikit-learn: FAILED"
        python -c "import hypertools; print(f'  hypertools: {hypertools.__version__}')" 2>/dev/null && log_success "  hypertools: OK" || log_warning "  hypertools: FAILED"

        conda deactivate
    else
        log_warning "CDL environment: Not found"
        all_good=false
    fi

    if [ "$all_good" = true ]; then
        log_success "All core components verified!"
    else
        log_warning "Some components may need attention (see above)"
    fi
}

# ============================================================================
# Summary
# ============================================================================

print_summary() {
    echo ""
    echo "============================================================"
    echo -e "${GREEN}CDL Development Environment Setup Complete!${NC}"
    echo "============================================================"
    echo ""
    echo "Installed components:"
    echo "  - Git"
    echo "  - Slack"
    echo "  - VS Code"
    echo "  - LaTeX"
    echo "  - Dropbox"
    echo "  - Miniconda"
    echo "  - CDL conda environment"
    echo ""
    echo "To activate the CDL environment:"
    echo "  conda activate cdl"
    echo ""
    echo "Log file: $LOG_FILE"
    echo ""
    echo "Getting help:"
    echo "  - Software/hardware issues: help@dartmouth.edu"
    echo "  - Lab-specific issues: Slack or email Jeremy"
    echo "  - General questions: https://github.com/ContextLab/lab-manual/issues"
    echo ""
    echo -e "${YELLOW}NOTE: You may need to restart your shell for all changes to take effect.${NC}"
    echo ""
}

# ============================================================================
# Main
# ============================================================================

main() {
    echo ""
    echo "============================================================"
    echo "CDL Development Environment Setup"
    echo "Contextual Dynamics Laboratory, Dartmouth College"
    echo "============================================================"
    echo ""

    # Initialize log file
    echo "CDL Setup Log - $(date)" > "$LOG_FILE"
    echo "============================================================" >> "$LOG_FILE"

    # Detect platform
    detect_platform

    # Install package manager
    case "$OS" in
        macos)
            install_homebrew
            ;;
        ubuntu)
            update_apt
            ;;
    esac

    # Install applications
    install_git
    install_slack
    install_vscode
    install_latex
    install_dropbox

    # Install Conda and set up environment
    install_conda
    setup_cdl_environment

    # Verify installation
    verify_installation

    # Print summary
    print_summary
}

# Run main
main "$@"

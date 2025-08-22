#!/usr/bin/env bash
# setup_environment.sh
# -----------------------------------------------------------------------------
# Environment File Setup Script for Keuka Sensor
#
# PURPOSE:
#   Creates system environment files from templates with user customization.
#   Designed to be run during initial system setup or configuration updates.
#
# USAGE:
#   sudo ./deployment/environment/setup_environment.sh [options]
#
# OPTIONS:
#   --interactive    Prompt for configuration values
#   --defaults       Use template defaults without prompting
#   --force          Overwrite existing environment files
#   --help           Show this help message
#
# CREATES:
#   /etc/default/keuka-sensor - Service-specific environment variables
#   /etc/keuka.env            - Global system environment variables
# -----------------------------------------------------------------------------

set -euo pipefail

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE_DIR="$SCRIPT_DIR"

# System paths
DEFAULT_DIR="/etc/default"
KEUKA_ENV_FILE="/etc/keuka.env"

# Templates
SENSOR_TEMPLATE="$TEMPLATE_DIR/keuka-sensor.env.template"
GLOBAL_TEMPLATE="$TEMPLATE_DIR/keuka.env.template"

# Default settings
INTERACTIVE=false
USE_DEFAULTS=false
FORCE=false

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

show_help() {
    sed -n '/^# PURPOSE:/,/^# -/p' "$0" | sed 's/^# //; s/^#//'
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --interactive)
                INTERACTIVE=true
                shift
                ;;
            --defaults)
                USE_DEFAULTS=true
                shift
                ;;
            --force)
                FORCE=true
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root or with sudo"
        exit 1
    fi
}

validate_templates() {
    if [[ ! -f "$SENSOR_TEMPLATE" ]]; then
        log_error "Service template not found: $SENSOR_TEMPLATE"
        exit 1
    fi
    
    if [[ ! -f "$GLOBAL_TEMPLATE" ]]; then
        log_error "Global template not found: $GLOBAL_TEMPLATE"
        exit 1
    fi
}

setup_service_env() {
    local target_file="$DEFAULT_DIR/keuka-sensor"
    
    log_info "Setting up service environment file: $target_file"
    
    if [[ -f "$target_file" ]] && [[ "$FORCE" == "false" ]]; then
        log_warning "Environment file already exists: $target_file"
        read -p "Overwrite? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Skipping service environment setup"
            return 0
        fi
    fi
    
    # Copy template
    cp "$SENSOR_TEMPLATE" "$target_file"
    chmod 640 "$target_file"
    chown root:pi "$target_file"
    
    if [[ "$INTERACTIVE" == "true" ]]; then
        log_info "Interactive configuration not yet implemented"
        log_info "Please edit $target_file manually to customize settings"
    fi
    
    log_success "Service environment file created: $target_file"
}

setup_global_env() {
    local target_file="$KEUKA_ENV_FILE"
    
    log_info "Setting up global environment file: $target_file"
    
    if [[ -f "$target_file" ]] && [[ "$FORCE" == "false" ]]; then
        log_warning "Environment file already exists: $target_file"
        read -p "Overwrite? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Skipping global environment setup"
            return 0
        fi
    fi
    
    # Copy template
    cp "$GLOBAL_TEMPLATE" "$target_file"
    chmod 640 "$target_file"
    chown root:pi "$target_file"
    
    log_success "Global environment file created: $target_file"
}

main() {
    log_info "Setting up Keuka Sensor environment files..."
    
    check_root
    validate_templates
    
    # Ensure target directory exists
    mkdir -p "$DEFAULT_DIR"
    
    setup_service_env
    setup_global_env
    
    log_success "Environment setup completed!"
    echo
    log_info "Next steps:"
    log_info "1. Edit $DEFAULT_DIR/keuka-sensor to customize service settings"
    log_info "2. Edit $KEUKA_ENV_FILE to customize global settings"
    log_info "3. Run: sudo systemctl daemon-reload"
    log_info "4. Run: sudo systemctl restart keuka-sensor.service"
}

# Script entry point
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    parse_args "$@"
    main
fi
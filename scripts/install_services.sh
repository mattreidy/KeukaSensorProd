#!/usr/bin/env bash
# install_services.sh
# -----------------------------------------------------------------------------
# Keuka Sensor System Service Installation Script
#
# PURPOSE:
#   Installs systemd services and timers by copying files from the repository
#   to their proper system locations. Does not modify or embed configuration.
#
# SERVICES INSTALLED:
#   - keuka-sensor.service: Main Flask application service
#   - duckdns-update.service: DuckDNS dynamic DNS updater
#   - duckdns-update.timer: Timer for periodic DuckDNS updates
#   - log-cleanup.service: Log cleanup and rotation service
#   - log-cleanup.timer: Timer for periodic log cleanup
#
# REQUIREMENTS:
#   - Must be run as root or with sudo
#   - Repository must be accessible at the expected location
#
# USAGE:
#   sudo ./scripts/install_services.sh [options]
#
# OPTIONS:
#   --dry-run           Show what would be done without making changes
#   --force             Overwrite existing service files without prompting
#   --skip-enable       Skip enabling services (install only)
#   --skip-start        Skip starting services after installation
#   --help              Show this help message
#
# EXIT CODES:
#   0: Success
#   1: General error
#   2: Missing requirements
#   3: Permission error (not running as root)
#   4: Service installation failed
# -----------------------------------------------------------------------------

set -euo pipefail

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SYSTEMD_SOURCE_DIR="$REPO_ROOT/systemd"
SYSTEMD_TARGET_DIR="/etc/systemd/system"

# Default settings
DRY_RUN=false
FORCE=false
SKIP_ENABLE=false
SKIP_START=false

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
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

# Help function
show_help() {
    sed -n '/^# PURPOSE:/,/^# -/p' "$0" | sed 's/^# //; s/^#//'
}

# Parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --dry-run)
                DRY_RUN=true
                log_info "Dry run mode enabled - no changes will be made"
                shift
                ;;
            --force)
                FORCE=true
                shift
                ;;
            --skip-enable)
                SKIP_ENABLE=true
                shift
                ;;
            --skip-start)
                SKIP_START=true
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                echo "Use --help for usage information"
                exit 1
                ;;
        esac
    done
}

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root or with sudo"
        log_info "Usage: sudo $0"
        exit 3
    fi
}

# Validate system requirements
validate_requirements() {
    log_info "Validating system requirements..."
    
    local errors=0
    
    # Check if systemd source directory exists
    if [[ ! -d "$SYSTEMD_SOURCE_DIR" ]]; then
        log_error "Systemd source directory not found at: $SYSTEMD_SOURCE_DIR"
        ((errors++))
    fi
    
    # Check if systemd target directory exists
    if [[ ! -d "$SYSTEMD_TARGET_DIR" ]]; then
        log_error "Systemd target directory not found at: $SYSTEMD_TARGET_DIR"
        ((errors++))
    fi
    
    # Check required service files exist in source
    local required_files=(
        "keuka-sensor.service"
        "duckdns-update.service"
        "duckdns-update.timer"
        "log-cleanup.service"
        "log-cleanup.timer"
    )
    
    for file in "${required_files[@]}"; do
        if [[ ! -f "$SYSTEMD_SOURCE_DIR/$file" ]]; then
            log_error "Required service file not found: $SYSTEMD_SOURCE_DIR/$file"
            ((errors++))
        fi
    done
    
    # Check systemctl availability
    if ! command -v systemctl >/dev/null 2>&1; then
        log_error "systemctl command not found - systemd is required"
        ((errors++))
    fi
    
    if [[ $errors -gt 0 ]]; then
        log_error "Validation failed with $errors error(s)"
        exit 2
    fi
    
    log_success "System requirements validation passed"
}

# Install a single service file
install_service_file() {
    local service_file="$1"
    local source_path="$SYSTEMD_SOURCE_DIR/$service_file"
    local target_path="$SYSTEMD_TARGET_DIR/$service_file"
    
    if [[ ! -f "$source_path" ]]; then
        log_error "Source service file not found: $source_path"
        return 1
    fi
    
    if [[ "$DRY_RUN" == "false" ]]; then
        if [[ -f "$target_path" ]] && [[ "$FORCE" == "false" ]]; then
            log_warning "$service_file already exists in $SYSTEMD_TARGET_DIR"
            read -p "Overwrite? (y/N): " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                log_info "Skipping $service_file"
                return 0
            fi
        fi
        
        log_info "Installing $service_file"
        cp "$source_path" "$target_path"
        chmod 644 "$target_path"
        chown root:root "$target_path"
        log_success "Installed $service_file"
    else
        log_info "[DRY RUN] Would copy $source_path to $target_path"
    fi
    
    return 0
}

# Install all service files
install_services() {
    log_info "Installing systemd service files from repository..."
    
    local service_files=(
        "keuka-sensor.service"
        "duckdns-update.service"
        "duckdns-update.timer"
        "log-cleanup.service"
        "log-cleanup.timer"
    )
    
    local failed=0
    for service_file in "${service_files[@]}"; do
        if ! install_service_file "$service_file"; then
            ((failed++))
        fi
    done
    
    if [[ $failed -gt 0 ]]; then
        log_error "$failed service file(s) failed to install"
        return 1
    fi
    
    log_success "All service files installed successfully"
    return 0
}

# Reload systemd daemon
reload_systemd() {
    log_info "Reloading systemd daemon..."
    if [[ "$DRY_RUN" == "false" ]]; then
        if systemctl daemon-reload; then
            log_success "Systemd daemon reloaded"
        else
            log_error "Failed to reload systemd daemon"
            return 1
        fi
    else
        log_info "[DRY RUN] Would reload systemd daemon"
    fi
    return 0
}

# Enable services
enable_services() {
    if [[ "$SKIP_ENABLE" == "true" ]]; then
        log_info "Skipping service enablement"
        return 0
    fi
    
    log_info "Enabling services..."
    
    local services=(
        "keuka-sensor.service"
        "duckdns-update.timer"
        "log-cleanup.timer"
    )
    
    local failed=0
    for service in "${services[@]}"; do
        if [[ "$DRY_RUN" == "false" ]]; then
            if systemctl enable "$service"; then
                log_success "Enabled $service"
            else
                log_error "Failed to enable $service"
                ((failed++))
            fi
        else
            log_info "[DRY RUN] Would enable $service"
        fi
    done
    
    if [[ $failed -gt 0 ]]; then
        log_error "$failed service(s) failed to enable"
        return 1
    fi
    
    return 0
}

# Start services
start_services() {
    if [[ "$SKIP_START" == "true" ]]; then
        log_info "Skipping service startup"
        return 0
    fi
    
    log_info "Starting services..."
    
    # Only start the timers - the main service may need additional setup
    local services=(
        "duckdns-update.timer"
        "log-cleanup.timer"
    )
    
    local failed=0
    for service in "${services[@]}"; do
        if [[ "$DRY_RUN" == "false" ]]; then
            if systemctl start "$service"; then
                log_success "Started $service"
            else
                log_error "Failed to start $service"
                ((failed++))
            fi
        else
            log_info "[DRY RUN] Would start $service"
        fi
    done
    
    log_info "keuka-sensor.service is enabled but not started automatically"
    log_info "Start it manually when ready: sudo systemctl start keuka-sensor.service"
    
    if [[ $failed -gt 0 ]]; then
        log_error "$failed service(s) failed to start"
        return 1
    fi
    
    return 0
}

# Show service status
show_status() {
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "Skipping status display in dry run mode"
        return 0
    fi
    
    log_info "Service status:"
    echo
    
    local services=(
        "keuka-sensor.service"
        "duckdns-update.service"
        "duckdns-update.timer"
        "log-cleanup.service"
        "log-cleanup.timer"
    )
    
    for service in "${services[@]}"; do
        echo -e "${BLUE}=== $service ===${NC}"
        systemctl status "$service" --no-pager --lines=0 2>/dev/null || echo "Service not active"
        echo
    done
}

# Main installation function
main() {
    log_info "Starting Keuka Sensor service installation..."
    log_info "Source: $SYSTEMD_SOURCE_DIR"
    log_info "Target: $SYSTEMD_TARGET_DIR"
    
    # Validation
    check_root
    validate_requirements
    
    # Installation steps
    install_services || exit 4
    reload_systemd || exit 4
    enable_services || exit 4
    start_services || exit 4
    
    # Final status
    show_status
    
    log_success "Keuka Sensor service installation completed!"
    echo
    log_info "Services installed and enabled. Configure environment files as needed:"
    log_info "- /etc/default/keuka-sensor (service environment variables)"
    log_info "- /etc/keuka.env (global environment variables)"
    echo
    log_info "Start the main service when ready:"
    log_info "  sudo systemctl start keuka-sensor.service"
    echo
    log_info "View service logs with:"
    log_info "  sudo journalctl -u keuka-sensor.service -f"
    log_info "  sudo journalctl -u duckdns-update.service -f"
}

# Script entry point
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    parse_args "$@"
    main
fi
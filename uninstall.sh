#!/usr/bin/env bash
set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }

echo -e "${BOLD}BlueWard Uninstaller${NC}"
echo ""

# Stop and disable service
systemctl --user disable --now blueward.service 2>/dev/null && \
    info "Service stopped and disabled" || true

# Remove service file
rm -f "$HOME/.config/systemd/user/blueward.service"
systemctl --user daemon-reload 2>/dev/null
info "Service file removed"

# Uninstall Python package
python3 -m pip uninstall -y blueward 2>/dev/null && \
    info "Python package removed" || warn "Package not found (already removed?)"

# Config
echo ""
read -rp "Remove config (~/.config/blueward/)? [y/N] " answer
if [[ "${answer,,}" == "y" ]]; then
    rm -rf "$HOME/.config/blueward"
    info "Config removed"
else
    info "Config kept at ~/.config/blueward/"
fi

echo ""
echo -e "${GREEN}BlueWard uninstalled.${NC}"

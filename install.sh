#!/usr/bin/env bash
set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; exit 1; }

echo -e "${BOLD}BlueWard Installer${NC}"
echo "Bluetooth proximity-based screen lock for Linux"
echo ""

# --- Checks ---

if [[ "$OSTYPE" != "linux-gnu"* ]]; then
    error "BlueWard only supports Linux"
fi

PYTHON=python3
if ! command -v $PYTHON &>/dev/null; then
    error "python3 not found. Install it first."
fi

PY_VERSION=$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$($PYTHON -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$($PYTHON -c 'import sys; print(sys.version_info.minor)')

if (( PY_MAJOR < 3 || (PY_MAJOR == 3 && PY_MINOR < 11) )); then
    error "Python 3.11+ required (found $PY_VERSION)"
fi
info "Python $PY_VERSION"

# --- Detect package manager ---

if command -v apt &>/dev/null; then
    PKG_MGR="apt"
elif command -v dnf &>/dev/null; then
    PKG_MGR="dnf"
elif command -v pacman &>/dev/null; then
    PKG_MGR="pacman"
else
    warn "Unknown package manager. You'll need to install system deps manually."
    PKG_MGR="unknown"
fi

# --- Install system dependencies ---

info "Installing system dependencies..."

if [[ "$PKG_MGR" == "apt" ]]; then
    sudo apt install -y bluez python3-dbus python3-gi libnotify-bin 2>/dev/null
    # AppIndicator is optional (tray icon)
    sudo apt install -y gir1.2-appindicator3-0.1 2>/dev/null || warn "AppIndicator not available — tray icon disabled"
elif [[ "$PKG_MGR" == "dnf" ]]; then
    sudo dnf install -y bluez python3-dbus python3-gobject libnotify 2>/dev/null
    sudo dnf install -y libappindicator-gtk3 2>/dev/null || warn "AppIndicator not available — tray icon disabled"
elif [[ "$PKG_MGR" == "pacman" ]]; then
    sudo pacman -S --noconfirm --needed bluez python-dbus python-gobject libnotify 2>/dev/null
    sudo pacman -S --noconfirm --needed libappindicator-gtk3 2>/dev/null || warn "AppIndicator not available — tray icon disabled"
else
    warn "Please install manually: bluez python3-dbus python3-gi libnotify-bin"
fi

# --- Install BlueWard ---

info "Installing BlueWard..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

$PYTHON -m pip install --user --break-system-packages . 2>/dev/null \
    || $PYTHON -m pip install --user . 2>/dev/null \
    || error "pip install failed. Try: $PYTHON -m pip install --user ."

# Ensure ~/.local/bin is on PATH
LOCAL_BIN="$HOME/.local/bin"
if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
    warn "$LOCAL_BIN is not on your PATH. Add this to your ~/.bashrc:"
    warn "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# --- Copy default config ---

CONFIG_DIR="$HOME/.config/blueward"
CONFIG_FILE="$CONFIG_DIR/config.toml"
mkdir -p "$CONFIG_DIR"

if [[ -f "$CONFIG_FILE" ]]; then
    info "Config exists at $CONFIG_FILE (not overwriting)"
else
    cp "$SCRIPT_DIR/config.toml" "$CONFIG_FILE"
    info "Default config installed to $CONFIG_FILE"
fi

# --- Install systemd user service ---

SERVICE_DIR="$HOME/.config/systemd/user"
mkdir -p "$SERVICE_DIR"
cp "$SCRIPT_DIR/blueward.service" "$SERVICE_DIR/blueward.service"
systemctl --user daemon-reload
info "Systemd user service installed"

# --- Set l2ping capability ---

L2PING_PATH=$(which l2ping 2>/dev/null || command -v l2ping 2>/dev/null || echo "")
if [[ -n "$L2PING_PATH" ]]; then
    sudo setcap cap_net_raw+ep "$L2PING_PATH" 2>/dev/null && \
        info "l2ping capability set (no sudo needed at runtime)" || \
        warn "Could not set l2ping capability. Run: sudo setcap cap_net_raw+ep $L2PING_PATH"
else
    warn "l2ping not found. Classic BT fallback won't work."
fi

# --- Done ---

echo ""
echo -e "${BOLD}${GREEN}BlueWard installed successfully!${NC}"
echo ""
echo "Next steps:"
echo "  1. Find your phone's Bluetooth MAC address:"
echo "     ${BOLD}blueward scan${NC}"
echo ""
echo "  2. Edit the config with your device MAC:"
echo "     ${BOLD}nano ~/.config/blueward/config.toml${NC}"
echo ""
echo "  3. Test it:"
echo "     ${BOLD}blueward --verbose --no-tray${NC}"
echo ""
echo "  4. Enable auto-start on login:"
echo "     ${BOLD}systemctl --user enable --now blueward${NC}"
echo ""
echo "  5. Check logs:"
echo "     ${BOLD}journalctl --user -u blueward -f${NC}"

"""Interactive setup wizard for BlueWard.

Guides the user through selecting a Bluetooth device and writing config.
"""

import os
import subprocess
import sys
import tomllib

BOLD = "\033[1m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
RED = "\033[0;31m"
DIM = "\033[2m"
NC = "\033[0m"

CONFIG_DIR = os.path.expanduser("~/.config/blueward")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.toml")

DEFAULT_CONFIG_TEMPLATE = """\
[blueward]
scan_interval = 2.0
lock_delay = 4
unlock_delay = 1
notifications = true
tray_icon = true
log_rssi = false
rssi_log_path = "~/.local/share/blueward/rssi.log"

[[devices]]
name = "{name}"
mac = "{mac}"
rssi_at_1m = -55

[devices.zones]
immediate = -45
near = -60
far = -75

[blueward.policy]
mode = "all"

[actions]
lock_command = ""
unlock_command = ""
on_lock_extra = ""
on_unlock_extra = ""

[filter]
process_noise = 0.008
measurement_noise = 4.0
ema_alpha = 0.3
method = "kalman"
"""


def _print_header():
    print()
    print(f"{BOLD}{CYAN}╔══════════════════════════════════════╗{NC}")
    print(f"{BOLD}{CYAN}║       BlueWard Setup Wizard          ║{NC}")
    print(f"{BOLD}{CYAN}╚══════════════════════════════════════╝{NC}")
    print()


def _check_adapter() -> bool:
    """Check if a Bluetooth adapter is available and powered."""
    try:
        result = subprocess.run(
            ["bluetoothctl", "show"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return False
        powered = any("Powered: yes" in line for line in result.stdout.splitlines())
        if not powered:
            print(f"{YELLOW}[!]{NC} Bluetooth adapter is powered off. Trying to power on...")
            subprocess.run(
                ["bluetoothctl", "power", "on"],
                capture_output=True, timeout=5,
            )
            # Re-check
            result = subprocess.run(
                ["bluetoothctl", "show"],
                capture_output=True, text=True, timeout=5,
            )
            powered = any("Powered: yes" in line for line in result.stdout.splitlines())
            if powered:
                print(f"{GREEN}[+]{NC} Bluetooth powered on")
            else:
                print(f"{RED}[✗]{NC} Could not power on Bluetooth adapter")
                return False
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _get_paired_devices() -> list[tuple[str, str]]:
    """Get list of paired Bluetooth devices as (mac, name) tuples."""
    try:
        result = subprocess.run(
            ["bluetoothctl", "devices", "Paired"],
            capture_output=True, text=True, timeout=5,
        )
        devices = []
        for line in result.stdout.strip().splitlines():
            # Format: "Device XX:XX:XX:XX:XX:XX Name"
            parts = line.strip().split(" ", 2)
            if len(parts) >= 3 and parts[0] == "Device":
                mac = parts[1].upper()
                name = parts[2]
                devices.append((mac, name))
        return devices
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def _get_connected_devices() -> set[str]:
    """Get set of currently connected device MACs."""
    try:
        result = subprocess.run(
            ["bluetoothctl", "devices", "Connected"],
            capture_output=True, text=True, timeout=5,
        )
        connected = set()
        for line in result.stdout.strip().splitlines():
            parts = line.strip().split(" ", 2)
            if len(parts) >= 2 and parts[0] == "Device":
                connected.add(parts[1].upper())
        return connected
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()


def _scan_for_devices(duration: int = 10) -> list[tuple[str, str]]:
    """Scan for nearby Bluetooth devices. Returns (mac, name) tuples."""
    print(f"\n{CYAN}Scanning for nearby Bluetooth devices ({duration}s)...{NC}")
    print(f"{DIM}Make sure your phone's Bluetooth is on and discoverable.{NC}\n")

    try:
        # Start scanning
        scan_proc = subprocess.Popen(
            ["bluetoothctl", "--timeout", str(duration), "scan", "on"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        scan_proc.wait(timeout=duration + 5)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        if scan_proc:
            scan_proc.kill()

    # Now list all devices BlueZ knows about (includes scan results)
    try:
        result = subprocess.run(
            ["bluetoothctl", "devices"],
            capture_output=True, text=True, timeout=5,
        )
        devices = []
        seen_macs = set()
        for line in result.stdout.strip().splitlines():
            parts = line.strip().split(" ", 2)
            if len(parts) >= 3 and parts[0] == "Device":
                mac = parts[1].upper()
                name = parts[2]
                # Skip unnamed devices (just MAC repeated as name)
                if mac not in seen_macs and name != mac.replace(":", "-"):
                    devices.append((mac, name))
                    seen_macs.add(mac)
        return devices
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def _select_device(devices: list[tuple[str, str]], connected: set[str]) -> tuple[str, str] | None:
    """Display device list and let user select one."""
    print(f"\n{BOLD}Available Bluetooth devices:{NC}\n")
    print(f"  {'#':<4} {'Name':<30} {'MAC Address':<20} {'Status'}")
    print(f"  {'─'*4} {'─'*30} {'─'*20} {'─'*10}")

    for i, (mac, name) in enumerate(devices, 1):
        status = f"{GREEN}Connected{NC}" if mac in connected else f"{DIM}Paired{NC}"
        print(f"  {i:<4} {name:<30} {mac:<20} {status}")

    print()
    while True:
        try:
            choice = input(f"{BOLD}Select device number (or 'q' to quit): {NC}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if choice.lower() == 'q':
            return None
        try:
            idx = int(choice)
            if 1 <= idx <= len(devices):
                return devices[idx - 1]
            print(f"{RED}  Invalid choice. Enter 1-{len(devices)}.{NC}")
        except ValueError:
            print(f"{RED}  Invalid input. Enter a number.{NC}")


def _test_device(mac: str) -> bool:
    """Quick l2ping test to verify device is reachable."""
    print(f"\n{CYAN}Testing connection to {mac}...{NC}")
    try:
        result = subprocess.run(
            ["l2ping", "-c", "2", "-t", "1", mac],
            capture_output=True, text=True, timeout=8,
        )
        if result.returncode == 0:
            print(f"{GREEN}[+]{NC} Device is reachable!")
            return True
        else:
            print(f"{YELLOW}[!]{NC} l2ping failed (device may use BLE only)")
            return False
    except FileNotFoundError:
        print(f"{YELLOW}[!]{NC} l2ping not found — skipping connectivity test")
        return False
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}[!]{NC} Connection test timed out")
        return False


def _write_config(mac: str, name: str):
    """Write or update the BlueWard config file with the selected device."""
    os.makedirs(CONFIG_DIR, exist_ok=True)

    if os.path.exists(CONFIG_FILE):
        # Update existing config — replace the first device entry
        with open(CONFIG_FILE, "rb") as f:
            existing = tomllib.load(f)

        # Read raw text to do a targeted replacement
        with open(CONFIG_FILE, "r") as f:
            content = f.read()

        # Check if there's already a [[devices]] section
        if "[[devices]]" in content:
            # Replace the first device's mac and name
            import re
            content = re.sub(
                r'(\[\[devices\]\]\s*\n\s*name\s*=\s*)"[^"]*"',
                rf'\1"{name}"',
                content,
                count=1,
            )
            content = re.sub(
                r'(mac\s*=\s*)"[^"]*"',
                rf'\1"{mac}"',
                content,
                count=1,
            )
            with open(CONFIG_FILE, "w") as f:
                f.write(content)
        else:
            # Append device section
            with open(CONFIG_FILE, "a") as f:
                f.write(f'\n[[devices]]\nname = "{name}"\nmac = "{mac}"\n')
                f.write('rssi_at_1m = -55\n\n')
                f.write('[devices.zones]\nimmediate = -45\nnear = -60\nfar = -75\n')
    else:
        # Write fresh config
        content = DEFAULT_CONFIG_TEMPLATE.format(name=name, mac=mac)
        with open(CONFIG_FILE, "w") as f:
            f.write(content)

    print(f"{GREEN}[+]{NC} Config saved to {CONFIG_FILE}")


def run_setup():
    """Main interactive setup wizard."""
    _print_header()

    # Step 1: Check adapter
    print(f"{BOLD}Step 1:{NC} Checking Bluetooth adapter...")
    if not _check_adapter():
        print(f"\n{RED}[✗]{NC} No Bluetooth adapter found or bluetoothctl not available.")
        print("    Install BlueZ: sudo apt install bluez")
        sys.exit(1)
    print(f"{GREEN}[+]{NC} Bluetooth adapter is ready\n")

    # Step 2: Look for paired devices
    print(f"{BOLD}Step 2:{NC} Looking for paired Bluetooth devices...")
    paired = _get_paired_devices()
    connected = _get_connected_devices()

    if paired:
        print(f"{GREEN}[+]{NC} Found {len(paired)} paired device(s)")
        selected = _select_device(paired, connected)

        if selected is None:
            # User wants to scan for new devices instead
            print(f"\n{BOLD}Scanning for new devices...{NC}")
            scanned = _scan_for_devices()
            if not scanned:
                print(f"{RED}[✗]{NC} No devices found. Make sure your device is discoverable.")
                sys.exit(1)
            selected = _select_device(scanned, connected)
            if selected is None:
                print("Setup cancelled.")
                sys.exit(0)
    else:
        print(f"{YELLOW}[!]{NC} No paired devices found")
        print()
        print(f"  {BOLD}Please pair your phone first:{NC}")
        print(f"  1. Open Bluetooth settings on your phone")
        print(f"  2. Make your phone discoverable")
        print(f"  3. On this computer, pair via Settings → Bluetooth")
        print(f"     Or run: {BOLD}bluetoothctl{NC}")
        print()

        answer = input(f"Scan for nearby devices anyway? [Y/n] ").strip()
        if answer.lower() == 'n':
            print("Setup cancelled. Pair a device first, then run 'blueward setup' again.")
            sys.exit(0)

        scanned = _scan_for_devices()
        if not scanned:
            print(f"\n{RED}[✗]{NC} No devices found. Make sure your device is discoverable.")
            sys.exit(1)
        selected = _select_device(scanned, connected)
        if selected is None:
            print("Setup cancelled.")
            sys.exit(0)

    mac, name = selected
    print(f"\n{GREEN}Selected:{NC} {BOLD}{name}{NC} ({mac})")

    # Step 3: Custom name
    custom = input(f"\nDevice name [{name}]: ").strip()
    if custom:
        name = custom

    # Step 4: Test connectivity
    _test_device(mac)

    # Step 5: Write config
    print(f"\n{BOLD}Step 3:{NC} Saving configuration...")
    _write_config(mac, name)

    # Done
    print()
    print(f"{BOLD}{GREEN}╔══════════════════════════════════════╗{NC}")
    print(f"{BOLD}{GREEN}║         Setup Complete!              ║{NC}")
    print(f"{BOLD}{GREEN}╚══════════════════════════════════════╝{NC}")
    print()
    print(f"  Device: {BOLD}{name}{NC} ({mac})")
    print(f"  Config: {CONFIG_FILE}")
    print()
    print(f"  Next steps:")
    print(f"    Test it:     {BOLD}blueward --verbose --no-tray{NC}")
    print(f"    Auto-start:  {BOLD}systemctl --user enable --now blueward{NC}")
    print()

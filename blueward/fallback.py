"""Fallback proximity detection for Classic Bluetooth devices."""

import logging
import subprocess

log = logging.getLogger(__name__)


def try_l2ping(address: str, timeout: int = 1) -> bool:
    """Ping a Classic Bluetooth device via L2CAP. Returns True if reachable."""
    try:
        result = subprocess.run(
            ["l2ping", "-c", "1", "-t", str(timeout), address],
            capture_output=True, timeout=timeout + 2,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def try_hci_rssi(address: str) -> int | None:
    """Get RSSI via hcitool for an actively connected Classic BT device."""
    try:
        result = subprocess.run(
            ["hcitool", "rssi", address],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and "RSSI return value" in result.stdout:
            return int(result.stdout.strip().split(":")[1].strip())
    except Exception:
        pass
    return None

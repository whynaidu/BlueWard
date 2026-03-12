"""Screen lock/unlock control for Linux desktop environments.

Supports GNOME (org.gnome.ScreenSaver), KDE (org.freedesktop.ScreenSaver),
and a universal fallback via loginctl.
"""

import logging
import subprocess

log = logging.getLogger(__name__)


def _try_dbus_lock() -> bool:
    """Attempt to lock via GNOME ScreenSaver D-Bus interface."""
    try:
        import dbus
        bus = dbus.SessionBus()
        screensaver = dbus.Interface(
            bus.get_object("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver"),
            "org.gnome.ScreenSaver",
        )
        screensaver.Lock()
        return True
    except Exception:
        return False


def _try_loginctl_lock() -> bool:
    """Lock via loginctl (works on any systemd-based desktop)."""
    try:
        result = subprocess.run(
            ["loginctl", "lock-session"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def lock_screen() -> bool:
    """Lock the screen using the best available method.

    Returns True if lock was triggered successfully.
    """
    if _try_dbus_lock():
        log.info("Screen locked via GNOME ScreenSaver D-Bus")
        return True

    if _try_loginctl_lock():
        log.info("Screen locked via loginctl")
        return True

    log.error("Failed to lock screen: no working method found")
    return False


def is_locked() -> bool:
    """Check if the screen is currently locked (GNOME only)."""
    try:
        import dbus
        bus = dbus.SessionBus()
        screensaver = dbus.Interface(
            bus.get_object("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver"),
            "org.gnome.ScreenSaver",
        )
        return bool(screensaver.GetActive())
    except Exception:
        return False


def unlock_screen() -> bool:
    """Attempt to unlock the screen via loginctl.

    Note: On GNOME, this does NOT work - GNOME Shell requires PAM authentication.
    This works on some lightweight DEs. Returns True if the signal was sent.
    """
    try:
        result = subprocess.run(
            ["loginctl", "unlock-session"],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            log.info("Unlock signal sent via loginctl")
            return True
    except Exception:
        pass

    log.warning("unlock-session sent but may not work on GNOME (PAM required)")
    return False


def run_custom_command(command: str) -> bool:
    """Run a user-defined shell command (e.g. 'playerctl pause')."""
    if not command:
        return True
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            log.warning("Custom command failed: %s (exit %d)", command, result.returncode)
            return False
        return True
    except subprocess.TimeoutExpired:
        log.warning("Custom command timed out: %s", command)
        return False

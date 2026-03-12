"""Desktop notifications via notify-send."""

import logging
import subprocess

log = logging.getLogger(__name__)

APP_NAME = "BlueWard"


def notify(
    title: str,
    body: str,
    icon: str = "bluetooth",
    urgency: str = "normal",
    timeout_ms: int = 5000,
):
    """Send a desktop notification via notify-send."""
    try:
        subprocess.run(
            [
                "notify-send",
                f"--urgency={urgency}",
                f"--icon={icon}",
                f"--expire-time={timeout_ms}",
                f"--app-name={APP_NAME}",
                title,
                body,
            ],
            capture_output=True,
            timeout=5,
        )
    except FileNotFoundError:
        log.warning("notify-send not found; install libnotify-bin for notifications")
    except Exception as e:
        log.warning("Notification failed: %s", e)


def notify_locked(device_name: str):
    notify(
        APP_NAME,
        f"Screen locked \u2014 {device_name} out of range",
        icon="system-lock-screen",
        urgency="normal",
    )


def notify_device_nearby(device_name: str):
    notify(
        APP_NAME,
        f"{device_name} detected nearby. Ready to unlock.",
        icon="bluetooth-active",
        urgency="low",
    )


def notify_adapter_error():
    notify(
        APP_NAME,
        "Bluetooth adapter error. Check that Bluetooth is enabled.",
        icon="dialog-error",
        urgency="critical",
    )


def notify_started():
    notify(
        APP_NAME,
        "Proximity monitoring active.",
        icon="bluetooth-active",
        urgency="low",
        timeout_ms=3000,
    )

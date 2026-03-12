"""System tray icon using AppIndicator3 (GTK)."""

import logging

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AppIndicator3", "0.1")
from gi.repository import Gtk, AppIndicator3, GLib  # noqa: E402

log = logging.getLogger(__name__)

ICONS = {
    "near": "bluetooth-active",
    "away": "bluetooth-disabled",
    "scanning": "bluetooth-paired",
    "error": "dialog-error",
    "paused": "bluetooth-offline",
}


class TrayIcon:
    """System tray indicator showing BlueWard status."""

    def __init__(self, service):
        self._service = service

        self._indicator = AppIndicator3.Indicator.new(
            "blueward",
            ICONS["scanning"],
            AppIndicator3.IndicatorCategory.SYSTEM_SERVICES,
        )
        self._indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self._indicator.set_title("BlueWard")
        self._indicator.set_menu(self._build_menu())

        # Register for state changes
        service.on_state_change(self._on_state_change)

    def _build_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()

        self._status_item = Gtk.MenuItem(label="Status: Initializing")
        self._status_item.set_sensitive(False)
        menu.append(self._status_item)

        self._rssi_item = Gtk.MenuItem(label="RSSI: --")
        self._rssi_item.set_sensitive(False)
        menu.append(self._rssi_item)

        self._device_item = Gtk.MenuItem(label="Device: --")
        self._device_item.set_sensitive(False)
        menu.append(self._device_item)

        menu.append(Gtk.SeparatorMenuItem())

        self._pause_item = Gtk.MenuItem(label="Pause")
        self._pause_item.connect("activate", self._on_pause)
        menu.append(self._pause_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self._on_quit)
        menu.append(quit_item)

        menu.show_all()
        return menu

    def _on_state_change(self, old_state, new_state, info):
        """Called by the service when state changes."""
        GLib.idle_add(self._do_update, new_state, info)

    def _do_update(self, state, info):
        from .service import State

        state_labels = {
            State.DEVICE_NEAR: ("Device nearby", "near"),
            State.DEVICE_LEAVING: ("Device leaving...", "scanning"),
            State.DEVICE_AWAY: ("Device away (locked)", "away"),
            State.DEVICE_APPROACHING: ("Device approaching...", "scanning"),
            State.SCANNING: ("Scanning...", "scanning"),
            State.ADAPTER_ERROR: ("Adapter error", "error"),
            State.SUSPENDED: ("Paused", "paused"),
            State.INITIALIZING: ("Initializing", "scanning"),
        }

        label, icon_key = state_labels.get(state, ("Unknown", "scanning"))
        self._indicator.set_icon_full(ICONS[icon_key], label)
        self._status_item.set_label(f"Status: {label}")

        # Update RSSI display from device info
        devices = info.get("devices", {})
        if devices:
            # Show the first/primary device
            for mac, dinfo in devices.items():
                rssi = dinfo.get("rssi")
                name = dinfo.get("name", mac)
                zone = dinfo.get("zone", "unknown")
                self._device_item.set_label(f"Device: {name} ({zone})")
                if rssi is not None:
                    self._rssi_item.set_label(f"RSSI: {rssi:.0f} dBm")
                else:
                    self._rssi_item.set_label("RSSI: --")
                break

        # Update pause label
        if self._service.is_paused:
            self._pause_item.set_label("Resume")
        else:
            self._pause_item.set_label("Pause")

        return False  # Remove from idle queue

    def _on_pause(self, widget):
        self._service.toggle_pause()

    def _on_quit(self, widget):
        self._service.shutdown()
        Gtk.main_quit()

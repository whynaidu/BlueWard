"""Core BlueWard service: state machine, event loop, and coordination."""

import logging
import signal
import threading
import time
from enum import Enum, auto

from gi.repository import GLib

from .config import Config
from .devices import DeviceRegistry, TrackedDevice
from .fallback import try_l2ping
from .notifier import notify_locked, notify_device_nearby, notify_adapter_error, notify_started
from .proximity import ProximityZone, classify_zone
from .scanner import BlueZAdapter, ActiveScanner, PassiveScanner, init_dbus_mainloop
from .screen import lock_screen, unlock_screen, is_locked, run_custom_command

log = logging.getLogger(__name__)


class State(Enum):
    INITIALIZING = auto()
    SCANNING = auto()
    DEVICE_NEAR = auto()       # Trusted device in range
    DEVICE_LEAVING = auto()    # RSSI dropping, grace period
    DEVICE_AWAY = auto()       # Screen locked
    DEVICE_APPROACHING = auto()  # RSSI rising, confirming return
    ADAPTER_ERROR = auto()
    SUSPENDED = auto()


class BlueWardService:
    """Main service coordinating scanning, proximity, and screen actions."""

    def __init__(self, config: Config):
        self.config = config
        self.state = State.INITIALIZING
        self._state_entered_at = time.monotonic()
        self._paused = False
        self._loop: GLib.MainLoop | None = None
        self._timeout_id: int | None = None

        # Callbacks for UI (tray icon updates)
        self._on_state_change: list = []

        # Device registry
        self.devices = DeviceRegistry(
            devices=config.devices,
            filter_config=config.filter,
            policy_mode=config.policy_mode,
        )

        # RSSI logging
        self._rssi_log_file = None
        if config.log_rssi:
            from pathlib import Path
            log_path = Path(config.rssi_log_path).expanduser()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._rssi_log_file = open(log_path, "a")
            self._rssi_log_file.write("timestamp,device,raw_rssi,smoothed_rssi,zone\n")

    def on_state_change(self, callback):
        """Register a callback for state transitions: callback(old_state, new_state, device_info)."""
        self._on_state_change.append(callback)

    def start(self):
        """Initialize scanning and start the GLib main loop."""
        init_dbus_mainloop()

        signal.signal(signal.SIGTERM, lambda *_: self.shutdown())
        signal.signal(signal.SIGHUP, lambda *_: log.info("SIGHUP received (config reload not yet supported)"))

        try:
            adapter = BlueZAdapter(f"/org/bluez/{self.config.devices[0].mac if False else 'hci0'}")
            if not adapter.ensure_powered():
                log.error("Could not power on Bluetooth adapter")
                self._transition(State.ADAPTER_ERROR)
                if self.config.notifications:
                    notify_adapter_error()
                return
        except Exception as e:
            log.error("Failed to connect to BlueZ: %s", e)
            self._transition(State.ADAPTER_ERROR)
            if self.config.notifications:
                notify_adapter_error()
            return

        # Start BLE scanner - try passive (AdvertisementMonitor) first, fall back to active
        try:
            self._scanner = PassiveScanner(
                adapter=adapter,
                on_rssi=self._handle_rssi,
                on_lost=self._handle_device_lost,
                trusted_macs=self.devices.trusted_macs,
                rssi_high=self.config.devices[0].zones.near if self.config.devices else -60,
                rssi_low=self.config.devices[0].zones.far if self.config.devices else -75,
            )
            self._scanner.start()
            log.info("Using passive AdvertisementMonitor scanning")
        except Exception:
            log.info("AdvertisementMonitor not available, falling back to active discovery")
            self._scanner = ActiveScanner(
                adapter=adapter,
                on_rssi=self._handle_rssi,
                trusted_macs=self.devices.trusted_macs,
            )
            self._scanner.start()

        self._transition(State.SCANNING)

        # Periodic timeout check (every 2 seconds)
        self._timeout_id = GLib.timeout_add_seconds(2, self._check_timeouts)

        if self.config.notifications:
            notify_started()

        self._loop = GLib.MainLoop()
        log.info("BlueWard service started, monitoring %d device(s)", len(self.config.devices))

        try:
            self._loop.run()
        except KeyboardInterrupt:
            self.shutdown()

    def shutdown(self):
        """Clean up and stop the service."""
        log.info("Shutting down BlueWard")
        if hasattr(self, "_scanner"):
            self._scanner.stop()
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
        if self._rssi_log_file:
            self._rssi_log_file.close()
        if self._loop and self._loop.is_running():
            self._loop.quit()

    def toggle_pause(self):
        """Toggle pause state."""
        if self._paused:
            self._paused = False
            self._transition(State.SCANNING)
            log.info("BlueWard resumed")
        else:
            self._paused = True
            self._transition(State.SUSPENDED)
            log.info("BlueWard paused")

    @property
    def is_paused(self) -> bool:
        return self._paused

    def _handle_rssi(self, mac: str, rssi: int):
        """Callback from scanner when an RSSI reading is received."""
        if self._paused:
            return

        device = self.devices.get(mac)
        if device is None:
            return

        smoothed = device.update_rssi(rssi)

        # Classify into proximity zone
        new_zone = classify_zone(
            smoothed_rssi=smoothed,
            thresholds=device.config.zones,
            current_zone=device.zone,
        )

        old_zone = device.zone
        if new_zone != old_zone:
            device.zone = new_zone
            log.info(
                "%s zone: %s -> %s (RSSI: raw=%d smoothed=%.1f)",
                device.name, old_zone.value, new_zone.value, rssi, smoothed,
            )
            self._handle_zone_transition(device, old_zone, new_zone)

        # Log RSSI if enabled
        if self._rssi_log_file:
            import datetime
            ts = datetime.datetime.now().isoformat()
            self._rssi_log_file.write(
                f"{ts},{device.name},{rssi},{smoothed:.1f},{device.zone.value}\n"
            )
            self._rssi_log_file.flush()

        # Notify UI
        for cb in self._on_state_change:
            cb(self.state, self.state, self._device_summary())

    def _handle_device_lost(self, mac: str):
        """Callback from AdvMonitor when a device is declared lost."""
        if self._paused:
            return

        device = self.devices.get(mac)
        if device is None:
            return

        device.zone = ProximityZone.OUT_OF_RANGE
        log.info("%s: device lost (AdvertisementMonitor)", device.name)
        self._evaluate_lock_state()

    def _handle_zone_transition(self, device: TrackedDevice, old_zone: ProximityZone, new_zone: ProximityZone):
        """React to a device changing proximity zone."""
        self._evaluate_lock_state()

    def _evaluate_lock_state(self):
        """Evaluate whether to lock/unlock based on all device states."""
        if self.devices.should_lock():
            if self.state not in (State.DEVICE_AWAY, State.DEVICE_LEAVING):
                self._transition(State.DEVICE_LEAVING)
                # Grace period is handled by the timeout checker
        elif self.devices.any_device_near():
            if self.state in (State.DEVICE_AWAY, State.DEVICE_LEAVING):
                # Transitioning from away/leaving: go through APPROACHING first
                if self.config.notifications:
                    for d in self.devices.all_devices():
                        if d.zone in (ProximityZone.NEAR, ProximityZone.IMMEDIATE):
                            notify_device_nearby(d.name)
                            break
                self._transition(State.DEVICE_APPROACHING)
            elif self.state == State.SCANNING:
                # First detection after startup: go directly to DEVICE_NEAR
                self._transition(State.DEVICE_NEAR)
                if is_locked():
                    unlock_screen()
                    log.info("Screen unlocked — device detected")
                if self.config.actions.on_unlock_extra:
                    run_custom_command(self.config.actions.on_unlock_extra)

    def _check_timeouts(self) -> bool:
        """Periodic check for stale devices and grace period expiry. Returns True to keep running."""
        if self._paused:
            return True

        now = time.monotonic()

        # l2ping fallback for Classic BT devices that don't show up in BLE scans.
        # Only poll when device state is uncertain — NOT when already confirmed NEAR.
        # This avoids constant BT connection churn on the phone.
        L2PING_INTERVAL = 5  # seconds between l2ping attempts
        for device in self.devices.all_devices():
            last_ping = getattr(device, '_last_l2ping', 0)
            since_ping = now - last_ping

            # Skip l2ping if:
            # - device is confirmed NEAR/IMMEDIATE and was seen recently (no need to re-check)
            # - a ping is already in flight
            # - we pinged too recently
            if device.zone in (ProximityZone.NEAR, ProximityZone.IMMEDIATE) and device.age < L2PING_INTERVAL:
                continue
            if getattr(device, '_l2ping_pending', False):
                continue
            if since_ping < L2PING_INTERVAL:
                continue

            device._l2ping_pending = True
            device._last_l2ping = now
            threading.Thread(
                target=self._l2ping_poll,
                args=(device.mac,),
                daemon=True,
            ).start()

        # Check for stale devices (no signal received)
        no_signal_timeout = self.config.lock_delay * 3  # 3x lock_delay for total silence
        for device in self.devices.stale_devices(no_signal_timeout):
            if device.zone != ProximityZone.OUT_OF_RANGE:
                log.warning("%s: no signal for %.0fs, marking out of range", device.name, device.age)
                device.zone = ProximityZone.OUT_OF_RANGE

        # Handle DEVICE_LEAVING grace period
        if self.state == State.DEVICE_LEAVING:
            elapsed = now - self._state_entered_at
            if elapsed >= self.config.lock_delay:
                # Grace period expired - still should lock?
                if self.devices.should_lock():
                    self._do_lock()

        # Handle DEVICE_APPROACHING confirmation period
        if self.state == State.DEVICE_APPROACHING:
            elapsed = now - self._state_entered_at
            if elapsed >= self.config.unlock_delay:
                if self.devices.any_device_near():
                    self._transition(State.DEVICE_NEAR)
                    # Auto-unlock if screen is locked
                    if is_locked():
                        unlock_screen()
                        log.info("Screen unlocked — device confirmed nearby")
                    if self.config.actions.on_unlock_extra:
                        run_custom_command(self.config.actions.on_unlock_extra)
                else:
                    # Device disappeared again during approach
                    self._transition(State.DEVICE_AWAY)

        # Re-evaluate in case stale device detection changed things
        if self.state not in (State.DEVICE_AWAY, State.SUSPENDED, State.ADAPTER_ERROR):
            if self.devices.should_lock() and self.state != State.DEVICE_LEAVING:
                self._transition(State.DEVICE_LEAVING)

        return True  # Keep timer running

    def _l2ping_poll(self, mac: str):
        """Run l2ping in a background thread, then schedule result on the main loop."""
        reachable = try_l2ping(mac, timeout=2)
        GLib.idle_add(self._l2ping_result, mac, reachable)

    def _l2ping_result(self, mac: str, reachable: bool):
        """Handle l2ping result on the GLib main thread."""
        device = self.devices.get(mac)
        if device is None:
            return False
        device._l2ping_pending = False

        if reachable:
            device.last_seen = time.monotonic()
            if device.zone == ProximityZone.OUT_OF_RANGE:
                log.info("%s: reachable via l2ping, marking NEAR", device.name)
                device.zone = ProximityZone.NEAR
                self._evaluate_lock_state()
            log.debug("%s: l2ping OK", device.name)
        else:
            if device.zone not in (ProximityZone.FAR, ProximityZone.OUT_OF_RANGE):
                log.info("%s: l2ping failed, marking FAR", device.name)
                device.zone = ProximityZone.FAR
                self._evaluate_lock_state()
        return False  # Remove from idle queue

    def _do_lock(self):
        """Execute the lock action."""
        if self.state == State.DEVICE_AWAY:
            return  # Already locked

        self._transition(State.DEVICE_AWAY)

        if not is_locked():
            lock_screen()
            log.info("Screen locked")

            if self.config.actions.on_lock_extra:
                run_custom_command(self.config.actions.on_lock_extra)

            if self.config.notifications:
                # Find the device that triggered the lock
                for d in self.devices.all_devices():
                    if d.zone == ProximityZone.OUT_OF_RANGE:
                        notify_locked(d.name)
                        break

    def _transition(self, new_state: State):
        """Transition to a new state."""
        old = self.state
        if old == new_state:
            return

        self.state = new_state
        self._state_entered_at = time.monotonic()
        log.debug("State: %s -> %s", old.name, new_state.name)

        for cb in self._on_state_change:
            cb(old, new_state, self._device_summary())

    def _device_summary(self) -> dict:
        """Build a summary of device states for UI callbacks."""
        devices = {}
        for d in self.devices.all_devices():
            devices[d.mac] = {
                "name": d.name,
                "zone": d.zone.value,
                "rssi": d.smoothed_rssi,
                "age": d.age,
            }
        return {"state": self.state.name, "devices": devices}

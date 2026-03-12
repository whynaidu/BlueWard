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

    # States where we need fast, responsive polling
    _ACTIVE_STATES = frozenset({
        State.SCANNING, State.DEVICE_LEAVING, State.DEVICE_APPROACHING,
    })

    def __init__(self, config: Config):
        self.config = config
        self.state = State.INITIALIZING
        self._state_entered_at = time.monotonic()
        self._paused = False
        self._loop: GLib.MainLoop | None = None
        self._timeout_id: int | None = None
        self._current_check_interval: int = config.timing.check_interval

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
            log.error("")
            log.error("Troubleshooting steps:")
            log.error("  1. Check if Bluetooth hardware is present:  rfkill list bluetooth")
            log.error("  2. Start the BlueZ service:                 sudo systemctl start bluetooth")
            log.error("  3. Enable it on boot:                       sudo systemctl enable bluetooth")
            log.error("  4. If no adapter exists, you need a USB Bluetooth dongle")
            log.error("")
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
                rssi_high_timeout=self.config.timing.rssi_high_timeout,
                rssi_low_timeout=self.config.timing.rssi_low_timeout,
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

        # Run an initial l2ping check for all devices before evaluating state,
        # so we don't falsely trigger DEVICE_LEAVING on startup.
        self._startup_ping_pending = len(self.config.devices)
        for device in self.devices.all_devices():
            device._l2ping_pending = True
            device._last_l2ping = time.monotonic()
            threading.Thread(
                target=self._startup_ping,
                args=(device.mac,),
                daemon=True,
            ).start()

        # Periodic timeout check — start after a short delay to let startup pings complete
        self._timeout_id = GLib.timeout_add_seconds(
            self.config.timing.check_interval, self._check_timeouts
        )

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
        # Don't evaluate until startup pings have completed
        if getattr(self, '_startup_ping_pending', 0) > 0:
            return

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

        # Don't evaluate lock state until startup pings have completed
        if getattr(self, '_startup_ping_pending', 0) > 0:
            return True

        now = time.monotonic()
        is_stable = self.state not in self._ACTIVE_STATES

        # l2ping fallback for Classic BT devices that don't show up in BLE scans.
        # In stable states (DEVICE_NEAR, DEVICE_AWAY), ping much less often to save battery.
        base_interval = self.config.timing.l2ping_interval
        if is_stable:
            l2ping_interval = base_interval * self.config.timing.idle_l2ping_multiplier
        else:
            l2ping_interval = base_interval

        for device in self.devices.all_devices():
            last_ping = getattr(device, '_last_l2ping', 0)
            since_ping = now - last_ping

            if getattr(device, '_l2ping_pending', False):
                continue
            if since_ping < l2ping_interval:
                continue

            device._l2ping_pending = True
            device._last_l2ping = now
            threading.Thread(
                target=self._l2ping_poll,
                args=(device.mac,),
                daemon=True,
            ).start()

        # Check for stale devices (no signal received)
        # In idle mode, stale timeout must be at least 2× the idle l2ping interval
        # to avoid false positives from slow polling.
        base_stale = self.config.timing.lock_delay * self.config.timing.stale_multiplier
        if is_stable:
            idle_l2ping = base_interval * self.config.timing.idle_l2ping_multiplier
            no_signal_timeout = max(base_stale, idle_l2ping * 2)
        else:
            no_signal_timeout = base_stale
        for device in self.devices.stale_devices(no_signal_timeout):
            if device.zone != ProximityZone.OUT_OF_RANGE:
                log.warning("%s: no signal for %.0fs, marking out of range", device.name, device.age)
                device.zone = ProximityZone.OUT_OF_RANGE

        # Handle DEVICE_LEAVING grace period
        if self.state == State.DEVICE_LEAVING:
            elapsed = now - self._state_entered_at
            if elapsed >= self.config.timing.lock_delay:
                # Grace period expired - still should lock?
                if self.devices.should_lock():
                    self._do_lock()

        # Handle DEVICE_APPROACHING confirmation period
        if self.state == State.DEVICE_APPROACHING:
            elapsed = now - self._state_entered_at
            if elapsed >= self.config.timing.unlock_delay:
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

    def _startup_ping(self, mac: str):
        """Initial l2ping on startup to detect devices before evaluating state."""
        reachable = try_l2ping(mac, timeout=self.config.timing.l2ping_timeout)
        GLib.idle_add(self._startup_ping_result, mac, reachable)

    def _startup_ping_result(self, mac: str, reachable: bool):
        """Handle startup l2ping result on the GLib main thread."""
        device = self.devices.get(mac)
        if device is not None:
            device._l2ping_pending = False
            if reachable:
                device.last_seen = time.monotonic()
                device.zone = ProximityZone.NEAR
                log.info("%s: found nearby on startup via l2ping", device.name)
            else:
                log.info("%s: not reachable on startup", device.name)

        self._startup_ping_pending -= 1
        if self._startup_ping_pending <= 0:
            # All startup pings done — now evaluate initial state
            self._startup_ping_pending = 0
            self._evaluate_lock_state()
            log.debug("Startup device check complete")
        return False

    def _l2ping_poll(self, mac: str):
        """Run l2ping in a background thread, then schedule result on the main loop."""
        reachable = try_l2ping(mac, timeout=self.config.timing.l2ping_timeout)
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

    def _get_check_interval(self, state: State) -> int:
        """Return the appropriate check interval for a given state."""
        base = self.config.timing.check_interval
        if state in self._ACTIVE_STATES:
            return base
        return base * self.config.timing.idle_poll_multiplier

    def _reschedule_timer(self, new_interval: int):
        """Reschedule the GLib check timer if the interval changed."""
        if new_interval == self._current_check_interval:
            return
        old = self._current_check_interval
        self._current_check_interval = new_interval
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
        self._timeout_id = GLib.timeout_add_seconds(new_interval, self._check_timeouts)
        log.debug("Poll interval: %ds -> %ds", old, new_interval)

    def _transition(self, new_state: State):
        """Transition to a new state."""
        old = self.state
        if old == new_state:
            return

        self.state = new_state
        self._state_entered_at = time.monotonic()
        log.debug("State: %s -> %s", old.name, new_state.name)

        # Adapt polling speed: fast for active states, slow for stable states
        self._reschedule_timer(self._get_check_interval(new_state))

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

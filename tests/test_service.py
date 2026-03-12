"""Tests for blueward.service — BlueWardService state transitions.

All external dependencies (GLib, dbus, scanner, screen, notifier) are mocked
so tests run without a Bluetooth adapter or D-Bus session.
"""

import sys
import time
from unittest.mock import patch, MagicMock

import pytest

# Mock external C-extension dependencies before any blueward imports.
# These modules require system libraries (D-Bus, GLib) not available in test environments.
_mock_gi = MagicMock()
_mock_glib = MagicMock()
_mock_gi.repository.GLib = _mock_glib
sys.modules.setdefault("gi", _mock_gi)
sys.modules.setdefault("gi.repository", _mock_gi.repository)

_mock_dbus = MagicMock()
sys.modules.setdefault("dbus", _mock_dbus)
sys.modules.setdefault("dbus.service", MagicMock())
sys.modules.setdefault("dbus.mainloop", MagicMock())
sys.modules.setdefault("dbus.mainloop.glib", MagicMock())

from blueward.config import ActionsConfig, Config, Device, FilterConfig, TimingConfig, ZoneThresholds
from blueward.devices import DeviceRegistry
from blueward.proximity import ProximityZone
from blueward.service import BlueWardService, State


def _make_config(
    lock_delay=8,
    unlock_delay=3,
    notifications=False,
    policy_mode="all",
    log_rssi=False,
):
    return Config(
        scan_interval=2.0,
        lock_delay=lock_delay,
        unlock_delay=unlock_delay,
        notifications=notifications,
        tray_icon=False,
        log_rssi=log_rssi,
        policy_mode=policy_mode,
        devices=[
            Device(
                name="Phone",
                mac="AA:BB:CC:DD:EE:FF",
                rssi_at_1m=-55,
                zones=ZoneThresholds(immediate=-45, near=-60, far=-75),
            ),
        ],
        filter=FilterConfig(),
    )


def _make_two_device_config(policy_mode="all"):
    return Config(
        scan_interval=2.0,
        lock_delay=8,
        unlock_delay=3,
        notifications=False,
        tray_icon=False,
        log_rssi=False,
        policy_mode=policy_mode,
        devices=[
            Device(name="Phone", mac="AA:BB:CC:DD:EE:FF", rssi_at_1m=-55,
                   zones=ZoneThresholds(immediate=-45, near=-60, far=-75)),
            Device(name="Watch", mac="11:22:33:44:55:66", rssi_at_1m=-55,
                   zones=ZoneThresholds(immediate=-45, near=-60, far=-75)),
        ],
        filter=FilterConfig(),
    )


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestInit:
    def test_initial_state(self):
        svc = BlueWardService(_make_config())
        assert svc.state == State.INITIALIZING

    def test_devices_registered(self):
        svc = BlueWardService(_make_config())
        assert svc.devices.is_trusted("AA:BB:CC:DD:EE:FF")

    def test_not_paused_initially(self):
        svc = BlueWardService(_make_config())
        assert svc.is_paused is False


# ---------------------------------------------------------------------------
# _handle_rssi — zone transitions drive state changes
# ---------------------------------------------------------------------------

class TestHandleRssi:
    def test_strong_rssi_transitions_to_device_near(self):
        """A strong RSSI reading should move from SCANNING to DEVICE_NEAR."""
        svc = BlueWardService(_make_config())
        svc._transition(State.SCANNING)

        # Feed strong RSSI readings to get device into NEAR or IMMEDIATE zone
        for _ in range(5):
            svc._handle_rssi("AA:BB:CC:DD:EE:FF", -40)

        device = svc.devices.get("AA:BB:CC:DD:EE:FF")
        assert device.zone in (ProximityZone.NEAR, ProximityZone.IMMEDIATE)
        assert svc.state == State.DEVICE_NEAR

    def test_weak_rssi_transitions_to_device_leaving(self):
        """A weak RSSI should move to DEVICE_LEAVING (grace period)."""
        svc = BlueWardService(_make_config())
        svc._transition(State.SCANNING)

        # First bring device into range
        for _ in range(10):
            svc._handle_rssi("AA:BB:CC:DD:EE:FF", -40)
        assert svc.state == State.DEVICE_NEAR

        # Now send very weak RSSI to push device out of range
        for _ in range(20):
            svc._handle_rssi("AA:BB:CC:DD:EE:FF", -95)

        device = svc.devices.get("AA:BB:CC:DD:EE:FF")
        assert device.zone in (ProximityZone.FAR, ProximityZone.OUT_OF_RANGE)
        assert svc.state == State.DEVICE_LEAVING

    def test_unknown_mac_ignored(self):
        svc = BlueWardService(_make_config())
        svc._transition(State.SCANNING)
        svc._handle_rssi("00:00:00:00:00:00", -50)
        assert svc.state == State.SCANNING

    def test_paused_ignores_rssi(self):
        svc = BlueWardService(_make_config())
        svc._transition(State.SCANNING)
        svc._paused = True
        svc._handle_rssi("AA:BB:CC:DD:EE:FF", -40)
        # State should not change while paused
        assert svc.state == State.SCANNING


# ---------------------------------------------------------------------------
# _handle_device_lost
# ---------------------------------------------------------------------------

class TestHandleDeviceLost:
    def test_device_lost_marks_oor(self):
        svc = BlueWardService(_make_config())
        svc._transition(State.DEVICE_NEAR)
        device = svc.devices.get("AA:BB:CC:DD:EE:FF")
        device.zone = ProximityZone.NEAR

        svc._handle_device_lost("AA:BB:CC:DD:EE:FF")

        assert device.zone == ProximityZone.OUT_OF_RANGE
        assert svc.state == State.DEVICE_LEAVING

    def test_device_lost_unknown_mac_ignored(self):
        svc = BlueWardService(_make_config())
        svc._transition(State.DEVICE_NEAR)
        svc._handle_device_lost("00:00:00:00:00:00")
        assert svc.state == State.DEVICE_NEAR

    def test_device_lost_while_paused_ignored(self):
        svc = BlueWardService(_make_config())
        svc._transition(State.DEVICE_NEAR)
        svc._paused = True
        device = svc.devices.get("AA:BB:CC:DD:EE:FF")
        device.zone = ProximityZone.NEAR

        svc._handle_device_lost("AA:BB:CC:DD:EE:FF")
        # Zone should NOT change while paused
        assert device.zone == ProximityZone.NEAR


# ---------------------------------------------------------------------------
# _check_timeouts — grace period expiry and stale devices
# ---------------------------------------------------------------------------

class TestCheckTimeouts:
    @patch("blueward.service.lock_screen", return_value=True)
    @patch("blueward.service.is_locked", return_value=False)
    def test_grace_period_expiry_locks(self, mock_is_locked, mock_lock):
        svc = BlueWardService(_make_config(lock_delay=0))
        svc._transition(State.DEVICE_LEAVING)
        # All devices are OOR by default, so should_lock() is True
        # With lock_delay=0, grace period immediately expires

        svc._check_timeouts()

        assert svc.state == State.DEVICE_AWAY
        mock_lock.assert_called_once()

    @patch("blueward.service.lock_screen")
    @patch("blueward.service.is_locked", return_value=False)
    def test_grace_period_not_expired(self, mock_is_locked, mock_lock):
        svc = BlueWardService(_make_config(lock_delay=999))
        svc._transition(State.DEVICE_LEAVING)

        svc._check_timeouts()

        # Should still be DEVICE_LEAVING since grace period hasn't expired
        assert svc.state == State.DEVICE_LEAVING
        mock_lock.assert_not_called()

    def test_stale_device_marked_oor(self):
        svc = BlueWardService(_make_config(lock_delay=8))
        svc._transition(State.SCANNING)
        device = svc.devices.get("AA:BB:CC:DD:EE:FF")
        device.zone = ProximityZone.NEAR
        # Fake old last_seen (older than lock_delay * 3 = 24s)
        device.last_seen = time.monotonic() - 30.0

        svc._check_timeouts()

        assert device.zone == ProximityZone.OUT_OF_RANGE

    def test_paused_skips_timeout_check(self):
        svc = BlueWardService(_make_config(lock_delay=0))
        svc._transition(State.DEVICE_LEAVING)
        svc._paused = True

        result = svc._check_timeouts()

        assert result is True
        assert svc.state == State.DEVICE_LEAVING

    @patch("blueward.service.run_custom_command", return_value=True)
    @patch("blueward.service.lock_screen")
    @patch("blueward.service.is_locked", return_value=False)
    def test_approaching_confirmed_transitions_to_device_near(self, mock_is_locked, mock_lock, mock_cmd):
        svc = BlueWardService(_make_config(unlock_delay=0))
        svc._transition(State.DEVICE_APPROACHING)
        device = svc.devices.get("AA:BB:CC:DD:EE:FF")
        device.zone = ProximityZone.NEAR
        # Give a recent last_seen so it isn't flagged stale
        device.last_seen = time.monotonic()

        svc._check_timeouts()

        assert svc.state == State.DEVICE_NEAR

    @patch("blueward.service.lock_screen")
    @patch("blueward.service.is_locked", return_value=False)
    def test_approaching_device_disappeared_goes_to_away(self, mock_is_locked, mock_lock):
        svc = BlueWardService(_make_config(unlock_delay=0))
        svc._transition(State.DEVICE_APPROACHING)
        # Device is OOR (default), so any_device_near() is False

        svc._check_timeouts()

        assert svc.state == State.DEVICE_AWAY

    def test_returns_true_to_keep_timer(self):
        svc = BlueWardService(_make_config())
        assert svc._check_timeouts() is True


# ---------------------------------------------------------------------------
# _do_lock
# ---------------------------------------------------------------------------

class TestDoLock:
    @patch("blueward.service.lock_screen", return_value=True)
    @patch("blueward.service.is_locked", return_value=False)
    def test_locks_screen(self, mock_is_locked, mock_lock):
        svc = BlueWardService(_make_config())
        svc._transition(State.DEVICE_LEAVING)

        svc._do_lock()

        assert svc.state == State.DEVICE_AWAY
        mock_lock.assert_called_once()

    @patch("blueward.service.lock_screen")
    @patch("blueward.service.is_locked", return_value=True)
    def test_already_locked_screen_not_locked_again(self, mock_is_locked, mock_lock):
        svc = BlueWardService(_make_config())
        svc._transition(State.DEVICE_LEAVING)

        svc._do_lock()

        assert svc.state == State.DEVICE_AWAY
        mock_lock.assert_not_called()

    @patch("blueward.service.lock_screen")
    @patch("blueward.service.is_locked")
    def test_already_in_device_away_noop(self, mock_is_locked, mock_lock):
        svc = BlueWardService(_make_config())
        svc._transition(State.DEVICE_AWAY)

        svc._do_lock()

        mock_lock.assert_not_called()


# ---------------------------------------------------------------------------
# toggle_pause
# ---------------------------------------------------------------------------

class TestTogglePause:
    def test_pause(self):
        svc = BlueWardService(_make_config())
        svc._transition(State.SCANNING)
        svc.toggle_pause()
        assert svc.is_paused is True
        assert svc.state == State.SUSPENDED

    def test_resume(self):
        svc = BlueWardService(_make_config())
        svc._transition(State.SCANNING)
        svc.toggle_pause()  # pause
        svc.toggle_pause()  # resume
        assert svc.is_paused is False
        assert svc.state == State.SCANNING


# ---------------------------------------------------------------------------
# _transition
# ---------------------------------------------------------------------------

class TestTransition:
    def test_same_state_noop(self):
        svc = BlueWardService(_make_config())
        svc._transition(State.SCANNING)
        t1 = svc._state_entered_at
        time.sleep(0.01)
        svc._transition(State.SCANNING)
        # Timestamp should NOT change for same-state transition
        assert svc._state_entered_at == t1

    def test_callback_invoked(self):
        svc = BlueWardService(_make_config())
        transitions = []
        svc.on_state_change(lambda old, new, info: transitions.append((old, new)))

        svc._transition(State.SCANNING)
        svc._transition(State.DEVICE_NEAR)

        assert len(transitions) == 2
        assert transitions[0] == (State.INITIALIZING, State.SCANNING)
        assert transitions[1] == (State.SCANNING, State.DEVICE_NEAR)


# ---------------------------------------------------------------------------
# _evaluate_lock_state
# ---------------------------------------------------------------------------

class TestEvaluateLockState:
    def test_should_lock_enters_device_leaving(self):
        svc = BlueWardService(_make_config())
        svc._transition(State.SCANNING)
        # All devices default to OOR, so should_lock() == True
        svc._evaluate_lock_state()
        assert svc.state == State.DEVICE_LEAVING

    def test_device_near_transitions_from_scanning(self):
        svc = BlueWardService(_make_config())
        svc._transition(State.SCANNING)
        device = svc.devices.get("AA:BB:CC:DD:EE:FF")
        device.zone = ProximityZone.NEAR

        svc._evaluate_lock_state()
        assert svc.state == State.DEVICE_NEAR

    @patch("blueward.service.notify_device_nearby")
    def test_device_approaching_from_device_away_with_notifications(self, mock_notify):
        """From DEVICE_AWAY, detecting a near device goes to DEVICE_APPROACHING (not DEVICE_NEAR)."""
        svc = BlueWardService(_make_config(notifications=True))
        svc._transition(State.DEVICE_AWAY)
        device = svc.devices.get("AA:BB:CC:DD:EE:FF")
        device.zone = ProximityZone.NEAR

        svc._evaluate_lock_state()

        assert svc.state == State.DEVICE_APPROACHING
        mock_notify.assert_called_once_with("Phone")

    @patch("blueward.service.notify_device_nearby")
    def test_device_approaching_from_device_leaving(self, mock_notify):
        """From DEVICE_LEAVING, detecting a near device goes to DEVICE_APPROACHING."""
        svc = BlueWardService(_make_config(notifications=False))
        svc._transition(State.DEVICE_LEAVING)
        device = svc.devices.get("AA:BB:CC:DD:EE:FF")
        device.zone = ProximityZone.NEAR

        svc._evaluate_lock_state()

        assert svc.state == State.DEVICE_APPROACHING
        mock_notify.assert_not_called()

    def test_already_device_away_stays(self):
        svc = BlueWardService(_make_config())
        svc._transition(State.DEVICE_AWAY)
        # All OOR, should_lock() is True, but already in DEVICE_AWAY
        svc._evaluate_lock_state()
        assert svc.state == State.DEVICE_AWAY


# ---------------------------------------------------------------------------
# Multi-device scenarios
# ---------------------------------------------------------------------------

class TestMultiDevice:
    def test_all_policy_needs_all_away(self):
        """With 'all' policy, both devices must be away to trigger lock."""
        svc = BlueWardService(_make_two_device_config(policy_mode="all"))
        svc._transition(State.SCANNING)

        # One device near, one OOR -> should NOT lock
        svc.devices.get("AA:BB:CC:DD:EE:FF").zone = ProximityZone.NEAR
        svc._evaluate_lock_state()
        assert svc.state == State.DEVICE_NEAR

    def test_any_policy_one_away_triggers(self):
        """With 'any' policy, one device away triggers lock."""
        svc = BlueWardService(_make_two_device_config(policy_mode="any"))
        svc._transition(State.SCANNING)

        svc.devices.get("AA:BB:CC:DD:EE:FF").zone = ProximityZone.NEAR
        # Watch is OOR by default
        svc._evaluate_lock_state()
        assert svc.state == State.DEVICE_LEAVING


# ---------------------------------------------------------------------------
# _device_summary
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Timing config integration
# ---------------------------------------------------------------------------

class TestTimingIntegration:
    @patch("blueward.service.lock_screen", return_value=True)
    @patch("blueward.service.is_locked", return_value=False)
    def test_custom_lock_delay_used(self, mock_is_locked, mock_lock):
        """Service uses timing.lock_delay, not a hardcoded value."""
        cfg = _make_config(lock_delay=0)
        assert cfg.timing.lock_delay == 0  # __post_init__ syncs
        svc = BlueWardService(cfg)
        svc._transition(State.DEVICE_LEAVING)

        svc._check_timeouts()

        assert svc.state == State.DEVICE_AWAY
        mock_lock.assert_called_once()

    @patch("blueward.service.lock_screen")
    @patch("blueward.service.is_locked", return_value=False)
    def test_large_lock_delay_prevents_lock(self, mock_is_locked, mock_lock):
        cfg = _make_config(lock_delay=999)
        assert cfg.timing.lock_delay == 999
        svc = BlueWardService(cfg)
        svc._transition(State.DEVICE_LEAVING)

        svc._check_timeouts()

        assert svc.state == State.DEVICE_LEAVING
        mock_lock.assert_not_called()

    @patch("blueward.service.run_custom_command", return_value=True)
    @patch("blueward.service.lock_screen")
    @patch("blueward.service.is_locked", return_value=False)
    def test_custom_unlock_delay_used(self, mock_is_locked, mock_lock, mock_cmd):
        cfg = _make_config(unlock_delay=0)
        assert cfg.timing.unlock_delay == 0
        svc = BlueWardService(cfg)
        svc._transition(State.DEVICE_APPROACHING)
        device = svc.devices.get("AA:BB:CC:DD:EE:FF")
        device.zone = ProximityZone.NEAR
        device.last_seen = time.monotonic()

        svc._check_timeouts()

        assert svc.state == State.DEVICE_NEAR

    def test_stale_multiplier_respected(self):
        """Stale timeout = lock_delay * stale_multiplier."""
        cfg = _make_config(lock_delay=10)
        cfg.timing.stale_multiplier = 2  # stale after 20s
        svc = BlueWardService(cfg)
        svc._transition(State.SCANNING)
        device = svc.devices.get("AA:BB:CC:DD:EE:FF")
        device.zone = ProximityZone.NEAR

        # 15s old — less than 10*2=20, should NOT be stale
        device.last_seen = time.monotonic() - 15.0
        svc._check_timeouts()
        assert device.zone == ProximityZone.NEAR

        # 25s old — more than 10*2=20, should be marked OOR
        device.last_seen = time.monotonic() - 25.0
        svc._check_timeouts()
        assert device.zone == ProximityZone.OUT_OF_RANGE

    def test_config_timing_sync_in_make_config(self):
        """_make_config properly syncs top-level to timing via __post_init__."""
        cfg = _make_config(lock_delay=5, unlock_delay=2)
        assert cfg.timing.lock_delay == 5
        assert cfg.timing.unlock_delay == 2
        assert cfg.timing.scan_interval == 2.0


# ---------------------------------------------------------------------------
# _device_summary
# ---------------------------------------------------------------------------

class TestDeviceSummary:
    def test_summary_structure(self):
        svc = BlueWardService(_make_config())
        svc._transition(State.SCANNING)
        device = svc.devices.get("AA:BB:CC:DD:EE:FF")
        device.update_rssi(-55)
        device.zone = ProximityZone.NEAR

        summary = svc._device_summary()

        assert summary["state"] == "SCANNING"
        assert "AA:BB:CC:DD:EE:FF" in summary["devices"]
        dev_info = summary["devices"]["AA:BB:CC:DD:EE:FF"]
        assert dev_info["name"] == "Phone"
        assert dev_info["zone"] == "near"
        assert dev_info["rssi"] is not None

"""Tests for blueward.devices — TrackedDevice and DeviceRegistry."""

import time
from unittest.mock import patch

import pytest

from blueward.config import Device, FilterConfig, ZoneThresholds
from blueward.devices import TrackedDevice, DeviceRegistry
from blueward.proximity import ProximityZone


def _make_device(name="Phone", mac="AA:BB:CC:DD:EE:FF", immediate=-45, near=-60, far=-75):
    return Device(
        name=name,
        mac=mac,
        rssi_at_1m=-55,
        zones=ZoneThresholds(immediate=immediate, near=near, far=far),
    )


def _default_filter_config():
    return FilterConfig(method="kalman", process_noise=0.008, measurement_noise=4.0, ema_alpha=0.3)


# ---------------------------------------------------------------------------
# TrackedDevice
# ---------------------------------------------------------------------------

class TestTrackedDevice:
    def test_initial_state(self):
        td = TrackedDevice(config=_make_device())
        assert td.zone == ProximityZone.OUT_OF_RANGE
        assert td.raw_rssi is None
        assert td.smoothed_rssi is None
        assert td.last_seen == 0.0
        assert td.rssi_history == []

    def test_mac_and_name_properties(self):
        td = TrackedDevice(config=_make_device(name="Watch", mac="11:22:33:44:55:66"))
        assert td.mac == "11:22:33:44:55:66"
        assert td.name == "Watch"

    def test_age_before_any_reading(self):
        td = TrackedDevice(config=_make_device())
        assert td.age == float("inf")

    def test_age_after_reading(self):
        td = TrackedDevice(config=_make_device())
        td.update_rssi(-60)
        # Age should be very small since we just updated
        assert td.age < 1.0

    def test_update_rssi_without_filter(self):
        td = TrackedDevice(config=_make_device())
        # No filter initialized, so smoothed = raw
        result = td.update_rssi(-65)
        assert result == -65.0
        assert td.raw_rssi == -65
        assert td.smoothed_rssi == -65.0

    def test_update_rssi_with_kalman_filter(self):
        td = TrackedDevice(config=_make_device())
        td.init_filter(_default_filter_config())
        first = td.update_rssi(-60)
        assert first == -60.0  # First reading passes through
        second = td.update_rssi(-80)
        # Kalman smooths, so second should be between -60 and -80
        assert -80 < second < -60

    def test_update_rssi_with_ema_filter(self):
        td = TrackedDevice(config=_make_device())
        td.init_filter(FilterConfig(method="ema", ema_alpha=0.5))
        td.update_rssi(-60)
        result = td.update_rssi(-80)
        assert result == pytest.approx(-70.0)

    def test_rssi_history_capped_at_20(self):
        td = TrackedDevice(config=_make_device())
        for i in range(30):
            td.update_rssi(-50 - i)
        assert len(td.rssi_history) == 20
        # The first 10 should have been dropped
        assert td.rssi_history[0] == -60.0

    def test_last_seen_updates(self):
        td = TrackedDevice(config=_make_device())
        td.update_rssi(-60)
        t1 = td.last_seen
        assert t1 > 0
        time.sleep(0.01)
        td.update_rssi(-62)
        assert td.last_seen > t1

    def test_reset(self):
        td = TrackedDevice(config=_make_device())
        td.init_filter(_default_filter_config())
        td.update_rssi(-60)
        td.update_rssi(-62)
        td.zone = ProximityZone.NEAR

        td.reset()

        assert td.zone == ProximityZone.OUT_OF_RANGE
        assert td.raw_rssi is None
        assert td.smoothed_rssi is None
        assert td.last_seen == 0.0
        assert td.rssi_history == []


# ---------------------------------------------------------------------------
# DeviceRegistry
# ---------------------------------------------------------------------------

class TestDeviceRegistry:
    def _make_registry(self, devices=None, policy_mode="all"):
        if devices is None:
            devices = [_make_device()]
        return DeviceRegistry(devices, _default_filter_config(), policy_mode=policy_mode)

    def test_trusted_macs(self):
        reg = self._make_registry([
            _make_device(mac="AA:BB:CC:DD:EE:FF"),
            _make_device(mac="11:22:33:44:55:66"),
        ])
        assert reg.trusted_macs == {"AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"}

    def test_get_existing_device(self):
        reg = self._make_registry()
        d = reg.get("AA:BB:CC:DD:EE:FF")
        assert d is not None
        assert d.mac == "AA:BB:CC:DD:EE:FF"

    def test_get_case_insensitive(self):
        reg = self._make_registry()
        assert reg.get("aa:bb:cc:dd:ee:ff") is not None

    def test_get_unknown_returns_none(self):
        reg = self._make_registry()
        assert reg.get("00:00:00:00:00:00") is None

    def test_is_trusted(self):
        reg = self._make_registry()
        assert reg.is_trusted("AA:BB:CC:DD:EE:FF") is True
        assert reg.is_trusted("aa:bb:cc:dd:ee:ff") is True
        assert reg.is_trusted("00:00:00:00:00:00") is False

    def test_all_devices(self):
        reg = self._make_registry([
            _make_device(mac="AA:BB:CC:DD:EE:FF"),
            _make_device(mac="11:22:33:44:55:66"),
        ])
        assert len(reg.all_devices()) == 2


# ---------------------------------------------------------------------------
# should_lock — "all" policy
# ---------------------------------------------------------------------------

class TestShouldLockAllPolicy:
    def _make_registry(self, devices=None):
        if devices is None:
            devices = [
                _make_device(name="Phone", mac="AA:BB:CC:DD:EE:FF"),
                _make_device(name="Watch", mac="11:22:33:44:55:66"),
            ]
        return DeviceRegistry(devices, _default_filter_config(), policy_mode="all")

    def test_no_devices_returns_false(self):
        reg = DeviceRegistry([], _default_filter_config(), policy_mode="all")
        assert reg.should_lock() is False

    def test_all_oor_locks(self):
        reg = self._make_registry()
        # Default zone is OUT_OF_RANGE for all devices
        assert reg.should_lock() is True

    def test_one_near_one_oor_no_lock(self):
        reg = self._make_registry()
        reg.get("AA:BB:CC:DD:EE:FF").zone = ProximityZone.NEAR
        assert reg.should_lock() is False

    def test_all_far_locks(self):
        reg = self._make_registry()
        for d in reg.all_devices():
            d.zone = ProximityZone.FAR
        assert reg.should_lock() is True

    def test_one_immediate_no_lock(self):
        reg = self._make_registry()
        reg.get("AA:BB:CC:DD:EE:FF").zone = ProximityZone.IMMEDIATE
        reg.get("11:22:33:44:55:66").zone = ProximityZone.FAR
        assert reg.should_lock() is False

    def test_mixed_far_and_oor_locks(self):
        reg = self._make_registry()
        reg.get("AA:BB:CC:DD:EE:FF").zone = ProximityZone.FAR
        reg.get("11:22:33:44:55:66").zone = ProximityZone.OUT_OF_RANGE
        assert reg.should_lock() is True


# ---------------------------------------------------------------------------
# should_lock — "any" policy
# ---------------------------------------------------------------------------

class TestShouldLockAnyPolicy:
    def _make_registry(self):
        devices = [
            _make_device(name="Phone", mac="AA:BB:CC:DD:EE:FF"),
            _make_device(name="Watch", mac="11:22:33:44:55:66"),
        ]
        return DeviceRegistry(devices, _default_filter_config(), policy_mode="any")

    def test_one_far_triggers_lock(self):
        reg = self._make_registry()
        reg.get("AA:BB:CC:DD:EE:FF").zone = ProximityZone.NEAR
        reg.get("11:22:33:44:55:66").zone = ProximityZone.FAR
        assert reg.should_lock() is True

    def test_all_near_no_lock(self):
        reg = self._make_registry()
        for d in reg.all_devices():
            d.zone = ProximityZone.NEAR
        assert reg.should_lock() is False

    def test_all_immediate_no_lock(self):
        reg = self._make_registry()
        for d in reg.all_devices():
            d.zone = ProximityZone.IMMEDIATE
        assert reg.should_lock() is False

    def test_one_oor_triggers_lock(self):
        reg = self._make_registry()
        reg.get("AA:BB:CC:DD:EE:FF").zone = ProximityZone.IMMEDIATE
        reg.get("11:22:33:44:55:66").zone = ProximityZone.OUT_OF_RANGE
        assert reg.should_lock() is True


# ---------------------------------------------------------------------------
# any_device_near
# ---------------------------------------------------------------------------

class TestAnyDeviceNear:
    def test_none_near(self):
        reg = DeviceRegistry([_make_device()], _default_filter_config())
        # Default is OOR
        assert reg.any_device_near() is False

    def test_one_near(self):
        reg = DeviceRegistry([_make_device()], _default_filter_config())
        reg.get("AA:BB:CC:DD:EE:FF").zone = ProximityZone.NEAR
        assert reg.any_device_near() is True

    def test_one_immediate(self):
        reg = DeviceRegistry([_make_device()], _default_filter_config())
        reg.get("AA:BB:CC:DD:EE:FF").zone = ProximityZone.IMMEDIATE
        assert reg.any_device_near() is True

    def test_far_not_counted_as_near(self):
        reg = DeviceRegistry([_make_device()], _default_filter_config())
        reg.get("AA:BB:CC:DD:EE:FF").zone = ProximityZone.FAR
        assert reg.any_device_near() is False


# ---------------------------------------------------------------------------
# stale_devices
# ---------------------------------------------------------------------------

class TestStaleDevices:
    def test_no_readings_is_stale(self):
        reg = DeviceRegistry([_make_device()], _default_filter_config())
        # age is inf when never seen
        stale = reg.stale_devices(timeout=10.0)
        assert len(stale) == 1

    def test_recently_seen_not_stale(self):
        reg = DeviceRegistry([_make_device()], _default_filter_config())
        reg.get("AA:BB:CC:DD:EE:FF").update_rssi(-60)
        stale = reg.stale_devices(timeout=10.0)
        assert len(stale) == 0

    def test_old_reading_becomes_stale(self):
        reg = DeviceRegistry([_make_device()], _default_filter_config())
        device = reg.get("AA:BB:CC:DD:EE:FF")
        # Fake an old last_seen
        device.last_seen = time.monotonic() - 30.0
        stale = reg.stale_devices(timeout=10.0)
        assert len(stale) == 1
        assert stale[0].mac == "AA:BB:CC:DD:EE:FF"

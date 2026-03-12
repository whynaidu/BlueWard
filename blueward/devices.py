"""Trusted device tracking with per-device RSSI filters and zone state."""

import time
from dataclasses import dataclass, field

from .config import Device, FilterConfig
from .filter import create_filter, KalmanFilter, EMAFilter
from .proximity import ProximityZone


@dataclass
class TrackedDevice:
    """Runtime state for a trusted Bluetooth device."""

    config: Device
    zone: ProximityZone = ProximityZone.OUT_OF_RANGE
    raw_rssi: int | None = None
    smoothed_rssi: float | None = None
    last_seen: float = 0.0
    rssi_history: list[float] = field(default_factory=list)
    _filter: KalmanFilter | EMAFilter | None = field(default=None, repr=False)

    @property
    def mac(self) -> str:
        return self.config.mac

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def age(self) -> float:
        """Seconds since last RSSI reading."""
        if self.last_seen == 0:
            return float("inf")
        return time.monotonic() - self.last_seen

    def init_filter(self, filter_config: FilterConfig):
        self._filter = create_filter(
            method=filter_config.method,
            process_noise=filter_config.process_noise,
            measurement_noise=filter_config.measurement_noise,
            ema_alpha=filter_config.ema_alpha,
        )

    def update_rssi(self, rssi: int) -> float:
        """Feed a new RSSI reading and return the smoothed value."""
        self.raw_rssi = rssi
        self.last_seen = time.monotonic()

        if self._filter is None:
            self.smoothed_rssi = float(rssi)
        else:
            self.smoothed_rssi = self._filter.update(rssi)

        # Keep last 20 readings for trend analysis
        self.rssi_history.append(float(rssi))
        if len(self.rssi_history) > 20:
            self.rssi_history.pop(0)

        return self.smoothed_rssi

    def reset(self):
        self.zone = ProximityZone.OUT_OF_RANGE
        self.raw_rssi = None
        self.smoothed_rssi = None
        self.last_seen = 0.0
        self.rssi_history.clear()
        if self._filter is not None:
            self._filter.reset()


class DeviceRegistry:
    """Manages all trusted devices and provides multi-device policy evaluation."""

    def __init__(self, devices: list[Device], filter_config: FilterConfig, policy_mode: str = "all"):
        self._devices: dict[str, TrackedDevice] = {}
        self._policy_mode = policy_mode

        for dev in devices:
            tracked = TrackedDevice(config=dev)
            tracked.init_filter(filter_config)
            self._devices[dev.mac.upper()] = tracked

    @property
    def trusted_macs(self) -> set[str]:
        return set(self._devices.keys())

    def get(self, mac: str) -> TrackedDevice | None:
        return self._devices.get(mac.upper())

    def all_devices(self) -> list[TrackedDevice]:
        return list(self._devices.values())

    def is_trusted(self, mac: str) -> bool:
        return mac.upper() in self._devices

    def should_lock(self) -> bool:
        """Evaluate whether to lock based on the multi-device policy.

        "all" mode: lock when ALL trusted devices are FAR or OUT_OF_RANGE.
        "any" mode: lock when ANY trusted device is FAR or OUT_OF_RANGE.
        """
        if not self._devices:
            return False

        away_zones = {ProximityZone.FAR, ProximityZone.OUT_OF_RANGE}

        if self._policy_mode == "any":
            return any(d.zone in away_zones for d in self._devices.values())
        else:  # "all" (default)
            return all(d.zone in away_zones for d in self._devices.values())

    def any_device_near(self) -> bool:
        """True if any trusted device is in NEAR or IMMEDIATE zone."""
        near_zones = {ProximityZone.NEAR, ProximityZone.IMMEDIATE}
        return any(d.zone in near_zones for d in self._devices.values())

    def stale_devices(self, timeout: float) -> list[TrackedDevice]:
        """Return devices that haven't been seen within the timeout period."""
        return [d for d in self._devices.values() if d.age > timeout]

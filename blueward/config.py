from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ImportError:
    import tomli as tomllib


@dataclass
class ZoneThresholds:
    immediate: int = -45
    near: int = -60
    far: int = -75


@dataclass
class Device:
    name: str
    mac: str
    rssi_at_1m: int = -55
    zones: ZoneThresholds = field(default_factory=ZoneThresholds)


@dataclass
class FilterConfig:
    method: str = "kalman"
    process_noise: float = 0.008
    measurement_noise: float = 4.0
    ema_alpha: float = 0.3


@dataclass
class ActionsConfig:
    lock_command: str = ""
    unlock_command: str = ""
    on_lock_extra: str = ""    # e.g. "playerctl pause"
    on_unlock_extra: str = ""  # e.g. "playerctl play"


@dataclass
class Config:
    scan_interval: float = 2.0
    lock_delay: int = 8
    unlock_delay: int = 3
    notifications: bool = True
    tray_icon: bool = True
    log_rssi: bool = False
    rssi_log_path: str = "~/.local/share/blueward/rssi.log"
    policy_mode: str = "all"
    devices: list[Device] = field(default_factory=list)
    filter: FilterConfig = field(default_factory=FilterConfig)
    actions: ActionsConfig = field(default_factory=ActionsConfig)


def load_config(path: Optional[str] = None) -> Config:
    if path is None:
        candidates = [
            Path.home() / ".config" / "blueward" / "config.toml",
            Path(__file__).parent.parent / "config.toml",
        ]
        for candidate in candidates:
            if candidate.exists():
                path = str(candidate)
                break
        else:
            return Config()

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    bw = raw.get("blueward", {})
    flt = raw.get("filter", {})
    act = raw.get("actions", {})

    devices = []
    for d in raw.get("devices", []):
        zones_raw = d.get("zones", {})
        zones = ZoneThresholds(
            immediate=zones_raw.get("immediate", -45),
            near=zones_raw.get("near", -60),
            far=zones_raw.get("far", -75),
        )
        devices.append(Device(
            name=d.get("name", "Unknown"),
            mac=d.get("mac", "").upper(),
            rssi_at_1m=d.get("rssi_at_1m", -55),
            zones=zones,
        ))

    policy = bw.get("policy", {})

    return Config(
        scan_interval=bw.get("scan_interval", 2.0),
        lock_delay=bw.get("lock_delay", 8),
        unlock_delay=bw.get("unlock_delay", 3),
        notifications=bw.get("notifications", True),
        tray_icon=bw.get("tray_icon", True),
        log_rssi=bw.get("log_rssi", False),
        rssi_log_path=bw.get("rssi_log_path", "~/.local/share/blueward/rssi.log"),
        policy_mode=policy.get("mode", "all"),
        devices=devices,
        filter=FilterConfig(
            method=flt.get("method", "kalman"),
            process_noise=flt.get("process_noise", 0.008),
            measurement_noise=flt.get("measurement_noise", 4.0),
            ema_alpha=flt.get("ema_alpha", 0.3),
        ),
        actions=ActionsConfig(
            lock_command=act.get("lock_command", ""),
            unlock_command=act.get("unlock_command", ""),
            on_lock_extra=act.get("on_lock_extra", ""),
            on_unlock_extra=act.get("on_unlock_extra", ""),
        ),
    )

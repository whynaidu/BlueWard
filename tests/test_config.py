"""Tests for blueward.config — load_config and dataclass defaults."""

import os
import tempfile

import pytest

from blueward.config import (
    ActionsConfig,
    Config,
    Device,
    FilterConfig,
    ZoneThresholds,
    load_config,
)


SAMPLE_TOML = """\
[blueward]
scan_interval = 3.0
lock_delay = 10
unlock_delay = 5
notifications = false
tray_icon = false
log_rssi = true
rssi_log_path = "/tmp/blueward_test.log"

[blueward.policy]
mode = "any"

[[devices]]
name = "Test Phone"
mac = "AA:BB:CC:DD:EE:FF"
rssi_at_1m = -50

[devices.zones]
immediate = -40
near = -55
far = -70

[[devices]]
name = "Test Watch"
mac = "11:22:33:44:55:66"

[filter]
method = "ema"
ema_alpha = 0.5
process_noise = 0.01
measurement_noise = 3.0
"""


MINIMAL_TOML = """\
[blueward]
lock_delay = 15
"""


@pytest.fixture
def sample_config_path(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(SAMPLE_TOML)
    return str(p)


@pytest.fixture
def minimal_config_path(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(MINIMAL_TOML)
    return str(p)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_config_defaults(self):
        c = Config()
        assert c.scan_interval == 2.0
        assert c.lock_delay == 8
        assert c.unlock_delay == 3
        assert c.notifications is True
        assert c.tray_icon is True
        assert c.log_rssi is False
        assert c.policy_mode == "all"
        assert c.devices == []
        assert isinstance(c.filter, FilterConfig)
        assert isinstance(c.actions, ActionsConfig)

    def test_actions_config_defaults(self):
        a = ActionsConfig()
        assert a.lock_command == ""
        assert a.unlock_command == ""
        assert a.on_lock_extra == ""
        assert a.on_unlock_extra == ""

    def test_zone_thresholds_defaults(self):
        z = ZoneThresholds()
        assert z.immediate == -45
        assert z.near == -60
        assert z.far == -75

    def test_filter_config_defaults(self):
        f = FilterConfig()
        assert f.method == "kalman"
        assert f.process_noise == 0.008
        assert f.measurement_noise == 4.0
        assert f.ema_alpha == 0.3


# ---------------------------------------------------------------------------
# load_config with full TOML
# ---------------------------------------------------------------------------

class TestLoadConfigFull:
    def test_blueward_section(self, sample_config_path):
        cfg = load_config(sample_config_path)
        assert cfg.scan_interval == 3.0
        assert cfg.lock_delay == 10
        assert cfg.unlock_delay == 5
        assert cfg.notifications is False
        assert cfg.tray_icon is False
        assert cfg.log_rssi is True
        assert cfg.rssi_log_path == "/tmp/blueward_test.log"

    def test_policy_mode(self, sample_config_path):
        cfg = load_config(sample_config_path)
        assert cfg.policy_mode == "any"

    def test_devices_count(self, sample_config_path):
        cfg = load_config(sample_config_path)
        assert len(cfg.devices) == 2

    def test_first_device(self, sample_config_path):
        cfg = load_config(sample_config_path)
        dev = cfg.devices[0]
        assert dev.name == "Test Phone"
        assert dev.mac == "AA:BB:CC:DD:EE:FF"
        assert dev.rssi_at_1m == -50
        assert dev.zones.immediate == -40
        assert dev.zones.near == -55
        assert dev.zones.far == -70

    def test_second_device_gets_zone_defaults(self, sample_config_path):
        cfg = load_config(sample_config_path)
        dev = cfg.devices[1]
        assert dev.name == "Test Watch"
        assert dev.mac == "11:22:33:44:55:66"
        # No zones specified — should get defaults
        assert dev.zones.immediate == -45
        assert dev.zones.near == -60
        assert dev.zones.far == -75

    def test_mac_uppercased(self, sample_config_path):
        cfg = load_config(sample_config_path)
        for dev in cfg.devices:
            assert dev.mac == dev.mac.upper()

    def test_filter_section(self, sample_config_path):
        cfg = load_config(sample_config_path)
        assert cfg.filter.method == "ema"
        assert cfg.filter.ema_alpha == 0.5
        assert cfg.filter.process_noise == 0.01
        assert cfg.filter.measurement_noise == 3.0


# ---------------------------------------------------------------------------
# load_config with minimal TOML
# ---------------------------------------------------------------------------

class TestLoadConfigMinimal:
    def test_specified_values_override(self, minimal_config_path):
        cfg = load_config(minimal_config_path)
        assert cfg.lock_delay == 15

    def test_unspecified_values_use_defaults(self, minimal_config_path):
        cfg = load_config(minimal_config_path)
        assert cfg.scan_interval == 2.0
        assert cfg.notifications is True
        assert cfg.devices == []
        assert cfg.filter.method == "kalman"


# ---------------------------------------------------------------------------
# load_config with no path (fallback)
# ---------------------------------------------------------------------------

class TestLoadConfigFallback:
    def test_no_path_no_file_returns_defaults(self, tmp_path, monkeypatch):
        """When no config file exists at any candidate path, return defaults."""
        # Ensure neither candidate path exists by pointing home to an empty dir
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        # Also patch __file__ parent.parent to tmp_path so the second candidate fails
        monkeypatch.setattr("blueward.config.Path.__fspath__", lambda self: str(tmp_path / "nonexistent"))
        cfg = load_config(None)
        # Should get default Config (no crash)
        assert isinstance(cfg, Config)

    def test_nonexistent_path_raises(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.toml")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_toml(self, tmp_path):
        p = tmp_path / "empty.toml"
        p.write_text("")
        cfg = load_config(str(p))
        # Should return defaults
        assert cfg.scan_interval == 2.0
        assert cfg.devices == []

    def test_lowercase_mac_gets_uppercased(self, tmp_path):
        toml_content = """\
[[devices]]
name = "Lower"
mac = "aa:bb:cc:dd:ee:ff"
"""
        p = tmp_path / "cfg.toml"
        p.write_text(toml_content)
        cfg = load_config(str(p))
        assert cfg.devices[0].mac == "AA:BB:CC:DD:EE:FF"

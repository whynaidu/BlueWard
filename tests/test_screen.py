"""Tests for blueward.screen — screen lock/unlock with mocked dbus/subprocess."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from blueward import screen


# ---------------------------------------------------------------------------
# _try_dbus_lock
# ---------------------------------------------------------------------------

class TestTryDbusLock:
    @patch("blueward.screen.dbus", create=True)
    def test_success(self, mock_dbus_module):
        """Successful GNOME D-Bus lock returns True."""
        # We need to mock the import inside the function.
        # _try_dbus_lock does `import dbus` at module level — screen.py imports it.
        # But screen.py uses a try/except, so we mock at module level.
        mock_bus = MagicMock()
        mock_dbus_module.SessionBus.return_value = mock_bus
        mock_iface = MagicMock()
        mock_dbus_module.Interface.return_value = mock_iface

        # Patch the import inside the function
        with patch.dict("sys.modules", {"dbus": mock_dbus_module}):
            result = screen._try_dbus_lock()

        assert result is True
        mock_iface.Lock.assert_called_once()

    def test_no_dbus_returns_false(self):
        """When dbus raises an exception, returns False."""
        with patch.dict("sys.modules", {"dbus": MagicMock(side_effect=Exception("no session bus"))}):
            # The function catches all exceptions
            result = screen._try_dbus_lock()
        # Even if dbus module mock raises, the function should handle it
        # In practice, the real function catches Exception, so let's test
        # with a mock that makes SessionBus() raise
        mock_dbus = MagicMock()
        mock_dbus.SessionBus.side_effect = Exception("no bus")
        with patch.dict("sys.modules", {"dbus": mock_dbus}):
            result = screen._try_dbus_lock()
        assert result is False


# ---------------------------------------------------------------------------
# _try_loginctl_lock
# ---------------------------------------------------------------------------

class TestTryLoginctlLock:
    @patch("blueward.screen.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert screen._try_loginctl_lock() is True
        mock_run.assert_called_once_with(
            ["loginctl", "lock-session"],
            capture_output=True, timeout=5,
        )

    @patch("blueward.screen.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert screen._try_loginctl_lock() is False

    @patch("blueward.screen.subprocess.run", side_effect=FileNotFoundError)
    def test_command_not_found(self, mock_run):
        assert screen._try_loginctl_lock() is False

    @patch("blueward.screen.subprocess.run", side_effect=subprocess.TimeoutExpired("loginctl", 5))
    def test_timeout(self, mock_run):
        assert screen._try_loginctl_lock() is False


# ---------------------------------------------------------------------------
# lock_screen (orchestrator)
# ---------------------------------------------------------------------------

class TestLockScreen:
    @patch("blueward.screen._try_loginctl_lock")
    @patch("blueward.screen._try_dbus_lock")
    def test_dbus_success_skips_loginctl(self, mock_dbus, mock_loginctl):
        mock_dbus.return_value = True
        assert screen.lock_screen() is True
        mock_loginctl.assert_not_called()

    @patch("blueward.screen._try_loginctl_lock")
    @patch("blueward.screen._try_dbus_lock")
    def test_dbus_fail_falls_back_to_loginctl(self, mock_dbus, mock_loginctl):
        mock_dbus.return_value = False
        mock_loginctl.return_value = True
        assert screen.lock_screen() is True

    @patch("blueward.screen._try_loginctl_lock")
    @patch("blueward.screen._try_dbus_lock")
    def test_both_fail(self, mock_dbus, mock_loginctl):
        mock_dbus.return_value = False
        mock_loginctl.return_value = False
        assert screen.lock_screen() is False


# ---------------------------------------------------------------------------
# is_locked
# ---------------------------------------------------------------------------

class TestIsLocked:
    def test_locked(self):
        mock_dbus = MagicMock()
        mock_bus = MagicMock()
        mock_dbus.SessionBus.return_value = mock_bus
        mock_iface = MagicMock()
        mock_iface.GetActive.return_value = True
        mock_dbus.Interface.return_value = mock_iface

        with patch.dict("sys.modules", {"dbus": mock_dbus}):
            result = screen.is_locked()
        assert result is True

    def test_not_locked(self):
        mock_dbus = MagicMock()
        mock_bus = MagicMock()
        mock_dbus.SessionBus.return_value = mock_bus
        mock_iface = MagicMock()
        mock_iface.GetActive.return_value = False
        mock_dbus.Interface.return_value = mock_iface

        with patch.dict("sys.modules", {"dbus": mock_dbus}):
            result = screen.is_locked()
        assert result is False

    def test_dbus_error_returns_false(self):
        mock_dbus = MagicMock()
        mock_dbus.SessionBus.side_effect = Exception("no bus")
        with patch.dict("sys.modules", {"dbus": mock_dbus}):
            assert screen.is_locked() is False


# ---------------------------------------------------------------------------
# unlock_screen
# ---------------------------------------------------------------------------

class TestUnlockScreen:
    @patch("blueward.screen.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert screen.unlock_screen() is True

    @patch("blueward.screen.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert screen.unlock_screen() is False

    @patch("blueward.screen.subprocess.run", side_effect=Exception("error"))
    def test_exception_returns_false(self, mock_run):
        assert screen.unlock_screen() is False


# ---------------------------------------------------------------------------
# run_custom_command
# ---------------------------------------------------------------------------

class TestRunCustomCommand:
    @patch("blueward.screen.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert screen.run_custom_command("playerctl pause") is True
        mock_run.assert_called_once_with(
            "playerctl pause", shell=True, capture_output=True, timeout=10,
        )

    @patch("blueward.screen.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert screen.run_custom_command("false") is False

    def test_empty_command_returns_true(self):
        assert screen.run_custom_command("") is True

    @patch("blueward.screen.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10))
    def test_timeout(self, mock_run):
        assert screen.run_custom_command("sleep 999") is False

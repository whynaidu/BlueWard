# BlueWard — Claude Code Project Instructions

## Project Overview

BlueWard is a Bluetooth proximity-based screen lock for Linux. It monitors trusted Bluetooth devices and locks/unlocks the screen based on proximity.

## Architecture

- `blueward/service.py` — Core state machine (SCANNING, DEVICE_NEAR, DEVICE_LEAVING, DEVICE_AWAY, etc.)
- `blueward/scanner.py` — BLE scanning via BlueZ D-Bus (ActiveScanner + PassiveScanner)
- `blueward/proximity.py` — RSSI to proximity zone classification with hysteresis
- `blueward/filter.py` — Kalman and EMA filters for RSSI smoothing
- `blueward/devices.py` — Device registry and tracking
- `blueward/screen.py` — Screen lock/unlock via D-Bus and loginctl
- `blueward/config.py` — TOML config parsing with TimingConfig
- `blueward/notifier.py` — Desktop notifications via notify-send
- `blueward/tray.py` — GTK AppIndicator system tray
- `blueward/fallback.py` — Classic BT l2ping fallback
- `blueward/__main__.py` — CLI entry point (scan, status, setup subcommands)

## Development Rules

- Python 3.11+ required
- Run tests: `python3 -m pytest tests/ -v`
- All tests must pass before committing
- System deps (dbus, gi) are mocked in tests — see test_service.py for pattern
- Config changes must maintain backward compatibility with existing config.toml files
- New features need unit tests covering acceptance criteria
- Functional test: `timeout 15 blueward --verbose --no-tray --no-notify`

## Config Structure

Config is TOML at `~/.config/blueward/config.toml`:
- `[blueward]` — main settings (lock_delay, unlock_delay, notifications, etc.)
- `[[devices]]` — trusted Bluetooth devices with MAC, zones
- `[timing]` — all timing values (check_interval, l2ping_interval, idle multipliers)
- `[filter]` — Kalman/EMA filter parameters
- `[actions]` — custom lock/unlock commands

## Ralph Wiggum Development

When running in a Ralph loop:
1. Read `prd.json` for task requirements
2. Check `PROGRESS.md` for current state
3. Work through tasks in phase order
4. Update PROGRESS.md after each task
5. Commit after each completed task

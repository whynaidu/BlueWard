# BlueWard

Bluetooth proximity-based screen lock for Linux. Automatically locks your screen when your paired Bluetooth device (phone, watch, etc.) moves out of range, and unlocks when it returns.

## Features

- **Proximity-based lock/unlock** using Bluetooth RSSI signal strength
- **4 proximity zones**: Immediate (<0.5m), Near (0.5-2m), Far (2-5m), Out of Range (>5m)
- **Adaptive power management** — polls aggressively during state changes, backs off when stable to save battery
- **Dual scanning**: passive AdvertisementMonitor (power-efficient) with active discovery fallback
- **Classic BT fallback** via l2ping for devices that don't advertise over BLE
- **Kalman filter** or EMA smoothing to prevent false triggers from RSSI noise
- **Multi-device support** with "any" or "all" lock policy
- **Custom actions** on lock/unlock (e.g., pause media, mute mic)
- **System tray icon** with live status
- **Desktop notifications**
- **Systemd user service** for auto-start on login
- **Interactive setup wizard** (`blueward setup`)

## Requirements

- **Linux** (Ubuntu, Fedora, Arch, etc.)
- **Python 3.11+**
- **Bluetooth adapter** (built-in or USB dongle)
- **BlueZ** (installed automatically)

## Installation

```bash
git clone https://github.com/whynaidu/BlueWard.git
cd BlueWard
make install
```

The installer will:
1. Install system dependencies (`bluez`, `python3-dbus`, `python3-gi`, `libnotify-bin`)
2. Install BlueWard via pip
3. Set up default config at `~/.config/blueward/config.toml`
4. Install the systemd user service
5. Check Bluetooth readiness and warn if anything is missing

## Quick Start

```bash
# 1. Run the interactive setup wizard
blueward setup

# 2. Test it
blueward --verbose --no-tray

# 3. Enable auto-start on login
systemctl --user enable --now blueward

# 4. Check logs
journalctl --user -u blueward -f
```

### Manual Setup

If you prefer to configure manually:

```bash
# Find your device's MAC address
blueward scan

# Edit the config
nano ~/.config/blueward/config.toml

# Add your device:
# [[devices]]
# name = "My Phone"
# mac = "AA:BB:CC:DD:EE:FF"
```

## Usage

```
blueward                     Start proximity monitoring
blueward scan                Scan for nearby Bluetooth devices
blueward scan -d 20          Scan for 20 seconds
blueward status              Show adapter info and current config
blueward setup               Interactive setup wizard
```

### Options

```
--config, -c PATH    Use a custom config file
--no-tray            Run without system tray icon
--no-notify          Disable desktop notifications
--log-rssi           Log RSSI readings to file (for calibration)
--verbose, -v        Enable debug logging
```

## Configuration

Config file: `~/.config/blueward/config.toml`

```toml
[blueward]
scan_interval = 2.0       # BLE scan cycle (seconds)
lock_delay = 4            # Seconds out-of-range before locking
unlock_delay = 1          # Seconds in-range before unlocking
notifications = true
tray_icon = true

[[devices]]
name = "My Phone"
mac = "AA:BB:CC:DD:EE:FF"
rssi_at_1m = -55          # Calibrate: hold phone 1m away, note average RSSI

[devices.zones]
immediate = -45           # Very close (< 0.5m)
near = -60                # Normal range (0.5-2m)
far = -75                 # Getting far (2-5m)
# Below 'far' = OUT_OF_RANGE -> triggers lock

# Multi-device policy: "any" or "all"
[blueward.policy]
mode = "all"

[actions]
lock_command = ""          # Override default lock (leave empty for D-Bus)
unlock_command = ""        # Override default unlock
on_lock_extra = ""         # e.g. "playerctl pause"
on_unlock_extra = ""       # e.g. "playerctl play"

[timing]
check_interval = 2         # Poll interval during active states (seconds)
l2ping_interval = 5        # Classic BT ping interval
l2ping_timeout = 2         # l2ping response timeout
rssi_high_timeout = 3      # BlueZ AdvMonitor near threshold timeout
rssi_low_timeout = 10      # BlueZ AdvMonitor far threshold timeout
stale_multiplier = 3       # Device stale after lock_delay x this
idle_poll_multiplier = 5   # In stable states, poll 5x slower
idle_l2ping_multiplier = 6 # In stable states, l2ping 6x less often

[filter]
method = "kalman"          # "kalman" or "ema"
process_noise = 0.008
measurement_noise = 4.0
ema_alpha = 0.3
```

## How It Works

```
Phone nearby          Phone walks away       Phone returns
     |                      |                      |
 DEVICE_NEAR ──> DEVICE_LEAVING ──> DEVICE_AWAY ──> DEVICE_APPROACHING ──> DEVICE_NEAR
  (unlocked)      (grace period)     (locked)       (confirming)          (unlocked)
```

1. BlueWard monitors your phone's Bluetooth signal strength (RSSI)
2. RSSI readings are smoothed through a Kalman filter to reduce noise
3. The smoothed RSSI is classified into proximity zones (Immediate/Near/Far/Out of Range)
4. When all trusted devices leave range, a grace period starts (`lock_delay`)
5. If the device doesn't return within the grace period, the screen locks
6. When a device returns, it must be confirmed nearby for `unlock_delay` seconds before unlocking
7. **Adaptive power management**: in stable states (device nearby or locked), polling slows down 5x to save battery on both laptop and phone

## Calibration

For best results, calibrate the RSSI threshold for your specific device:

```bash
# Log RSSI readings while holding your phone at different distances
blueward --log-rssi --verbose --no-tray

# RSSI data is saved to ~/.local/share/blueward/rssi.log
# Use this to tune your zone thresholds in config.toml
```

## Uninstalling

```bash
make uninstall
```

This stops the service, removes the systemd unit, and uninstalls the package. It will ask before deleting your config.

## Troubleshooting

**"Failed to connect to BlueZ"**
```bash
sudo systemctl start bluetooth    # Start BlueZ service
sudo systemctl enable bluetooth   # Enable on boot
rfkill list bluetooth             # Check if blocked
rfkill unblock bluetooth          # Unblock if soft-blocked
```

**No Bluetooth adapter detected**
- Check if your laptop has Bluetooth hardware: `lsusb | grep -i bluetooth`
- You may need a USB Bluetooth dongle
- Install firmware: `sudo apt install linux-firmware bluez-firmware`

**Device not found during scan**
- Make sure your phone's Bluetooth is on and discoverable
- Try pairing via `bluetoothctl` first
- Some phones only advertise over Classic BT, not BLE — BlueWard handles this via l2ping fallback

**Screen doesn't unlock**
- BlueWard uses D-Bus to unlock (GNOME) or `loginctl` as fallback
- Some desktop environments may block programmatic unlock
- Set a custom `unlock_command` in config if needed

## License

MIT

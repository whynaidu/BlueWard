# AuraLock: Bluetooth Proximity-Based Screen Locker for Linux
## Comprehensive Technical Research Summary

---

## 1. BLE Scanning Approaches on Linux

### Option A: BlueZ D-Bus API (Recommended for AuraLock)

BlueZ exposes its full functionality via D-Bus under the `org.bluez` bus name. There are two scanning paradigms:

#### 1a. Active Discovery (org.bluez.Adapter1)

Call `StartDiscovery()` on the adapter, with optional `SetDiscoveryFilter()`:

```python
import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

DBusGMainLoop(set_as_default=True)

bus = dbus.SystemBus()

adapter_path = "/org/bluez/hci0"
adapter = dbus.Interface(
    bus.get_object("org.bluez", adapter_path),
    "org.bluez.Adapter1"
)

# Set discovery filter for BLE only, with RSSI threshold
adapter.SetDiscoveryFilter({
    "Transport": dbus.String("le"),
    "RSSI": dbus.Int16(-90),           # Only report devices > -90 dBm
    "DuplicateData": dbus.Boolean(True), # Get repeated RSSI updates
})

adapter.StartDiscovery()
```

When `DuplicateData` is True, BlueZ emits `PropertiesChanged` signals on Device objects every time a new advertisement is received, including updated RSSI values.

**Monitoring RSSI changes via PropertiesChanged:**

```python
def properties_changed(interface, changed, invalidated, path):
    if interface != "org.bluez.Device1":
        return
    if "RSSI" in changed:
        rssi = int(changed["RSSI"])
        address = path.split("/")[-1].replace("_", ":")
        print(f"Device {address}: RSSI = {rssi} dBm")

bus.add_signal_receiver(
    properties_changed,
    signal_name="PropertiesChanged",
    dbus_interface="org.freedesktop.DBus.Properties",
    bus_name="org.bluez",
    path_keyword="path",
)

loop = GLib.MainLoop()
loop.run()
```

**Key BlueZ Device Properties (org.bluez.Device1):**
- `Address` [readonly]: Bluetooth MAC address
- `RSSI` [readonly, optional]: Signal strength in dBm (updated during discovery/advertising)
- `TxPower` [readonly, optional]: Advertised transmit power level
- `Connected` [readonly]: Whether device is actively connected
- `Paired` [readonly]: Whether device is paired
- `Trusted` [readwrite]: Trust flag
- `ManufacturerData` [readonly, optional]: Manufacturer-specific advertisement data

**SetDiscoveryFilter parameters:**
- `Transport`: "auto" | "bredr" | "le" -- set to "le" for BLE-only scanning
- `RSSI` (int16): Minimum RSSI threshold; PropertiesChanged signals emitted for already-existing Device objects when RSSI updates
- `Pathloss` (uint16): Path loss threshold (alternative to RSSI filter)
- `DuplicateData` (bool): When true, generates updates for ManufacturerData/ServiceData on every advertisement
- `Pattern` (string): Filter by address or name prefix

#### 1b. Advertisement Monitor API (Best for Passive Background Monitoring)

BlueZ provides `org.bluez.AdvertisementMonitor1` and `org.bluez.AdvertisementMonitorManager1` for power-efficient passive monitoring. This is the **ideal approach for AuraLock** as it:

- Works without active discovery sessions
- Provides built-in RSSI threshold logic with hysteresis
- Has configurable timeouts for in-range/out-of-range transitions
- Is designed for exactly this use case (proximity detection)

**Key AdvertisementMonitor properties:**
- `Type`: "or_patterns" (currently the only supported type)
- `RSSIHighThreshold` (-127 to 20 dBm): Signal strength to consider device "in range"
- `RSSILowThreshold` (-127 to 20 dBm): Signal strength to consider device "out of range"
- `RSSIHighTimeout` (1-300 seconds): How long strong signal must persist to trigger "in range"
- `RSSILowTimeout` (1-300 seconds): How long weak signal must persist to trigger "out of range"
- `RSSISamplingPeriod`: Controls packet propagation (0 = all packets, 255 = first only, 1-254 = 100ms groups)
- `Patterns`: Array of (start_position, ad_data_type, content) for filtering

**Callbacks:**
- `Activate()`: Monitor successfully registered
- `DeviceFound(object device)`: Target device detected within RSSI thresholds
- `DeviceLost(object device)`: Target device lost (below RSSI threshold for timeout duration)
- `Release()`: Monitor deactivated

**Registration flow:**
1. Create a D-Bus object implementing `org.bluez.AdvertisementMonitor1`
2. Register hierarchy root with `AdvertisementMonitorManager1.RegisterMonitor(path)`
3. Receive `Activate()` callback on success
4. Receive `DeviceFound()`/`DeviceLost()` callbacks as devices enter/leave range

```python
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

DBusGMainLoop(set_as_default=True)

MONITOR_PATH = "/org/auralock/monitor"
APP_PATH = "/org/auralock"

class AdvMonitor(dbus.service.Object):
    # org.bluez.AdvertisementMonitor1 interface
    IFACE = "org.bluez.AdvertisementMonitor1"

    def __init__(self, bus, path):
        super().__init__(bus, path)

    @dbus.service.method(IFACE, in_signature="", out_signature="")
    def Release(self):
        print("Monitor released")

    @dbus.service.method(IFACE, in_signature="", out_signature="")
    def Activate(self):
        print("Monitor activated")

    @dbus.service.method(IFACE, in_signature="o", out_signature="")
    def DeviceFound(self, device):
        print(f"Device FOUND (in range): {device}")
        # Trigger unlock / notification

    @dbus.service.method(IFACE, in_signature="o", out_signature="")
    def DeviceLost(self, device):
        print(f"Device LOST (out of range): {device}")
        # Trigger screen lock

    @dbus.service.method(
        "org.freedesktop.DBus.Properties",
        in_signature="ss", out_signature="v"
    )
    def Get(self, interface, prop):
        return self.GetAll(interface)[prop]

    @dbus.service.method(
        "org.freedesktop.DBus.Properties",
        in_signature="s", out_signature="a{sv}"
    )
    def GetAll(self, interface):
        if interface != self.IFACE:
            return {}
        return {
            "Type": dbus.String("or_patterns"),
            "RSSIHighThreshold": dbus.Int16(-50),   # "near" threshold
            "RSSILowThreshold": dbus.Int16(-70),     # "far" threshold
            "RSSIHighTimeout": dbus.UInt16(3),       # 3 sec to confirm "near"
            "RSSILowTimeout": dbus.UInt16(10),       # 10 sec to confirm "far"
            "RSSISamplingPeriod": dbus.UInt16(0),    # report all packets
            "Patterns": dbus.Array([], signature="(yyay)"),
        }


class AdvMonitorApp(dbus.service.Object):
    def __init__(self, bus, path, monitor):
        super().__init__(bus, path)
        self.monitor = monitor

    # ObjectManager interface required by BlueZ
    @dbus.service.method(
        "org.freedesktop.DBus.ObjectManager",
        in_signature="", out_signature="a{oa{sa{sv}}}"
    )
    def GetManagedObjects(self):
        return {
            MONITOR_PATH: {
                AdvMonitor.IFACE: self.monitor.GetAll(AdvMonitor.IFACE)
            }
        }


bus = dbus.SystemBus()
monitor = AdvMonitor(bus, MONITOR_PATH)
app = AdvMonitorApp(bus, APP_PATH, monitor)

# Register with BlueZ
mgr = dbus.Interface(
    bus.get_object("org.bluez", "/org/bluez/hci0"),
    "org.bluez.AdvertisementMonitorManager1"
)
mgr.RegisterMonitor(dbus.ObjectPath(APP_PATH))

loop = GLib.MainLoop()
loop.run()
```

### Option B: bleak (Python BLE Library)

bleak is the most popular Python BLE library (v2.1.1, MIT license, actively maintained). It wraps BlueZ D-Bus on Linux.

```python
import asyncio
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

TARGET_ADDRESS = "AA:BB:CC:DD:EE:FF"

def detection_callback(device: BLEDevice, adv_data: AdvertisementData):
    if device.address.upper() == TARGET_ADDRESS:
        print(f"RSSI: {adv_data.rssi} dBm, TxPower: {adv_data.tx_power}")

async def main():
    scanner = BleakScanner(
        detection_callback=detection_callback,
        scanning_mode="active",  # or "passive" on Linux
        bluez={
            "or_patterns": [],  # Use advertisement monitor on BlueZ
        }
    )
    async with scanner:
        await asyncio.sleep(3600)  # Run for 1 hour

asyncio.run(main())
```

**BleakScanner key features:**
- `detection_callback`: Invoked per advertisement received, with `BLEDevice` and `AdvertisementData`
- `scanning_mode`: "active" (sends scan requests) or "passive" (listen-only, Linux only)
- `AdvertisementData` exposes: `rssi`, `tx_power`, `local_name`, `manufacturer_data`, `service_data`, `service_uuids`
- Async iterator mode: `async for device, adv_data in scanner.advertisement_data()`
- On Linux, bleak uses BlueZ D-Bus internally, so it inherits BlueZ's capabilities

**bleak pros:** Clean async API, cross-platform, well-maintained, easy to use
**bleak cons:** Adds abstraction layer over BlueZ (less control over AdvertisementMonitor), primarily designed for GATT client operations

### Option C: bluetoothctl (CLI)

Useful for prototyping and debugging, not for production:

```bash
# Scan for BLE devices
bluetoothctl menu scan
bluetoothctl transport le
bluetoothctl back
bluetoothctl scan on

# Monitor with btmon for RSSI
sudo btmon  # Shows all HCI events including RSSI
```

### Comparison Matrix

| Feature                    | BlueZ D-Bus (direct) | bleak           | bluetoothctl |
|----------------------------|-----------------------|-----------------|--------------|
| RSSI reliability           | Excellent             | Good            | Poor (no RSSI shown) |
| Continuous monitoring      | Yes (PropertiesChanged / AdvMonitor) | Yes (callback) | Manual only |
| Passive scanning           | Yes (AdvMonitor)      | Yes (Linux)     | Yes |
| Background monitoring      | Yes (AdvMonitor)      | Limited         | No |
| Power efficiency           | Best (kernel offload) | Good            | N/A |
| Python integration         | Via dbus-python/dasbus | Native          | Subprocess |
| Complexity                 | High                  | Low             | Lowest |

### BLE vs Classic Bluetooth for Proximity

**BLE is strongly recommended for AuraLock.** Here's why:

| Aspect | BLE | Classic Bluetooth |
|--------|-----|-------------------|
| Advertisements | Broadcasts regularly without connection | Requires inquiry scan |
| RSSI availability | Every advertisement packet | Only during inquiry or with active connection |
| Power consumption | Very low | Higher |
| Range | ~100m (tunable Tx power) | ~10-100m |
| Passive monitoring | Yes | No (must pair/connect) |
| Phone compatibility | All modern phones broadcast BLE adverts | Requires explicit discoverability mode |
| Update frequency | Every 20ms-10s (configurable on peripheral) | Inquiry every ~10s |

**Critical insight:** Most phones continuously broadcast BLE advertisements (for Find My, COVID exposure notifications, etc.) even when not explicitly in discoverable mode. Classic Bluetooth requires the device to be in "discoverable" mode or maintain an active connection, which is impractical for proximity detection.

**However:** If the target device is already *paired and connected* (e.g., for audio), Classic Bluetooth RSSI via `hcitool rssi <bdaddr>` (deprecated but functional) or the BlueZ D-Bus Device1.RSSI property can also work. A hybrid approach -- preferring BLE advertisements but falling back to Classic connection-based RSSI -- provides the most robust solution.

---

## 2. RSSI-to-Distance Accuracy

### The Log-Distance Path Loss Model

The standard model for estimating distance from RSSI:

```
RSSI = TxPower - 10 * n * log10(d / d0) + X_sigma

Rearranged for distance:
d = d0 * 10^((TxPower - RSSI) / (10 * n))
```

Where:
- `RSSI`: Measured received signal strength (dBm)
- `TxPower`: Transmitted power at reference distance d0 (dBm), often advertised by BLE devices
- `d`: Estimated distance (meters)
- `d0`: Reference distance, typically 1 meter
- `n`: Path loss exponent (environment-dependent)
- `X_sigma`: Gaussian random variable for shadow fading (zero-mean, sigma ~4-8 dB)

### Path Loss Exponent Values

| Environment | n (path loss exponent) |
|-------------|----------------------|
| Free space | 2.0 |
| Open office/hallway | 1.6 - 2.0 |
| Suburban residential | 2.0 - 3.0 |
| Indoor (line of sight) | 1.6 - 1.8 |
| Indoor (obstructed) | 2.7 - 4.3 |
| Dense indoor (walls) | 4.0 - 6.0 |
| Industrial | 2.0 - 3.0 |

### Calibration: Measuring TxPower

Many BLE devices advertise their Tx Power Level in the advertisement data (AD Type 0x0A). If not available, calibrate manually:

```python
# Calibration procedure:
# 1. Place device at exactly 1 meter from the adapter
# 2. Collect 100+ RSSI samples
# 3. Take the median as your TxPower reference

import statistics

calibration_samples = []  # Collect RSSI readings at 1m

# After collection:
tx_power_1m = statistics.median(calibration_samples)
# Typical values: -50 to -70 dBm at 1m depending on device
```

### Signal Smoothing: Exponential Moving Average (EMA)

Raw RSSI is noisy (variance of 5-15 dBm). EMA is the simplest effective filter:

```python
class RSSISmoother:
    """Exponential Moving Average filter for RSSI values."""

    def __init__(self, alpha: float = 0.3):
        """
        Args:
            alpha: Smoothing factor (0-1).
                   Lower = smoother but slower response.
                   Higher = more responsive but noisier.
                   0.2-0.4 is typically good for proximity detection.
        """
        self.alpha = alpha
        self.smoothed: float | None = None

    def update(self, rssi: float) -> float:
        if self.smoothed is None:
            self.smoothed = rssi
        else:
            self.smoothed = self.alpha * rssi + (1 - self.alpha) * self.smoothed
        return self.smoothed

    def reset(self):
        self.smoothed = None
```

### Signal Smoothing: Kalman Filter (Better)

```python
class RSSIKalmanFilter:
    """
    Simple 1D Kalman filter for RSSI smoothing.

    Better than EMA because it adapts its gain based on
    measurement vs. process noise ratio.
    """

    def __init__(
        self,
        process_noise: float = 0.008,   # Q: how much we expect RSSI to change
        measurement_noise: float = 4.0,  # R: typical RSSI measurement variance
        initial_estimate: float = -70.0,
        initial_error: float = 10.0,
    ):
        self.Q = process_noise
        self.R = measurement_noise
        self.x = initial_estimate   # State estimate
        self.P = initial_error      # Estimate error covariance

    def update(self, measurement: float) -> float:
        # Prediction step (static model: RSSI doesn't change on its own)
        self.P += self.Q

        # Update step
        K = self.P / (self.P + self.R)  # Kalman gain
        self.x += K * (measurement - self.x)
        self.P *= (1 - K)

        return self.x

    @property
    def estimate(self) -> float:
        return self.x
```

### Realistic Accuracy Expectations

**Key finding: Exact distance estimation from RSSI is unreliable. Use zone-based proximity instead.**

| Scenario | Distance accuracy | Notes |
|----------|------------------|-------|
| Open space, line of sight | +/- 1-2m at 5m | Best case |
| Indoor, same room | +/- 2-4m | Reflections, body blocking |
| Through walls | Nearly useless for distance | But still works for presence/absence |
| Near/Far binary classification | 85-95% accuracy | Practical and recommended |

**Recommended approach for AuraLock -- Zone-Based Detection:**

```python
from enum import Enum
from dataclasses import dataclass

class ProximityZone(Enum):
    IMMEDIATE = "immediate"   # < 1m, RSSI typically > -55 dBm
    NEAR = "near"             # 1-3m, RSSI typically -55 to -70 dBm
    FAR = "far"               # 3-10m, RSSI typically -70 to -85 dBm
    OUT_OF_RANGE = "unknown"  # > 10m or no signal

@dataclass
class ProximityConfig:
    """Configurable RSSI thresholds for proximity zones.

    These MUST be calibrated per-device and per-environment.
    """
    immediate_threshold: int = -55   # dBm
    near_threshold: int = -70        # dBm
    far_threshold: int = -85         # dBm
    # Hysteresis to prevent zone flapping
    hysteresis: int = 3              # dBm

def classify_zone(
    smoothed_rssi: float,
    config: ProximityConfig,
    current_zone: ProximityZone,
) -> ProximityZone:
    """
    Classify RSSI into proximity zone with hysteresis.
    Hysteresis prevents rapid zone switching at boundaries.
    """
    h = config.hysteresis

    if current_zone == ProximityZone.IMMEDIATE:
        if smoothed_rssi < config.immediate_threshold - h:
            if smoothed_rssi >= config.near_threshold:
                return ProximityZone.NEAR
            elif smoothed_rssi >= config.far_threshold:
                return ProximityZone.FAR
            else:
                return ProximityZone.OUT_OF_RANGE
    elif current_zone == ProximityZone.NEAR:
        if smoothed_rssi > config.immediate_threshold + h:
            return ProximityZone.IMMEDIATE
        elif smoothed_rssi < config.near_threshold - h:
            if smoothed_rssi >= config.far_threshold:
                return ProximityZone.FAR
            else:
                return ProximityZone.OUT_OF_RANGE
    elif current_zone == ProximityZone.FAR:
        if smoothed_rssi > config.near_threshold + h:
            return ProximityZone.NEAR
        elif smoothed_rssi < config.far_threshold - h:
            return ProximityZone.OUT_OF_RANGE
    elif current_zone == ProximityZone.OUT_OF_RANGE:
        if smoothed_rssi > config.far_threshold + h:
            if smoothed_rssi > config.near_threshold:
                return ProximityZone.NEAR
            else:
                return ProximityZone.FAR

    return current_zone

```

### Using Tx Power from Advertisements

BLE advertisements can include the Tx Power Level (measured power at 0m or 1m). This self-reported value improves distance estimation significantly because it accounts for per-device transmitter variation:

```python
def estimate_distance(rssi: float, tx_power: float, n: float = 2.5) -> float:
    """
    Estimate distance using the log-distance path loss model.

    Args:
        rssi: Smoothed RSSI reading (dBm)
        tx_power: Transmitted power at 1m reference distance (dBm)
                  Use advertised TxPower if available, or calibrated value.
        n: Path loss exponent (2.0 free space, 2.5-3.5 typical indoor)

    Returns:
        Estimated distance in meters.
    """
    if rssi >= tx_power:
        return 0.1  # Essentially at the transmitter

    distance = 10 ** ((tx_power - rssi) / (10 * n))
    return round(distance, 2)
```

**However, for AuraLock, prefer zone-based RSSI thresholds over distance estimation.** Zone classification with hysteresis is more robust than trying to convert to meters.

---

## 3. Screen Lock/Unlock Mechanisms on Linux (GNOME/Ubuntu)

### Locking the Screen

Multiple reliable methods exist. Use whichever matches the desktop environment:

#### Method 1: loginctl (Universal, systemd-based)

```python
import subprocess

def lock_screen():
    """Lock screen via loginctl. Works on any systemd-based desktop."""
    subprocess.run(["loginctl", "lock-session"], check=True)
```

How it works: `loginctl lock-session` sends a `Lock()` signal on the session's D-Bus object. The desktop environment's screen locker (GNOME Shell, KDE, etc.) listens for this signal and activates its lock screen. If no session ID is provided, it targets the caller's session.

#### Method 2: GNOME ScreenSaver D-Bus API

```python
import dbus

def lock_screen_gnome():
    """Lock via GNOME ScreenSaver D-Bus interface."""
    bus = dbus.SessionBus()
    screensaver = dbus.Interface(
        bus.get_object("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver"),
        "org.gnome.ScreenSaver"
    )
    screensaver.Lock()

def is_screen_locked_gnome() -> bool:
    """Check if GNOME screen is currently locked."""
    bus = dbus.SessionBus()
    screensaver = dbus.Interface(
        bus.get_object("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver"),
        "org.gnome.ScreenSaver"
    )
    return bool(screensaver.GetActive())
```

**GNOME ScreenSaver D-Bus interface (org.gnome.ScreenSaver):**
- `Lock()`: Locks the screen (no parameters, no return)
- `GetActive()`: Returns boolean -- whether lock screen is active
- `SetActive(bool)`: Enable/disable screensaver
- Signal `ActiveChanged(bool)`: Emitted when lock state changes
- Signal `WakeUpScreen()`: Emitted when screen should wake

#### Method 3: D-Bus with subprocess (fallback)

```python
import subprocess

def lock_screen_dbus_send():
    """Lock via dbus-send CLI (no Python D-Bus dependency needed)."""
    subprocess.run([
        "dbus-send",
        "--type=method_call",
        "--dest=org.gnome.ScreenSaver",
        "/org/gnome/ScreenSaver",
        "org.gnome.ScreenSaver.Lock"
    ], check=True)
```

### Unlocking the Screen -- The Hard Problem

**Programmatic unlock is intentionally difficult and carries significant security implications.**

#### loginctl unlock-session

```python
def unlock_screen():
    """Send unlock signal via loginctl."""
    subprocess.run(["loginctl", "unlock-session"], check=True)
```

This sends an `Unlock()` D-Bus signal to the session. **However, on GNOME, the GNOME Shell lock screen ignores this signal.** GNOME Shell requires actual PAM authentication (password/fingerprint/smartcard) to unlock. The `loginctl unlock-session` command works on some lightweight desktop environments but not GNOME by default.

#### PAM-Based Auto-Unlock

The most viable approach for auto-unlock on GNOME is a custom PAM module:

```
# /etc/pam.d/gnome-screensaver or equivalent
# Add BEFORE pam_gnome_keyring.so:
auth sufficient pam_bt_proximity.so
```

A custom PAM module (written in C or via pam-python) could:
1. Check if the trusted Bluetooth device is nearby
2. If yes, return PAM_SUCCESS (bypassing password)
3. If no, return PAM_IGNORE (fall through to password)

**Security considerations for auto-unlock:**
- SIGNIFICANT security risk: anyone near your Bluetooth device can unlock your machine
- If phone is stolen, computer auto-unlocks for the thief
- BLE addresses can be spoofed (though pairing mitigates this)
- Recommended: require BOTH proximity AND another factor (e.g., short PIN, biometric)

#### Recommended Approach: Lock Aggressively, Notify for Unlock

```python
class ScreenController:
    """
    Conservative approach:
    - Auto-LOCK when device goes out of range (safe)
    - NOTIFY when device returns (user unlocks manually)
    - Optional: auto-unlock with security caveats
    """

    def __init__(self):
        self.bus = dbus.SessionBus()

    def lock(self):
        """Safe to automate -- just activates the lock screen."""
        screensaver = dbus.Interface(
            self.bus.get_object("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver"),
            "org.gnome.ScreenSaver"
        )
        if not screensaver.GetActive():
            screensaver.Lock()

    def notify_unlock_ready(self):
        """Send desktop notification when device is back in range."""
        subprocess.run([
            "notify-send",
            "--urgency=low",
            "--icon=system-lock-screen",
            "AuraLock",
            "Your trusted device is nearby. Screen is ready to unlock."
        ])

    def is_locked(self) -> bool:
        screensaver = dbus.Interface(
            self.bus.get_object("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver"),
            "org.gnome.ScreenSaver"
        )
        return bool(screensaver.GetActive())
```

### Desktop Environment Compatibility

| DE | Lock Method | Unlock Possible? | D-Bus Interface |
|----|-------------|------------------|-----------------|
| GNOME Shell | loginctl / org.gnome.ScreenSaver.Lock | No (PAM only) | org.gnome.ScreenSaver |
| KDE Plasma | loginctl / org.freedesktop.ScreenSaver.Lock | Via D-Bus (some versions) | org.freedesktop.ScreenSaver |
| XFCE | xflock4 / loginctl | Via xfce4-screensaver | org.xfce.ScreenSaver |
| Cinnamon | loginctl | Via D-Bus (some versions) | org.cinnamon.ScreenSaver |
| i3/sway | i3lock / swaylock | Process kill (insecure) | N/A |

---

## 4. Existing Projects & Prior Art

### Blueproximity (Thor77/Blueproximity)

**Repository:** github.com/Thor77/Blueproximity
**Language:** Python (91.9%)
**Status:** Working CLI, incomplete GUI, 36 open issues

**Approach:**
- Signal strength-based relative proximity (explicitly states "no measurement in meters is possible")
- Uses configurable numerical thresholds (not distance)
- Executes user-configurable shell commands for lock/unlock
- INI-style configuration files
- Requires manual pairing and MAC address specification

**Lock/Unlock commands (configurable):**
- Lock: `gnome-screensaver-command -l`
- Unlock: `gnome-screensaver-command -d`
- Custom commands supported (e.g., GAIM status changes)

**Architecture:**
- CLI-focused, event-driven
- Duration-based triggers (RSSI must be above/below threshold for N seconds)
- Configurable lock/unlock distances and durations

**Limitations & Problems:**
- CLI-only (GUI is broken)
- Uses deprecated gnome-screensaver-command
- Does not specify BLE vs Classic distinction
- No signal smoothing mentioned
- No advertisement monitor support

**Lessons for AuraLock:**
- Duration-based thresholds (not instant triggers) are essential to avoid false locks
- User-configurable commands increase flexibility
- Zone-based approach (near/far) rather than distance

### Other Notable Projects

**pam-bluetooth-proximity**: PAM module that checks Bluetooth device proximity during authentication. Uses l2ping or RSSI to verify device is nearby before allowing login. Demonstrates the PAM integration approach for auto-unlock.

**bluetooth-proximity (various)**: Multiple small projects exist on GitHub, most using either:
- `hcitool rssi <bdaddr>` (deprecated, requires active connection)
- `l2ping -c 1 <bdaddr>` (latency-based, Bluetooth Classic only)
- BlueZ D-Bus discovery with RSSI threshold

**Common problems across all projects:**
1. RSSI instability requiring smoothing/filtering
2. Difficulty with auto-unlock on modern GNOME
3. Bluetooth adapter power management interfering with scanning
4. False triggers from momentary signal dips (body blocking, phone in pocket)
5. Device going to sleep stopping BLE advertisements

---

## 5. Architecture Recommendations

### Service Architecture

**Recommendation: systemd user service (not system service)**

```ini
# ~/.config/systemd/user/auralock.service
[Unit]
Description=AuraLock Bluetooth Proximity Screen Locker
After=bluetooth.target graphical-session.target
Wants=bluetooth.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m auralock
Restart=on-failure
RestartSec=5
# Access to session D-Bus for screen locking
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%U/bus

[Install]
WantedBy=graphical-session.target
```

**Why user service (not system):**
- Needs access to session D-Bus (org.gnome.ScreenSaver is on session bus)
- Runs in the user's session context
- Automatically starts/stops with graphical session
- No root privileges needed for screen locking
- BlueZ D-Bus is on system bus but readable by users; scanning may need polkit authorization or group membership

**System bus access note:** The BlueZ D-Bus API runs on the system bus. The user process can read device properties without elevation, but `StartDiscovery()` and `RegisterMonitor()` may require the user to be in the `bluetooth` group or have appropriate polkit policies.

### Recommended Python Application Structure

```
auralock/
    __init__.py
    __main__.py           # Entry point
    config.py             # Configuration loading (TOML/YAML)
    scanner.py            # BLE scanning (BlueZ D-Bus or bleak)
    proximity.py          # RSSI smoothing, zone classification
    screen.py             # Lock/unlock/notification actions
    monitor.py            # BlueZ AdvertisementMonitor implementation
    devices.py            # Trusted device management
    notifier.py           # Desktop notifications
    service.py            # Main event loop, state machine
    tray.py               # Optional: system tray icon (GTK StatusIcon)
```

### Core State Machine

```python
from enum import Enum, auto
import time

class AuraLockState(Enum):
    INITIALIZING = auto()
    SCANNING = auto()
    DEVICE_NEAR = auto()       # Trusted device in range, screen unlocked
    DEVICE_LEAVING = auto()    # RSSI dropping, grace period before lock
    DEVICE_AWAY = auto()       # Screen locked, waiting for return
    DEVICE_APPROACHING = auto() # RSSI rising, waiting to confirm arrival
    ADAPTER_ERROR = auto()     # Bluetooth adapter issues
    SUSPENDED = auto()         # User paused AuraLock

class AuraLockService:
    def __init__(self, config):
        self.config = config
        self.state = AuraLockState.INITIALIZING
        self.state_entered_at = time.monotonic()
        self.smoother = RSSIKalmanFilter()
        self.current_zone = ProximityZone.OUT_OF_RANGE
        self.screen = ScreenController()
        self.last_rssi_time = 0

    def on_rssi_update(self, device_address: str, rssi: int):
        """Called when a new RSSI reading is received for a trusted device."""
        if device_address not in self.config.trusted_devices:
            return

        self.last_rssi_time = time.monotonic()
        smoothed = self.smoother.update(rssi)
        new_zone = classify_zone(smoothed, self.config.proximity, self.current_zone)

        if new_zone != self.current_zone:
            self._handle_zone_transition(self.current_zone, new_zone)
            self.current_zone = new_zone

    def on_device_lost(self, device_address: str):
        """Called when BlueZ AdvertisementMonitor reports device lost."""
        if device_address not in self.config.trusted_devices:
            return
        self.current_zone = ProximityZone.OUT_OF_RANGE
        self._handle_zone_transition(self.current_zone, ProximityZone.OUT_OF_RANGE)

    def check_timeout(self):
        """Called periodically to detect 'no signal' timeout."""
        if self.state in (AuraLockState.DEVICE_NEAR, AuraLockState.DEVICE_LEAVING):
            elapsed = time.monotonic() - self.last_rssi_time
            if elapsed > self.config.no_signal_timeout:
                # No advertisements received -- device probably out of range
                self._transition_to(AuraLockState.DEVICE_AWAY)
                self.screen.lock()

    def _handle_zone_transition(self, old_zone, new_zone):
        if new_zone == ProximityZone.OUT_OF_RANGE:
            self._transition_to(AuraLockState.DEVICE_AWAY)
            self.screen.lock()
        elif new_zone == ProximityZone.FAR and old_zone in (
            ProximityZone.NEAR, ProximityZone.IMMEDIATE
        ):
            self._transition_to(AuraLockState.DEVICE_LEAVING)
            # Don't lock yet -- grace period handled by AdvMonitor RSSILowTimeout
        elif new_zone in (ProximityZone.NEAR, ProximityZone.IMMEDIATE):
            if self.state == AuraLockState.DEVICE_AWAY:
                self.screen.notify_unlock_ready()
            self._transition_to(AuraLockState.DEVICE_NEAR)

    def _transition_to(self, new_state: AuraLockState):
        old = self.state
        self.state = new_state
        self.state_entered_at = time.monotonic()
        # Log transition, emit metrics, update tray icon, etc.
```

### BLE Adapter Power Management

```python
import dbus

class BluetoothAdapterManager:
    """Manage Bluetooth adapter state for reliable scanning."""

    def __init__(self, adapter_path: str = "/org/bluez/hci0"):
        self.bus = dbus.SystemBus()
        self.adapter_path = adapter_path

    def _get_adapter_props(self):
        return dbus.Interface(
            self.bus.get_object("org.bluez", self.adapter_path),
            "org.freedesktop.DBus.Properties"
        )

    def ensure_powered(self) -> bool:
        """Ensure the Bluetooth adapter is powered on."""
        props = self._get_adapter_props()
        powered = props.Get("org.bluez.Adapter1", "Powered")
        if not powered:
            try:
                props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
                return True
            except dbus.exceptions.DBusException as e:
                # May need polkit authorization
                return False
        return True

    def is_discovering(self) -> bool:
        props = self._get_adapter_props()
        return bool(props.Get("org.bluez.Adapter1", "Discovering"))

    def get_adapter_info(self) -> dict:
        props = self._get_adapter_props()
        return {
            "powered": bool(props.Get("org.bluez.Adapter1", "Powered")),
            "discovering": bool(props.Get("org.bluez.Adapter1", "Discovering")),
            "address": str(props.Get("org.bluez.Adapter1", "Address")),
            "name": str(props.Get("org.bluez.Adapter1", "Name")),
        }
```

### Handling "Device Not Found" vs "Out of Range" vs "Powered Off"

This is a critical distinction:

```python
import time
from enum import Enum

class DeviceAbsenceReason(Enum):
    OUT_OF_RANGE = "out_of_range"       # RSSI decayed to nothing
    POWERED_OFF = "powered_off"         # Was connected, connection dropped
    AIRPLANE_MODE = "airplane_mode"     # Same as powered off
    ADAPTER_ERROR = "adapter_error"     # Our BT adapter has issues
    UNKNOWN = "unknown"

class DeviceTracker:
    """
    Track why a device disappeared to choose appropriate response.
    """

    def __init__(self):
        self.last_rssi: float | None = None
        self.last_seen: float = 0
        self.was_connected: bool = False
        self.rssi_trend: list[float] = []  # Recent RSSI history

    def infer_absence_reason(self) -> DeviceAbsenceReason:
        if not self.rssi_trend:
            return DeviceAbsenceReason.UNKNOWN

        # Gradual RSSI decay suggests walking away
        if len(self.rssi_trend) >= 3:
            trend = [self.rssi_trend[i+1] - self.rssi_trend[i]
                     for i in range(len(self.rssi_trend)-1)]
            if all(d < -1 for d in trend[-3:]):
                return DeviceAbsenceReason.OUT_OF_RANGE

        # Sudden disappearance suggests power off
        time_since_seen = time.monotonic() - self.last_seen
        if self.last_rssi and self.last_rssi > -60 and time_since_seen > 10:
            # Was very close but suddenly vanished
            return DeviceAbsenceReason.POWERED_OFF

        return DeviceAbsenceReason.OUT_OF_RANGE
```

### Graceful Degradation

```python
class AuraLockFallbacks:
    """
    Fallback strategies when primary scanning fails.
    """

    @staticmethod
    def try_l2ping(address: str, timeout: int = 2) -> bool:
        """
        Fallback: attempt Classic Bluetooth L2CAP ping.
        Works for paired Classic BT devices when BLE scanning fails.
        Requires the device to be paired.
        """
        try:
            result = subprocess.run(
                ["l2ping", "-c", "1", "-t", str(timeout), address],
                capture_output=True, timeout=timeout + 2
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    @staticmethod
    def try_hci_rssi(address: str) -> int | None:
        """
        Fallback: get RSSI via hcitool (deprecated but functional).
        Only works for actively connected Classic BT devices.
        """
        try:
            result = subprocess.run(
                ["hcitool", "rssi", address],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and "RSSI return value" in result.stdout:
                return int(result.stdout.strip().split(":")[1].strip())
        except Exception:
            pass
        return None
```

### Configuration File Format

```toml
# ~/.config/auralock/config.toml

[general]
scan_interval = 2.0          # seconds between scans (if using polling)
lock_grace_period = 15        # seconds after "far" before locking
unlock_notify = true          # send notification when device returns
auto_unlock = false           # dangerous: auto-unlock when device returns
log_level = "INFO"
log_file = "~/.local/share/auralock/auralock.log"

[bluetooth]
adapter = "hci0"
scan_mode = "passive"         # "active" or "passive"
use_advertisement_monitor = true  # prefer AdvMonitor API

[proximity]
# RSSI thresholds in dBm (calibrate per environment!)
immediate_threshold = -50
near_threshold = -65
far_threshold = -80
hysteresis = 5                # dBm hysteresis for zone transitions

# Smoothing
filter = "kalman"             # "ema" or "kalman"
ema_alpha = 0.3               # only used if filter = "ema"
kalman_process_noise = 0.008
kalman_measurement_noise = 4.0

# Timeouts for AdvertisementMonitor
rssi_high_timeout = 3         # seconds to confirm "near"
rssi_low_timeout = 10         # seconds to confirm "far"
no_signal_timeout = 30        # seconds with no signal = lock

# Path loss model (for distance estimation, if desired)
path_loss_exponent = 2.5
reference_tx_power = -59      # dBm at 1m (override with per-device value)

[[trusted_devices]]
name = "Vedant's Pixel"
address = "AA:BB:CC:DD:EE:FF"
type = "ble"                  # "ble" or "classic"
tx_power_1m = -59             # calibrated Tx power at 1m (optional)

[[trusted_devices]]
name = "Galaxy Watch"
address = "11:22:33:44:55:66"
type = "ble"

[actions]
lock_command = ""             # empty = use D-Bus (recommended)
unlock_command = ""           # empty = notify only
on_lock_extra = ""            # e.g., "playerctl pause"
on_unlock_extra = ""          # e.g., "playerctl play"

[notifications]
enabled = true
lock_notification = true
unlock_notification = true
low_battery_warning = true    # if device reports battery level
```

---

## 6. Advanced Features Worth Considering

### Multiple Trusted Devices

```python
from dataclasses import dataclass, field

@dataclass
class TrustedDevice:
    name: str
    address: str
    device_type: str = "ble"  # "ble" or "classic"
    tx_power_1m: int = -59
    smoother: RSSIKalmanFilter = field(default_factory=RSSIKalmanFilter)
    last_zone: ProximityZone = ProximityZone.OUT_OF_RANGE
    last_seen: float = 0

class MultiDevicePolicy:
    """
    Policy for multiple trusted devices.
    Lock only when ALL trusted devices are away.
    Unlock when ANY trusted device is near.
    """

    def __init__(self, devices: list[TrustedDevice]):
        self.devices = {d.address: d for d in devices}

    def should_lock(self) -> bool:
        """Lock when NO trusted device is in NEAR or IMMEDIATE zone."""
        return all(
            d.last_zone in (ProximityZone.FAR, ProximityZone.OUT_OF_RANGE)
            for d in self.devices.values()
        )

    def any_device_near(self) -> bool:
        """Any trusted device in NEAR or IMMEDIATE range."""
        return any(
            d.last_zone in (ProximityZone.NEAR, ProximityZone.IMMEDIATE)
            for d in self.devices.values()
        )
```

### Desktop Notifications with gi (GTK)

```python
import subprocess

def send_notification(
    title: str,
    body: str,
    icon: str = "bluetooth",
    urgency: str = "normal",
    timeout_ms: int = 5000,
):
    """Send desktop notification via notify-send."""
    subprocess.run([
        "notify-send",
        f"--urgency={urgency}",
        f"--icon={icon}",
        f"--expire-time={str(timeout_ms)}",
        "--app-name=AuraLock",
        title,
        body,
    ])

# Usage:
# send_notification("AuraLock", "Screen locked - device out of range", icon="system-lock-screen")
# send_notification("AuraLock", "Device detected nearby", icon="bluetooth-active")
```

### System Tray Icon (GTK)

```python
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AppIndicator3', '0.1')
from gi.repository import Gtk, AppIndicator3, GLib

class AuraLockTray:
    """System tray indicator showing AuraLock status."""

    ICONS = {
        "near": "bluetooth-active",
        "far": "bluetooth-disabled",
        "scanning": "bluetooth-paired",
        "error": "dialog-error",
    }

    def __init__(self, service):
        self.service = service
        self.indicator = AppIndicator3.Indicator.new(
            "auralock",
            self.ICONS["scanning"],
            AppIndicator3.IndicatorCategory.SYSTEM_SERVICES,
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_menu(self._build_menu())

    def _build_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()

        self.status_item = Gtk.MenuItem(label="Status: Initializing")
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)

        self.rssi_item = Gtk.MenuItem(label="RSSI: --")
        self.rssi_item.set_sensitive(False)
        menu.append(self.rssi_item)

        menu.append(Gtk.SeparatorMenuItem())

        pause_item = Gtk.MenuItem(label="Pause AuraLock")
        pause_item.connect("activate", self._on_pause)
        menu.append(pause_item)

        settings_item = Gtk.MenuItem(label="Settings...")
        settings_item.connect("activate", self._on_settings)
        menu.append(settings_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self._on_quit)
        menu.append(quit_item)

        menu.show_all()
        return menu

    def update_status(self, state: AuraLockState, rssi: float | None = None):
        """Update tray icon and menu based on current state."""
        GLib.idle_add(self._do_update, state, rssi)

    def _do_update(self, state, rssi):
        if state == AuraLockState.DEVICE_NEAR:
            self.indicator.set_icon_full(self.ICONS["near"], "Device nearby")
            self.status_item.set_label(f"Status: Device nearby")
        elif state == AuraLockState.DEVICE_AWAY:
            self.indicator.set_icon_full(self.ICONS["far"], "Device away")
            self.status_item.set_label("Status: Device away (locked)")
        elif state == AuraLockState.ADAPTER_ERROR:
            self.indicator.set_icon_full(self.ICONS["error"], "Error")
            self.status_item.set_label("Status: Adapter error")
        else:
            self.indicator.set_icon_full(self.ICONS["scanning"], "Scanning")
            self.status_item.set_label(f"Status: {state.name}")

        if rssi is not None:
            self.rssi_item.set_label(f"RSSI: {rssi:.0f} dBm")

    def _on_pause(self, widget):
        self.service.toggle_pause()

    def _on_settings(self, widget):
        subprocess.Popen(["xdg-open", str(self.service.config_path)])

    def _on_quit(self, widget):
        self.service.shutdown()
        Gtk.main_quit()
```

### Logging and Analytics

```python
import logging
import json
from pathlib import Path
from datetime import datetime

def setup_logging(log_file: str = "~/.local/share/auralock/auralock.log"):
    log_path = Path(log_file).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(),
        ],
    )

class ProximityLogger:
    """Log RSSI and zone events for analysis and threshold tuning."""

    def __init__(self, log_dir: str = "~/.local/share/auralock/data"):
        self.log_dir = Path(log_dir).expanduser()
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log_rssi(self, device: str, raw_rssi: int, smoothed_rssi: float, zone: str):
        """Append RSSI reading to daily CSV for analysis."""
        today = datetime.now().strftime("%Y-%m-%d")
        path = self.log_dir / f"rssi_{today}.csv"

        write_header = not path.exists()
        with open(path, "a") as f:
            if write_header:
                f.write("timestamp,device,raw_rssi,smoothed_rssi,zone\n")
            f.write(f"{datetime.now().isoformat()},{device},{raw_rssi},{smoothed_rssi:.1f},{zone}\n")

    def log_event(self, event_type: str, details: dict):
        """Log lock/unlock events."""
        today = datetime.now().strftime("%Y-%m-%d")
        path = self.log_dir / f"events_{today}.jsonl"

        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": event_type,
            **details,
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
```

---

## 7. Complete Recommended Technology Stack

| Component | Recommendation | Rationale |
|-----------|---------------|-----------|
| **BLE Scanning** | BlueZ D-Bus AdvertisementMonitor API (primary) + bleak (fallback/prototyping) | AdvMonitor is kernel-level, power-efficient, has built-in RSSI thresholds with hysteresis |
| **D-Bus Library** | `dbus-python` or `dasbus` | dbus-python is widely available; dasbus has nicer API |
| **RSSI Filtering** | Kalman filter (primary), EMA (simple alternative) | Kalman adapts gain automatically; EMA is simpler |
| **Proximity Model** | Zone-based with hysteresis (NOT distance-in-meters) | More reliable than distance estimation |
| **Screen Locking** | loginctl lock-session (universal) or org.gnome.ScreenSaver.Lock (GNOME) | loginctl works across DEs; GNOME D-Bus gives status checking |
| **Screen Unlocking** | Notification only (safe default); optional PAM module (advanced) | Auto-unlock is a security risk; user should opt in explicitly |
| **Notifications** | notify-send / libnotify | Standard, no dependencies |
| **Config Format** | TOML (via tomllib in Python 3.11+) | Human-readable, typed, standard library |
| **Service Manager** | systemd user service | Automatic start, restart, journal logging |
| **Tray Icon** | AppIndicator3 (GTK) | Works on GNOME, KDE, XFCE |
| **Logging** | Python logging + CSV for RSSI data | CSV enables easy analysis in pandas/matplotlib for threshold tuning |
| **Python Version** | 3.11+ | tomllib built-in, match statements, performance |

## 8. Key Dependencies (pip/system)

```
# Python packages
dbus-python          # or dasbus for modern D-Bus bindings
bleak>=0.21          # BLE scanning (optional, for prototyping/fallback)
PyGObject            # GTK bindings for tray icon
tomli                # TOML parser (or tomllib in Python 3.11+)

# System packages (apt)
bluez                # BlueZ Bluetooth stack
python3-dbus         # D-Bus Python bindings
python3-gi           # GObject Introspection
gir1.2-appindicator3-0.1  # System tray support
libnotify-bin        # notify-send command
```

## 9. Security Considerations Summary

1. **Auto-lock is safe** -- worst case is inconvenience (false lock)
2. **Auto-unlock is risky** -- should be opt-in, ideally combined with a second factor
3. **BLE MAC address spoofing** -- possible but mitigated if device is bonded/paired (uses IRK for address resolution)
4. **Relay attacks** -- BLE signal could theoretically be relayed; not a concern for casual threat model
5. **Denial of service** -- attacker could jam BLE causing persistent lock; fallback to manual unlock always available
6. **Privacy** -- BLE scanning sees all nearby devices; only store data for trusted devices
7. **Permissions** -- running as user service limits blast radius vs. system service

## 10. Implementation Priority Roadmap

**Phase 1 (MVP):**
- BlueZ D-Bus active discovery with RSSI monitoring
- Single trusted device
- EMA smoothing
- Zone-based lock trigger (FAR/OUT_OF_RANGE = lock)
- loginctl lock-session
- Basic CLI with config file

**Phase 2:**
- AdvertisementMonitor API for passive/power-efficient scanning
- Kalman filter
- Multiple trusted devices
- Desktop notifications
- systemd user service
- RSSI data logging for calibration

**Phase 3:**
- System tray icon
- Configurable actions (pause media, set status, etc.)
- Per-device Tx Power calibration wizard
- Optional PAM module for auto-unlock
- Web dashboard for RSSI analytics

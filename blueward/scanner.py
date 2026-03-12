"""BLE scanning via BlueZ D-Bus API.

Supports two modes:
- Active discovery (StartDiscovery + PropertiesChanged signals)
- Advertisement Monitor API (passive, power-efficient, with built-in RSSI hysteresis)
"""

import logging
from typing import Callable

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop

log = logging.getLogger(__name__)

BLUEZ_BUS = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
ADV_MONITOR_IFACE = "org.bluez.AdvertisementMonitor1"
ADV_MONITOR_MGR_IFACE = "org.bluez.AdvertisementMonitorManager1"

# Callback signatures
RSSICallback = Callable[[str, int], None]  # (mac_address, rssi)
DeviceLostCallback = Callable[[str], None]  # (mac_address)


def _mac_from_path(path: str) -> str:
    """Extract MAC address from BlueZ device object path."""
    # Path looks like /org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF
    segment = path.split("/")[-1]
    if segment.startswith("dev_"):
        return segment[4:].replace("_", ":").upper()
    return segment.replace("_", ":").upper()


class BlueZAdapter:
    """Manage a BlueZ Bluetooth adapter."""

    def __init__(self, adapter_path: str = "/org/bluez/hci0"):
        self.bus = dbus.SystemBus()
        self.adapter_path = adapter_path
        self._adapter_obj = self.bus.get_object(BLUEZ_BUS, adapter_path)
        self._adapter = dbus.Interface(self._adapter_obj, ADAPTER_IFACE)
        self._props = dbus.Interface(self._adapter_obj, PROPERTIES_IFACE)

    def ensure_powered(self) -> bool:
        powered = self._props.Get(ADAPTER_IFACE, "Powered")
        if not powered:
            try:
                self._props.Set(ADAPTER_IFACE, "Powered", dbus.Boolean(True))
                log.info("Bluetooth adapter powered on")
                return True
            except dbus.exceptions.DBusException as e:
                log.error("Failed to power on adapter: %s", e)
                return False
        return True

    @property
    def powered(self) -> bool:
        return bool(self._props.Get(ADAPTER_IFACE, "Powered"))

    @property
    def discovering(self) -> bool:
        return bool(self._props.Get(ADAPTER_IFACE, "Discovering"))

    @property
    def address(self) -> str:
        return str(self._props.Get(ADAPTER_IFACE, "Address"))

    def info(self) -> dict:
        return {
            "powered": self.powered,
            "discovering": self.discovering,
            "address": self.address,
            "name": str(self._props.Get(ADAPTER_IFACE, "Name")),
        }


class ActiveScanner:
    """BLE scanner using BlueZ active discovery + PropertiesChanged signals."""

    def __init__(
        self,
        adapter: BlueZAdapter,
        on_rssi: RSSICallback,
        trusted_macs: set[str],
    ):
        self.adapter = adapter
        self._on_rssi = on_rssi
        self._trusted_macs = {m.upper() for m in trusted_macs}
        self._running = False

    def start(self):
        if self._running:
            return

        self.adapter.ensure_powered()

        # Set discovery filter for BLE with duplicate data for continuous RSSI
        adapter_iface = dbus.Interface(
            self.adapter._adapter_obj, ADAPTER_IFACE
        )
        adapter_iface.SetDiscoveryFilter({
            "Transport": dbus.String("le"),
            "DuplicateData": dbus.Boolean(True),
        })

        # Listen for PropertiesChanged on any BlueZ device
        self.adapter.bus.add_signal_receiver(
            self._on_properties_changed,
            signal_name="PropertiesChanged",
            dbus_interface=PROPERTIES_IFACE,
            bus_name=BLUEZ_BUS,
            path_keyword="path",
        )

        adapter_iface.StartDiscovery()
        self._running = True
        log.info("Active BLE discovery started")

    def stop(self):
        if not self._running:
            return
        try:
            adapter_iface = dbus.Interface(
                self.adapter._adapter_obj, ADAPTER_IFACE
            )
            adapter_iface.StopDiscovery()
        except dbus.exceptions.DBusException:
            pass
        self._running = False
        log.info("Active BLE discovery stopped")

    def _on_properties_changed(self, interface, changed, invalidated, path=""):
        if interface != DEVICE_IFACE:
            return
        if "RSSI" not in changed:
            return

        mac = _mac_from_path(path)
        if mac not in self._trusted_macs:
            return

        rssi = int(changed["RSSI"])
        self._on_rssi(mac, rssi)


class AdvMonitor(dbus.service.Object):
    """BlueZ AdvertisementMonitor1 implementation for passive BLE monitoring."""

    def __init__(
        self,
        bus: dbus.SystemBus,
        path: str,
        on_rssi: RSSICallback,
        on_lost: DeviceLostCallback,
        rssi_high: int = -50,
        rssi_low: int = -75,
        rssi_high_timeout: int = 3,
        rssi_low_timeout: int = 10,
    ):
        super().__init__(bus, path)
        self._on_rssi = on_rssi
        self._on_lost = on_lost
        self._rssi_high = rssi_high
        self._rssi_low = rssi_low
        self._rssi_high_timeout = rssi_high_timeout
        self._rssi_low_timeout = rssi_low_timeout

    @dbus.service.method(ADV_MONITOR_IFACE, in_signature="", out_signature="")
    def Release(self):
        log.info("AdvertisementMonitor released")

    @dbus.service.method(ADV_MONITOR_IFACE, in_signature="", out_signature="")
    def Activate(self):
        log.info("AdvertisementMonitor activated")

    @dbus.service.method(ADV_MONITOR_IFACE, in_signature="o", out_signature="")
    def DeviceFound(self, device):
        mac = _mac_from_path(str(device))
        log.debug("AdvMonitor: device found %s", mac)
        # Fetch current RSSI from device properties
        try:
            bus = dbus.SystemBus()
            props = dbus.Interface(
                bus.get_object(BLUEZ_BUS, device), PROPERTIES_IFACE
            )
            rssi = int(props.Get(DEVICE_IFACE, "RSSI"))
            self._on_rssi(mac, rssi)
        except dbus.exceptions.DBusException:
            # RSSI may not be available yet; report a strong signal
            self._on_rssi(mac, self._rssi_high)

    @dbus.service.method(ADV_MONITOR_IFACE, in_signature="o", out_signature="")
    def DeviceLost(self, device):
        mac = _mac_from_path(str(device))
        log.info("AdvMonitor: device lost %s", mac)
        self._on_lost(mac)

    @dbus.service.method(PROPERTIES_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        return self.GetAll(interface)[prop]

    @dbus.service.method(PROPERTIES_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != ADV_MONITOR_IFACE:
            return {}
        return {
            "Type": dbus.String("or_patterns"),
            "RSSIHighThreshold": dbus.Int16(self._rssi_high),
            "RSSILowThreshold": dbus.Int16(self._rssi_low),
            "RSSIHighTimeout": dbus.UInt16(self._rssi_high_timeout),
            "RSSILowTimeout": dbus.UInt16(self._rssi_low_timeout),
            "RSSISamplingPeriod": dbus.UInt16(0),  # report all packets
            "Patterns": dbus.Array([], signature="(yyay)"),
        }


class AdvMonitorApp(dbus.service.Object):
    """ObjectManager wrapper required by BlueZ for AdvertisementMonitor registration."""

    def __init__(self, bus: dbus.SystemBus, path: str, monitor: AdvMonitor, monitor_path: str):
        super().__init__(bus, path)
        self._monitor = monitor
        self._monitor_path = monitor_path

    @dbus.service.method(OBJECT_MANAGER_IFACE, in_signature="", out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        return {
            self._monitor_path: {
                ADV_MONITOR_IFACE: self._monitor.GetAll(ADV_MONITOR_IFACE)
            }
        }


class PassiveScanner:
    """BLE scanner using BlueZ AdvertisementMonitor API (power-efficient passive monitoring)."""

    APP_PATH = "/org/blueward"
    MONITOR_PATH = "/org/blueward/monitor"

    def __init__(
        self,
        adapter: BlueZAdapter,
        on_rssi: RSSICallback,
        on_lost: DeviceLostCallback,
        trusted_macs: set[str] | None = None,
        rssi_high: int = -50,
        rssi_low: int = -75,
        rssi_high_timeout: int = 3,
        rssi_low_timeout: int = 10,
    ):
        self.adapter = adapter
        self._on_rssi = on_rssi
        self._on_lost = on_lost
        self._trusted_macs = {m.upper() for m in trusted_macs} if trusted_macs else set()
        self._monitor: AdvMonitor | None = None
        self._app: AdvMonitorApp | None = None
        self._rssi_high = rssi_high
        self._rssi_low = rssi_low
        self._rssi_high_timeout = rssi_high_timeout
        self._rssi_low_timeout = rssi_low_timeout

    def start(self):
        self.adapter.ensure_powered()

        bus = self.adapter.bus
        self._monitor = AdvMonitor(
            bus,
            self.MONITOR_PATH,
            on_rssi=self._on_rssi,
            on_lost=self._on_lost,
            rssi_high=self._rssi_high,
            rssi_low=self._rssi_low,
            rssi_high_timeout=self._rssi_high_timeout,
            rssi_low_timeout=self._rssi_low_timeout,
        )
        self._app = AdvMonitorApp(bus, self.APP_PATH, self._monitor, self.MONITOR_PATH)

        # Also listen for PropertiesChanged to get continuous RSSI updates
        bus.add_signal_receiver(
            self._on_properties_changed,
            signal_name="PropertiesChanged",
            dbus_interface=PROPERTIES_IFACE,
            bus_name=BLUEZ_BUS,
            path_keyword="path",
        )

        # Register with BlueZ
        mgr = dbus.Interface(
            bus.get_object(BLUEZ_BUS, self.adapter.adapter_path),
            ADV_MONITOR_MGR_IFACE,
        )
        mgr.RegisterMonitor(dbus.ObjectPath(self.APP_PATH))
        log.info("Passive AdvertisementMonitor registered")

    def stop(self):
        if self._app is not None:
            try:
                bus = self.adapter.bus
                mgr = dbus.Interface(
                    bus.get_object(BLUEZ_BUS, self.adapter.adapter_path),
                    ADV_MONITOR_MGR_IFACE,
                )
                mgr.UnregisterMonitor(dbus.ObjectPath(self.APP_PATH))
            except dbus.exceptions.DBusException:
                pass
            self._app = None
            self._monitor = None
            log.info("Passive AdvertisementMonitor unregistered")

    def _on_properties_changed(self, interface, changed, invalidated, path=""):
        if interface != DEVICE_IFACE:
            return
        if "RSSI" not in changed:
            return
        mac = _mac_from_path(path)
        if self._trusted_macs and mac not in self._trusted_macs:
            return
        rssi = int(changed["RSSI"])
        self._on_rssi(mac, rssi)


def init_dbus_mainloop():
    """Initialize the D-Bus main loop integration. Must be called before creating scanners."""
    DBusGMainLoop(set_as_default=True)

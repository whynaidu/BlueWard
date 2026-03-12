"""BlueWard CLI entry point.

Usage:
    blueward                  Start proximity monitoring (foreground)
    blueward --config PATH    Use a specific config file
    blueward --no-tray        Run without the system tray icon
    blueward --no-notify      Run without desktop notifications
    blueward --log-rssi       Log RSSI readings to file for calibration
    blueward --verbose        Enable debug logging
    blueward scan             One-shot BLE scan to discover nearby devices
    blueward status           Show current adapter and config info
    blueward setup            Interactive setup wizard
"""

import argparse
import logging
import sys

from . import __app_name__, __version__


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="blueward",
        description="Bluetooth proximity-based screen lock for Linux",
    )
    parser.add_argument(
        "--version", action="version", version=f"{__app_name__} {__version__}"
    )
    parser.add_argument(
        "--config", "-c", metavar="PATH",
        help="Path to config.toml",
    )
    parser.add_argument(
        "--no-tray", action="store_true",
        help="Disable system tray icon",
    )
    parser.add_argument(
        "--no-notify", action="store_true",
        help="Disable desktop notifications",
    )
    parser.add_argument(
        "--log-rssi", action="store_true",
        help="Log RSSI readings to file for calibration",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    subparsers = parser.add_subparsers(dest="command")

    # `blueward scan` — one-shot device discovery
    scan_parser = subparsers.add_parser("scan", help="Scan for nearby BLE devices")
    scan_parser.add_argument(
        "--duration", "-d", type=int, default=10,
        help="Scan duration in seconds (default: 10)",
    )

    # `blueward status` — show adapter info
    subparsers.add_parser("status", help="Show Bluetooth adapter and config info")

    # `blueward setup` — interactive setup wizard
    subparsers.add_parser("setup", help="Interactive setup wizard")

    return parser.parse_args()


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_scan(duration: int):
    """Run a one-shot BLE scan and print discovered devices."""
    from .scanner import init_dbus_mainloop, BlueZAdapter
    import dbus

    init_dbus_mainloop()

    try:
        adapter = BlueZAdapter()
    except dbus.exceptions.DBusException as e:
        print(f"Error: Could not connect to BlueZ: {e}", file=sys.stderr)
        sys.exit(1)

    if not adapter.ensure_powered():
        print("Error: Could not power on Bluetooth adapter", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning for {duration} seconds...")

    discovered: dict[str, dict] = {}

    def on_rssi(mac: str, rssi: int):
        if mac not in discovered:
            discovered[mac] = {"rssi_min": rssi, "rssi_max": rssi, "count": 0}
        d = discovered[mac]
        d["count"] += 1
        d["rssi_min"] = min(d["rssi_min"], rssi)
        d["rssi_max"] = max(d["rssi_max"], rssi)
        d["rssi_last"] = rssi

    # Use active scan with no MAC filter (empty set = accept all)
    bus = dbus.SystemBus()
    adapter_iface = dbus.Interface(adapter._adapter_obj, "org.bluez.Adapter1")

    adapter_iface.SetDiscoveryFilter({
        "Transport": dbus.String("le"),
        "DuplicateData": dbus.Boolean(True),
    })

    bus.add_signal_receiver(
        lambda iface, changed, invalidated, path="": (
            on_rssi(
                path.split("/")[-1][4:].replace("_", ":").upper(),
                int(changed["RSSI"]),
            )
            if iface == "org.bluez.Device1" and "RSSI" in changed
            else None
        ),
        signal_name="PropertiesChanged",
        dbus_interface="org.freedesktop.DBus.Properties",
        bus_name="org.bluez",
        path_keyword="path",
    )

    adapter_iface.StartDiscovery()

    from gi.repository import GLib
    loop = GLib.MainLoop()
    GLib.timeout_add_seconds(duration, loop.quit)

    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            adapter_iface.StopDiscovery()
        except Exception:
            pass

    # Also check known device names from BlueZ cache
    obj_mgr = dbus.Interface(
        bus.get_object("org.bluez", "/"),
        "org.freedesktop.DBus.ObjectManager",
    )
    objects = obj_mgr.GetManagedObjects()

    names: dict[str, str] = {}
    for path, ifaces in objects.items():
        dev = ifaces.get("org.bluez.Device1")
        if dev:
            mac = str(dev.get("Address", "")).upper()
            name = str(dev.get("Name", dev.get("Alias", "")))
            if mac and name:
                names[mac] = name

    if not discovered:
        print("No BLE devices found.")
        return

    print(f"\n{'MAC Address':<20} {'Name':<25} {'RSSI (last)':<12} {'Range':<15} {'Pkts':>5}")
    print("-" * 80)

    for mac in sorted(discovered, key=lambda m: discovered[m].get("rssi_last", -100), reverse=True):
        d = discovered[mac]
        name = names.get(mac, "")
        rssi = d.get("rssi_last", 0)
        rng = f"{d['rssi_min']} to {d['rssi_max']}"
        print(f"{mac:<20} {name:<25} {rssi:<12} {rng:<15} {d['count']:>5}")

    print(f"\nTotal: {len(discovered)} device(s)")
    print("\nTo add a device to BlueWard, add it to your config.toml:")
    print("  [[devices]]")
    print('  name = "My Device"')
    print('  mac = "XX:XX:XX:XX:XX:XX"')


def cmd_status(config_path: str | None):
    """Show adapter info and current config."""
    from .config import load_config

    config = load_config(config_path)

    # Adapter info
    try:
        from .scanner import init_dbus_mainloop, BlueZAdapter
        init_dbus_mainloop()
        adapter = BlueZAdapter()
        info = adapter.info()
        print(f"Adapter:      {info['name']} ({info['address']})")
        print(f"Powered:      {info['powered']}")
        print(f"Discovering:  {info['discovering']}")
    except Exception as e:
        print(f"Adapter:      Error - {e}")

    print()
    print(f"Policy:       {config.policy_mode}")
    print(f"Lock delay:   {config.lock_delay}s")
    print(f"Unlock delay: {config.unlock_delay}s")
    print(f"Filter:       {config.filter.method}")
    print(f"Notifications: {config.notifications}")
    print(f"Tray icon:    {config.tray_icon}")
    print()

    if config.devices:
        print("Trusted devices:")
        for dev in config.devices:
            z = dev.zones
            print(f"  {dev.name} ({dev.mac})")
            print(f"    RSSI@1m: {dev.rssi_at_1m}  Zones: immediate={z.immediate} near={z.near} far={z.far}")
    else:
        print("No trusted devices configured.")
        print("Run 'blueward scan' to find nearby BLE devices.")


def cmd_run(config_path: str | None, no_tray: bool, no_notify: bool, log_rssi: bool):
    """Start the main BlueWard monitoring service."""
    from .config import load_config
    from .service import BlueWardService

    config = load_config(config_path)

    if no_notify:
        config.notifications = False
    if no_tray:
        config.tray_icon = False
    if log_rssi:
        config.log_rssi = True

    if not config.devices:
        print("No trusted devices configured.", file=sys.stderr)
        print("Run 'blueward setup' to configure a device interactively.", file=sys.stderr)
        sys.exit(1)

    service = BlueWardService(config)

    # System tray
    if config.tray_icon:
        try:
            from .tray import TrayIcon
            _tray = TrayIcon(service)
        except Exception as e:
            logging.getLogger(__name__).warning("Tray icon unavailable: %s", e)

    service.start()


def main():
    args = parse_args()
    setup_logging(args.verbose)

    if args.command == "scan":
        cmd_scan(args.duration)
    elif args.command == "status":
        cmd_status(args.config)
    elif args.command == "setup":
        from .setup import run_setup
        run_setup()
    else:
        cmd_run(args.config, args.no_tray, args.no_notify, args.log_rssi)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Skylight CLI - control and diagnose the lamp.

    python3 skylight.py on            # turn on
    python3 skylight.py off           # turn off
    python3 skylight.py toggle        # toggle
    python3 skylight.py status        # query current state
    python3 skylight.py scan          # BLE visibility + RSSI of the lamp
    python3 skylight.py provision     # freshly take into the mesh (see provision.py)

Note: on the Skylight, only on/off works reliably. Brightness, white tone and
the modes run over the remote's proprietary protocol and cannot be controlled
(see README).
"""

import asyncio
import sys

from bleak import BleakScanner

from meshlib.skylight import SkylightClient, CONFIG_FILE
from meshlib.state import load_cfg


def log(msg):
    print(msg, flush=True)


async def cmd_power(target):
    async with SkylightClient(log=log) as sky:
        if target == "toggle":
            target = not await sky.get_power()
        state = await sky.set_power(target)
        print(f"Lamp is now: {'ON' if state else 'OFF'}")


async def cmd_status():
    async with SkylightClient(log=log) as sky:
        print(f"Lamp is: {'ON' if await sky.get_power() else 'OFF'}")


async def cmd_scan():
    cfg = load_cfg(CONFIG_FILE)
    mac = cfg["mac"].upper()
    print(f"Searching for lamp {mac} (8 s) ...")
    devs = await BleakScanner.discover(timeout=8.0, return_adv=True)
    for addr, (d, adv) in devs.items():
        if addr.upper() == mac:
            q = ("very good" if adv.rssi > -65 else "good" if adv.rssi > -75
                 else "marginal" if adv.rssi > -85 else "weak")
            svc = [u[4:8] for u in (adv.service_data or {})]
            print(f"  found: RSSI {adv.rssi} dBm ({q}), services {svc}")
            return
    print("  NOT found - lamp on & in range?")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd in ("on", "off"):
        asyncio.run(cmd_power(cmd == "on"))
    elif cmd == "toggle":
        asyncio.run(cmd_power("toggle"))
    elif cmd == "status":
        asyncio.run(cmd_status())
    elif cmd == "scan":
        asyncio.run(cmd_scan())
    elif cmd == "provision":
        import provision
        asyncio.run(provision.main())
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()

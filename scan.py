#!/usr/bin/env python3
"""
Step 1 - BLE visibility check.
Finds the Skylight (BLE name "BK_MESH_light") and shows RSSI + advertisement.

Purpose: confirm that the Raspberry Pi can even see the lamp over its own
Bluetooth - before we get into the mesh stack.

Setup (on the Pi):
    sudo apt install -y python3-pip bluez
    pip3 install bleak
Run:
    python3 scan.py
"""

import asyncio
from bleak import BleakScanner

TARGET = "BK_MESH"           # part of the Skylight name
MESH_PROV_UUID = "1827"      # Mesh Provisioning Service
MESH_PROXY_UUID = "1828"     # Mesh Proxy Service (after provisioning)


async def main():
    print("Scanning 15 s for BLE devices ... (lamp should be in range)\n")
    found = await BleakScanner.discover(timeout=15.0, return_adv=True)

    hits = []
    for addr, (dev, adv) in found.items():
        name = dev.name or adv.local_name or ""
        is_target = TARGET in name if name else False
        # also detect via the mesh service in case no name comes through
        svc = [u.split("-")[0][-4:] for u in (adv.service_uuids or [])]
        is_mesh = any(u in (MESH_PROV_UUID, MESH_PROXY_UUID) for u in svc)

        if is_target or is_mesh:
            hits.append((addr, name or "(no name)", adv.rssi, svc, adv.service_data))

    if not hits:
        print("NO mesh lamp found.")
        print("- Lamp switched on & in range?")
        print("- Bluetooth active on the Pi? (bluetoothctl -> power on)")
        print("- Close nRF Connect/any other app that blocks the connection.")
        return

    print("Found:\n")
    for addr, name, rssi, svc, sdata in hits:
        print(f"  {addr}   RSSI {rssi} dBm   name={name}")
        if svc:
            print(f"    services: {', '.join(svc)}")
        for uuid, data in (sdata or {}).items():
            print(f"    service_data {uuid.split('-')[0][-4:]}: {data.hex()}")
        print()

    print("If BK_MESH_light appears here -> the Pi sees the lamp. On to step 2.")


if __name__ == "__main__":
    asyncio.run(main())

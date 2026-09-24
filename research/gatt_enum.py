#!/usr/bin/env python3
"""
Diagnostic - list the lamp's GATT services.

Purpose: find out whether the Skylight, BESIDES the SIG mesh (0x1827/0x1828),
also offers the Telink-proprietary mesh GATT service. If so, we can control
brightness/color directly via its command characteristic (opcode 0xD2) - with
no remote sniff or firmware dump.

Telink-proprietary mesh service (base UUID):
    00010203-0405-0607-0809-0a0b0c0d19xx
    ..1911 Notify/Status   ..1912 Command   ..1913 OTA   ..1914 Pair

IMPORTANT: the lamp only advertises when it is NOT connected. So stop the
bridge service first:  sudo systemctl stop skylight-bridge

    python3 gatt_enum.py               # uses MAC from skylight-mesh.json
    python3 gatt_enum.py <MAC>
"""

# --- Path bootstrap: this tool lives in research/, while the stack + the
# config (skylight-mesh.json) live in the repo root one level up. ---
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)
_CFG = _os.path.join(_ROOT, "skylight-mesh.json")

import asyncio
import json
import sys

from bleak import BleakClient, BleakScanner

TELINK_PREFIX = "00010203-0405-0607-0809-0a0b0c0d19"
SIG_PROV = "1827"
SIG_PROXY = "1828"


def load_mac():
    try:
        return json.load(open(_CFG))["mac"]
    except Exception:
        return None


def tag(uuid: str) -> str:
    u = uuid.lower()
    if u.startswith(TELINK_PREFIX):
        return "  <== TELINK proprietary!"
    short = u.split("-")[0][-4:]
    if short == SIG_PROV:
        return "  (SIG Mesh Provisioning)"
    if short == SIG_PROXY:
        return "  (SIG Mesh Proxy)"
    return ""


async def main():
    mac = sys.argv[1] if len(sys.argv) > 1 else load_mac()
    if not mac:
        print("No MAC. Pass it as an argument.")
        return 1
    print(f"Searching for lamp {mac} ... (bridge must be stopped)")
    dev = await BleakScanner.find_device_by_address(mac, timeout=20.0)
    if not dev:
        print("Not found. Is it advertising? -> bridge stopped? "
              "Power-cycle the lamp?")
        return 1
    print(f"Found: {dev.name or '(no name)'}  Connecting ...")
    async with BleakClient(dev) as client:
        print(f"Connected: {client.is_connected}\n")
        found_telink = False
        for svc in client.services:
            print(f"Service {svc.uuid}{tag(svc.uuid)}")
            if svc.uuid.lower().startswith(TELINK_PREFIX):
                found_telink = True
            for ch in svc.characteristics:
                props = ",".join(ch.properties)
                print(f"    char {ch.uuid}  [{props}]{tag(ch.uuid)}")
        print()
        if found_telink:
            print(">>> TELINK service present! We can control brightness/color "
                  "directly via the command char (..1912, opcode 0xD2).")
        else:
            print(">>> No Telink service. Then brightness probably runs "
                  "over a SIG VENDOR model (Company 0x0211) -> check the lamp's "
                  "Composition Data for vendor models.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

#!/usr/bin/env python3
"""
Diagnostic - read all readable GATT characteristics of the lamp.

Focus: the custom 0xFDA0 service (chars fda4/fda6/fda7/fda8). Their raw values
show whether there is a control/status interface (brightness?) there or only
OTA/version/config. Plus the device name & standard info for context.

IMPORTANT: stop the bridge first (the lamp only advertises when unconnected):
    sudo systemctl stop skylight-bridge

    python3 fda0_probe.py            # MAC from skylight-mesh.json
    python3 fda0_probe.py <MAC>
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


def load_mac():
    try:
        return json.load(open(_CFG))["mac"]
    except Exception:
        return None


def show(data: bytes) -> str:
    ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    return f"{data.hex():<40}  |{ascii_}|"


async def main():
    mac = sys.argv[1] if len(sys.argv) > 1 else load_mac()
    print(f"Searching for lamp {mac} ... (bridge stopped?)")
    dev = await BleakScanner.find_device_by_address(mac, timeout=20.0)
    if not dev:
        print("Not found.")
        return 1
    async with BleakClient(dev) as client:
        print(f"Connected: {client.is_connected}\n")
        for svc in client.services:
            is_fda0 = svc.uuid.lower().startswith("0000fda0")
            marker = "  <== custom 0xFDA0" if is_fda0 else ""
            print(f"Service {svc.uuid}{marker}")
            for ch in svc.characteristics:
                short = ch.uuid.split("-")[0][-4:]
                if "read" in ch.properties:
                    try:
                        val = bytes(await client.read_gatt_char(ch))
                        print(f"    {short} [{','.join(ch.properties)}] = {show(val)}")
                    except Exception as e:
                        print(f"    {short} [{','.join(ch.properties)}] = <read error: {e}>")
                else:
                    print(f"    {short} [{','.join(ch.properties)}] = <not readable>")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

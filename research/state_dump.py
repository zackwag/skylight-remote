#!/usr/bin/env python3
"""
Read-only state dump: queries all model states of the lamp via GET (OnOff,
Level, Lightness, CTL, CTL temp, HSL, current scene, scene register + vendor
attributes) and decodes the status responses. CHANGES NOTHING (GETs only).

Purpose: capture the state in ONE mode and compare it with another (default)
-> shows whether/where a mode set by the remote is readable.

Stop the bridge first (proxy exclusive):
    sudo systemctl stop skylight-bridge
    python3 research/state_dump.py
    sudo systemctl start skylight-bridge
"""

import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)

import asyncio

from meshlib import network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg, save_cfg
from meshlib.proxy import MeshProxy
from vendor_probe import listen, vendor_op

# (name, GET opcode, expected status opcode)
SIG_GETS = [
    ("OnOff",      0x8201, 0x8204),
    ("Level",      0x8205, 0x8208),
    ("Lightness",  0x824B, 0x824E),
    ("CTL",        0x825D, 0x8260),
    ("CTL-Temp",   0x8261, 0x8266),
    ("HSL",        0x826D, 0x8278),
    ("Scene(cur)", 0x8241, 0x5E),
    ("SceneReg",   0x8244, 0x8245),
]

# Vendor attribute GETs (structure [tid][attr 2B LE]) - usually no response
VENDOR_ATTRS = [("ONOFF", 0x0100), ("TARGET_TEMP", 0x010c),
                ("SCENE_MODE", 0xf004), ("WORKING_STATUS", 0xf001)]


def tid(cfg):
    cfg["tid"] = (cfg["tid"] + 1) & 0xFF
    return cfg["tid"]


async def get(proxy, cfg, app, lamp, iv, keys, op, want, params=b""):
    await proxy.send_access(cfg, app, True, lamp, op, params)
    for _k, r in await listen(proxy, lamp, iv, keys, 1.8):
        if r and (want is None or r[1][0] == want):
            return r[1]
    return None


async def main():
    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app = bytes.fromhex(cfg["app_key"])
    dev = bytes.fromhex(cfg["dev_key"])
    lamp, iv = cfg["unicast"], cfg["iv_index"]
    keys = (("app", app, 0x01), ("dev", dev, 0x02))

    print("=== SIG model states (GET, read-only) ===", flush=True)
    async with MeshProxy(cfg["mac"], ctx, cfg["src"], log=lambda *_: None) as proxy:
        for name, op, want in SIG_GETS:
            r = await get(proxy, cfg, app, lamp, iv, keys, op, want)
            if r:
                print(f"  {name:<11} status 0x{r[0]:x} = {r[1].hex()}", flush=True)
            else:
                print(f"  {name:<11} (no response)", flush=True)

        print("\n=== Vendor attribute GETs (0xD0) ===", flush=True)
        for aname, attr in VENDOR_ATTRS:
            payload = bytes([tid(cfg)]) + attr.to_bytes(2, "little")
            r = await get(proxy, cfg, app, lamp, iv, keys,
                          vendor_op(0xD0), None, payload)
            print(f"  {aname:<15} 0x{attr:04x} -> "
                  f"{'0x%x %s' % (r[0], r[1].hex()) if r else '(no response)'}",
                  flush=True)
        save_cfg(CONFIG_FILE, cfg)

    print("\n# Done. Note the value, compare with the other mode/default.")


if __name__ == "__main__":
    asyncio.run(main())

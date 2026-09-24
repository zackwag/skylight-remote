#!/usr/bin/env python3
"""
Vendor opcode sweep: sends the lamp every vendor opcode in a range with
contrasting payloads and lets YOU observe the reaction.

Per opcode: first 0x00 (dark pulse), then 0xff (bright). On the CORRECT
brightness opcode the lamp visibly blinks dark->bright. On the correct color
opcode the color changes. On all others: nothing.

    # range (hex) and payload width in bytes:
    python3 vendor_sweep.py C0 FF 1      # all 64, 1-byte payload
    python3 vendor_sweep.py D0 D5 2      # narrow, 2-byte payload
    python3 vendor_sweep.py D2 D2 1 raw 00ff  # one opcode, custom payloads

The bridge must be stopped.
"""

# --- Path bootstrap: this tool lives in research/, while the stack + the
# config (skylight-mesh.json) live in the repo root one level up. ---
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)
_CFG = _os.path.join(_ROOT, "skylight-mesh.json")

import asyncio
import sys

from meshlib import network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg, save_cfg
from meshlib.proxy import MeshProxy

COMPANY = 0x0211


def vop(b):
    return (b << 16) | COMPANY


async def send(proxy, cfg, key, op_byte, params):
    await proxy.send_access(cfg, key, True, cfg["unicast"], vop(op_byte), params)


async def onoff(proxy, cfg, key, on):
    cfg["tid"] = (cfg["tid"] + 1) & 0xFF
    # firmware quirk: inverted (0x00=ON)
    await proxy.send_access(cfg, key, True, cfg["unicast"], 0x8202,
                            bytes([0x00 if on else 0x01, cfg["tid"]]))


async def main():
    start = int(sys.argv[1], 16)
    end = int(sys.argv[2], 16)
    width = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    custom = None
    if len(sys.argv) > 5 and sys.argv[4] == "raw":
        # custom payloads, comma-separated hex after 'raw': e.g. 00ff,0064
        custom = [bytes.fromhex(x) for x in sys.argv[5].split(",")]

    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app = bytes.fromhex(cfg["app_key"])

    async with MeshProxy(cfg["mac"], ctx, cfg["src"], log=lambda *_: None) as proxy:
        print("Lamp ON, baseline bright ...", flush=True)
        await onoff(proxy, cfg, app, True)
        await asyncio.sleep(2)

        lo, hi = b"\x00" * width, b"\xff" * width
        n = end - start + 1
        print(f"=== Sweep 0x{start:02x}..0x{end:02x} ({n} opcodes, {width}B) - "
              f"watch for a BLINK or color/brightness change ===",
              flush=True)
        for idx, op in enumerate(range(start, end + 1)):
            secs = idx * 2  # rough timestamp for correlation
            print(f"[t~{secs:>3}s | #{idx:>2}] opcode 0x{op:02x}", flush=True)
            payloads = custom if custom else [lo, hi]
            for p in payloads:
                await send(proxy, cfg, app, op, p)
                await asyncio.sleep(1.0)

        # leave it cleanly bright/on
        await onoff(proxy, cfg, app, True)
        save_cfg(CONFIG_FILE, cfg)
        print("\n=== Sweep done. WHEN/at which #-number did the lamp "
              "react (blink/brightness/color)? ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

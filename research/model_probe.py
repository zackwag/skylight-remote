#!/usr/bin/env python3
"""
Comprehensive model prober: binds app_key to ALL controllable SIG models of the
lamp (including those provision.py never bound) and fires distinct commands -
you observe what has an effect (modes/color/brightness).

Candidates for the 'modes' (never tested before): Scene Recall, CTL temperature
(warm/cool = daylight?), Generic Level, HSL Hue/Saturation.

    python3 model_probe.py          # bind everything + command sequence
The bridge must be stopped.
"""

# --- Path bootstrap: this tool lives in research/, while the stack + the
# config (skylight-mesh.json) live in the repo root one level up. ---
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)
_CFG = _os.path.join(_ROOT, "skylight-mesh.json")

import asyncio

from meshlib import network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg, save_cfg
from meshlib.proxy import MeshProxy
from vendor_probe import listen

OP_BIND, OP_BIND_STATUS = 0x803D, 0x803E

# bind all controllable models (incl. previously unbound ones)
BIND_MODELS = [0x1000, 0x1002, 0x1004, 0x1006, 0x1300, 0x1301, 0x1303,
               0x1306, 0x1307, 0x130a, 0x130b, 0x1203]


def tid(cfg):
    cfg["tid"] = (cfg["tid"] + 1) & 0xFF
    return cfg["tid"]


async def do_bind(proxy, cfg, dev, lamp, iv, keys, model):
    params = lamp.to_bytes(2, "little") + b"\x00\x00" + model.to_bytes(2, "little")
    for _ in range(2):
        await proxy.send_access(cfg, dev, False, lamp, OP_BIND, params)
        for _k, r in await listen(proxy, lamp, iv, keys, 2.2):
            if r and r[1][0] == OP_BIND_STATUS:
                return "OK" if r[1][1][0] == 0 else f"ERR 0x{r[1][1][0]:02x}"
    return "no response"


async def fire(proxy, cfg, app, lamp, iv, keys, label, op, base, add_tid=True):
    params = base + (bytes([tid(cfg)]) if add_tid else b"")
    print(f">>> {label}", flush=True)
    await proxy.send_access(cfg, app, True, lamp, op, params)
    for _k, r in await listen(proxy, lamp, iv, keys, 1.8):
        if r:
            print(f"    <- status 0x{r[1][0]:x} {r[1][1].hex()}", flush=True)
    await asyncio.sleep(2.8)


async def main():
    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app, dev = bytes.fromhex(cfg["app_key"]), bytes.fromhex(cfg["dev_key"])
    lamp, iv = cfg["unicast"], cfg["iv_index"]
    keys = (("app", app, 0x01), ("dev", dev, 0x02))

    async with MeshProxy(cfg["mac"], ctx, cfg["src"], log=lambda *_: None) as proxy:
        print("=== Bind app_key to all models ===")
        for m in BIND_MODELS:
            print(f"  0x{m:04x}: {await do_bind(proxy, cfg, dev, lamp, iv, keys, m)}")

        print("\nLamp ON ...")
        await proxy.send_access(cfg, app, True, lamp, 0x8202, bytes([0x00, tid(cfg)]))
        await asyncio.sleep(2)

        print("\n=== Scene Register Get (which scenes/modes?) ===")
        await proxy.send_access(cfg, app, True, lamp, 0x8244, b"")
        for _k, r in await listen(proxy, lamp, iv, keys, 2.5):
            if r:
                print(f"  <- 0x{r[1][0]:x} {r[1][1].hex()}")

        # (label, opcode, base params without TID)
        WARM, COOL = (0x07D0).to_bytes(2, "little"), (0x1964).to_bytes(2, "little")
        duv = (0).to_bytes(2, "little")
        seq = [
            ("Scene Recall 1", 0x8242, (1).to_bytes(2, "little")),
            ("Scene Recall 2", 0x8242, (2).to_bytes(2, "little")),
            ("Scene Recall 3", 0x8242, (3).to_bytes(2, "little")),
            ("Scene Recall 4", 0x8242, (4).to_bytes(2, "little")),
            ("Scene Recall 5", 0x8242, (5).to_bytes(2, "little")),
            ("Scene Recall 6", 0x8242, (6).to_bytes(2, "little")),
            ("CTL temp WARM (2000K)", 0x8264, WARM + duv),
            ("CTL temp COOL (6500K)", 0x8264, COOL + duv),
            ("CTL Set warm+full", 0x825E, (0xFFFF).to_bytes(2, "little") + WARM + duv),
            ("CTL Set cool+full", 0x825E, (0xFFFF).to_bytes(2, "little") + COOL + duv),
            ("Level MAX", 0x8206, (0x7FFF).to_bytes(2, "little")),
            ("Level MIN", 0x8206, (-0x8000 & 0xFFFF).to_bytes(2, "little")),
            ("Lightness MAX", 0x824C, (0xFFFF).to_bytes(2, "little")),
            ("Lightness 25%", 0x824C, (0x4000).to_bytes(2, "little")),
            ("HSL red full", 0x8276, (0xFFFF).to_bytes(2, "little") + (0x0000).to_bytes(2, "little") + (0xFFFF).to_bytes(2, "little")),
            ("HSL green full", 0x8276, (0xFFFF).to_bytes(2, "little") + (0x5555).to_bytes(2, "little") + (0xFFFF).to_bytes(2, "little")),
            ("HSL blue full", 0x8276, (0xFFFF).to_bytes(2, "little") + (0xAAAA).to_bytes(2, "little") + (0xFFFF).to_bytes(2, "little")),
            ("HSL Hue red", 0x826F, (0x0000).to_bytes(2, "little")),
            ("HSL Sat full", 0x8273, (0xFFFF).to_bytes(2, "little")),
        ]
        print(f"\n=== {len(seq)} commands at ~5s intervals - WATCH THE LAMP ===")
        for label, op, base in seq:
            await fire(proxy, cfg, app, lamp, iv, keys, label, op, base)

        # leave it cleanly bright
        await proxy.send_access(cfg, app, True, lamp, 0x8202, bytes([0x00, tid(cfg)]))
        save_cfg(CONFIG_FILE, cfg)
        print("\n=== done. At WHICH label did the lamp react? ===")


if __name__ == "__main__":
    asyncio.run(main())

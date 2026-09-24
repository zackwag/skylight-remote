#!/usr/bin/env python3
"""
Hypothesis (yours): the modes run over the on/off path - the ONLY SIG command
that actually drives the LED driver. Maybe the OnOff byte is a MODE selector:
0x00=on, 0x01=off, 0x02+=on with mode 1,2,3...

Tests (watch the lamp):
  A) OnOff Set with byte 0x00,0x02..0x0A  (mode-as-value?)
  B) Repeated ON with a new TID           (tapping through -> mode cycle?)
  C) ON with transition/delay bytes        (mode in the extra fields?)

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

OP_ONOFF_SET = 0x8202


def tid(cfg):
    cfg["tid"] = (cfg["tid"] + 1) & 0xFF
    return cfg["tid"]


async def fire(proxy, cfg, app, lamp, iv, keys, label, params):
    print(f">>> {label}: params={params.hex()}", flush=True)
    await proxy.send_access(cfg, app, True, lamp, OP_ONOFF_SET, params)
    for _k, r in await listen(proxy, lamp, iv, keys, 1.5):
        if r:
            print(f"    <- 0x{r[1][0]:x} {r[1][1].hex()}", flush=True)
    await asyncio.sleep(3)


async def main():
    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app = bytes.fromhex(cfg["app_key"])
    dev = bytes.fromhex(cfg["dev_key"])
    lamp, iv = cfg["unicast"], cfg["iv_index"]
    keys = (("app", app, 0x01), ("dev", dev, 0x02))

    async with MeshProxy(cfg["mac"], ctx, cfg["src"], log=lambda *_: None) as proxy:
        print("Lamp ON (0x00) ...")
        await fire(proxy, cfg, app, lamp, iv, keys, "ON baseline", bytes([0x00, tid(cfg)]))

        print("\n=== A) OnOff byte as mode selector (0x02..0x0A) ===")
        for w in [0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A]:
            await fire(proxy, cfg, app, lamp, iv, keys,
                       f"OnOff wire=0x{w:02x}", bytes([w, tid(cfg)]))
            # safely turn ON again in between, in case a value acted as OFF
            await proxy.send_access(cfg, app, True, lamp, OP_ONOFF_SET,
                                    bytes([0x00, tid(cfg)]))
            await asyncio.sleep(0.5)

        print("\n=== B) Repeated ON (tapping through -> mode cycle?) ===")
        for i in range(6):
            await fire(proxy, cfg, app, lamp, iv, keys,
                       f"ON re-press #{i + 1}", bytes([0x00, tid(cfg)]))

        print("\n=== C) ON with transition/delay bytes (mode in the extra field?) ===")
        for t in [0x01, 0x02, 0x03, 0x04, 0x05, 0x06]:
            await fire(proxy, cfg, app, lamp, iv, keys,
                       f"ON transition=0x{t:02x}", bytes([0x00, tid(cfg), t, 0x00]))

        await proxy.send_access(cfg, app, True, lamp, OP_ONOFF_SET,
                                bytes([0x00, tid(cfg)]))
        save_cfg(CONFIG_FILE, cfg)
        print("\n=== done. At WHICH label did the lamp change? ===")


if __name__ == "__main__":
    asyncio.run(main())

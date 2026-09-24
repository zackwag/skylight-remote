#!/usr/bin/env python3
"""
RC key report sweep - emulates the original remote control.

Verified from the Telink SDK (Ai-Thinker-Open/Telink_SIG_Mesh, RGBCW_Ali_Mesh,
vendor_model.c):
  - Opcode VD_RC_KEY_REPORT = 0xC0 (Company 0x0211)
  - Sender vd_cmd_key_report(): payload = sizeof(vd_rc_key_report_t) = 8 bytes,
    struct { u8 code; u8 rsv[7] } -> [code][00 00 00 00 00 00 00]
  - The handler cb_vd_key_report() actually drives the light (RC_KEY_UP changes
    the brightness in the reference code).
  - Response: STATUS_NONE -> there is NEVER an acknowledgment, only the LED
    shows a hit.

Earlier (final_probe.py) only codes 0x00..0x1F were sent with 1-2 bytes (wrong
length). This tool sends the CORRECT 8-byte payload and sweeps the code range.
You watch the lamp: on the right code it visibly switches mode/brightness/color.

    sudo systemctl stop skylight-bridge
    python3 research/vendor_rc_sweep.py            # codes 0x00..0x3F
    python3 research/vendor_rc_sweep.py 00 ff      # full range
    python3 research/vendor_rc_sweep.py 20 40 2.0  # narrow, slower
    sudo systemctl start skylight-bridge

CAUTION: blindly across all codes there could theoretically be a reset/pairing
command in there too. The lamp is in our network and re-provisionable, but be
aware of it.
"""

# --- Path bootstrap ---
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)

import asyncio
import sys

from meshlib import network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg, save_cfg
from meshlib.proxy import MeshProxy
from vendor_probe import vendor_op

VD_RC_KEY_REPORT = 0xC0


def tid(cfg):
    cfg["tid"] = (cfg["tid"] + 1) & 0xFF
    return cfg["tid"]


async def onoff(proxy, cfg, app, lamp, on):
    await proxy.send_access(cfg, app, True, lamp, 0x8202,
                            bytes([0x00 if on else 0x01, tid(cfg)]))


async def key(proxy, cfg, app, lamp, code):
    # vd_rc_key_report_t: [code][rsv[7]] = 8 bytes
    payload = bytes([code]) + b"\x00" * 7
    await proxy.send_access(cfg, app, True, lamp, vendor_op(VD_RC_KEY_REPORT),
                            payload)


async def main():
    start = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x00
    end = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x3F
    step = float(sys.argv[3]) if len(sys.argv) > 3 else 1.3

    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app = bytes.fromhex(cfg["app_key"])
    lamp = cfg["unicast"]

    try:
        async with MeshProxy(cfg["mac"], ctx, cfg["src"], log=lambda *_: None) as proxy:
            print("Baseline: lamp ON, full brightness.", flush=True)
            await onoff(proxy, cfg, app, lamp, True)
            await asyncio.sleep(2)

            n = end - start + 1
            print(f"=== RC key sweep 0x{start:02x}..0x{end:02x} ({n} codes, "
                  f"8-byte payload) - WATCH THE LAMP ===", flush=True)
            t = 0
            for code in range(start, end + 1):
                print(f"  [t~{t:5.1f}s] key code = 0x{code:02x} ({code})",
                      flush=True)
                # send twice (packet loss); modes often react on the edge
                await key(proxy, cfg, app, lamp, code)
                await asyncio.sleep(0.15)
                await key(proxy, cfg, app, lamp, code)
                await asyncio.sleep(step)
                t += step + 0.15

            await onoff(proxy, cfg, app, lamp, True)
    finally:
        save_cfg(CONFIG_FILE, cfg)

    print("\n=== done. At WHICH time/WHICH code did the lamp "
          "visibly react? ===")


if __name__ == "__main__":
    asyncio.run(main())

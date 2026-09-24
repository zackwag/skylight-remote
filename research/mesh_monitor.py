#!/usr/bin/env python3
"""
Passive mesh capture - shows EVERYTHING the lamp emits on the network.

Motivation: all previous probes only listen briefly after one of our own sends
and hard-filter `if ctl or src != lamp`. That discards (a) control messages
(heartbeats) and (b) everything that does NOT come from the primary unicast
address. But the vendor model sits on element address 0x0002 -- a status
publication from there would never have been visible.

This tool pulls EVERY network-decryptable PDU out of the proxy (regardless of
src/dst/ctl), tries access-decrypt with the app AND dev key (unsegmented +
segmented, incl. ASZMIC) and otherwise logs raw hex. Optionally it toggles
On/Off in the meantime to provoke a state-bound publication.

    sudo systemctl stop skylight-bridge
    python3 research/mesh_monitor.py 45           # 45s purely passive
    python3 research/mesh_monitor.py 60 toggle    # 60s + On/Off provocation
    sudo systemctl start skylight-bridge
"""

# --- Path bootstrap: this tool lives in research/, while the stack + the
# config (skylight-mesh.json) live in the repo root one level up. ---
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)

import asyncio
import sys

from meshlib import network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg, save_cfg
from meshlib.proxy import MeshProxy
from vendor_probe import _try_decrypt, _seq_auth

OP_ONOFF_SET = 0x8202


def name_ctrl(op):
    return {0x0A: "HEARTBEAT", 0x00: "SEG-ACK", 0x0B: "FRIEND-POLL",
            0x0C: "FRIEND-UPDATE"}.get(op, f"CTRL-0x{op:02x}")


async def toggler(proxy, cfg, app, lamp, stop):
    """Toggles On/Off at intervals to provoke a publication."""
    on = True
    while not stop.is_set():
        cfg["tid"] = (cfg["tid"] + 1) & 0xFF
        wire = 0x00 if on else 0x01          # firmware quirk: inverted
        await proxy.send_access(cfg, app, True, lamp, OP_ONOFF_SET,
                                bytes([wire, cfg["tid"]]))
        print(f"    (toggle -> {'ON' if on else 'OFF'})", flush=True)
        on = not on
        try:
            await asyncio.wait_for(stop.wait(), timeout=6.0)
        except asyncio.TimeoutError:
            pass


async def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0
    do_toggle = len(sys.argv) > 2 and sys.argv[2].lower() == "toggle"

    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app = bytes.fromhex(cfg["app_key"])
    dev = bytes.fromhex(cfg["dev_key"])
    lamp, iv, mysrc = cfg["unicast"], cfg["iv_index"], cfg["src"]
    keys = (("app", app, 0x01), ("dev", dev, 0x02))

    # segment reassembly buffer per source
    segs = {}

    n_total = n_ctrl = n_lamp = n_undec = 0

    async with MeshProxy(cfg["mac"], ctx, mysrc, log=lambda *_: None) as proxy:
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        stop = asyncio.Event()
        tog = asyncio.create_task(toggler(proxy, cfg, app, lamp, stop)) \
            if do_toggle else None

        print(f"=== Capture {dur:.0f}s "
              f"({'with On/Off toggle' if do_toggle else 'purely passive'}) - "
              f"my src=0x{mysrc:04x}, lamp unicast=0x{lamp:04x} ===",
              flush=True)

        while True:
            rem = dur - (loop.time() - t0)
            if rem <= 0:
                break
            try:
                ctl, ttl, seq, src, dst, tr = await asyncio.wait_for(
                    proxy._rx.get(), timeout=rem)
            except asyncio.TimeoutError:
                break

            n_total += 1
            ts = loop.time() - t0
            mine = " (=ME)" if src == mysrc else ""
            head = f"[t~{ts:5.1f}] src=0x{src:04x}{mine} dst=0x{dst:04x} seq={seq}"

            if ctl:
                n_ctrl += 1
                print(f"{head}  CTRL {name_ctrl(tr[0] & 0x7f)} "
                      f"data={tr.hex()}", flush=True)
                continue

            if src == mysrc:                 # ignore our own echo sends
                continue

            if not (tr[0] & 0x80):           # unsegmented access PDU
                r = _try_decrypt(tr[1:], seq, src, dst, iv, 0, keys)
                if r:
                    kname, (op, params) = r
                    n_lamp += 1
                    print(f"{head}  ACCESS [{kname}] op=0x{op:x} "
                          f"params={params.hex()}  <== FROM THE LAMP",
                          flush=True)
                else:
                    n_undec += 1
                    print(f"{head}  <not decodable> raw={tr.hex()}",
                          flush=True)
                continue

            # segmented access PDU -> reassemble
            hdr = int.from_bytes(tr[1:4], "big")
            szmic = (hdr >> 23) & 1
            seq_zero = (hdr >> 10) & 0x1FFF
            seg_o, seg_n = (hdr >> 5) & 0x1F, hdr & 0x1F
            buf = segs.setdefault(src, {})
            buf[seg_o] = tr[4:]
            if len(buf) == seg_n + 1:
                cipher = b"".join(buf[i] for i in range(seg_n + 1))
                sa = _seq_auth(seq, seq_zero)
                r = _try_decrypt(cipher, sa, src, dst, iv, szmic, keys)
                segs[src] = {}
                if r:
                    kname, (op, params) = r
                    n_lamp += 1
                    print(f"{head}  SEG-ACCESS [{kname}] op=0x{op:x} "
                          f"params={params.hex()}  <== FROM THE LAMP",
                          flush=True)
                else:
                    n_undec += 1
                    print(f"{head}  SEG <not decodable> "
                          f"cipher={cipher.hex()}", flush=True)

        if tog:
            stop.set()
            await tog
        save_cfg(CONFIG_FILE, cfg)           # persist seq!

    print(f"\n=== CONCLUSION: {n_total} PDU(s) total | {n_ctrl} control | "
          f"{n_lamp} decoded lamp access | {n_undec} undecodable ===")
    if n_lamp == 0 and n_undec == 0 and n_ctrl == 0:
        print("The lamp emits NOTHING on the network on its own "
              "(no publication, no heartbeat) -> the publish-only hope is dead.")
    elif n_undec:
        print("There is undecodable traffic -> possibly a different key/network. "
              "Analyze the raw hex above.")


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
Proxy listener: the Pi connects as a GATT client to the remote (proxy server
0x1828), subscribes to Proxy Data Out (2ade), and logs EVERYTHING the remote
emits on a button press.

Safe: only enable notifications (CCCD write) + listen. NO OAD/firmware writes.

Caveat: the traffic is encrypted with the factory NetKey -> we see it raw
(ciphertext). But we see WHETHER and WHAT the remote sends per button press
(size/timing), and whether any unencrypted beacons/proxy config are in there. A
decode attempt with OUR NetKey runs along (will usually fail).

The remote must be awake -> HOLD DOWN a button, then press through the modes.

    sudo systemctl stop skylight-bridge
    python3 research/remote_listen.py [MAC] [duration_s]
    sudo systemctl start skylight-bridge
"""

import asyncio
import os
import sys
from datetime import datetime

from bleak import BleakClient, BleakScanner

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
from meshlib import network                     # noqa: E402
from meshlib.skylight import CONFIG_FILE        # noqa: E402
from meshlib.state import load_cfg              # noqa: E402

MAC = sys.argv[1] if len(sys.argv) > 1 else sys.exit(
    "Usage: remote_listen.py <REMOTE_MAC> [duration_s]  (find MAC via scan_all.py)")
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0

PROXY_OUT = "00002ade-0000-1000-8000-00805f9b34fb"   # notify
PROXY_IN = "00002add-0000-1000-8000-00805f9b34fb"    # write-no-resp


def stamp():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


async def find(timeout=8.0):
    fut = asyncio.get_event_loop().create_future()

    def cb(dev, adv):
        if dev.address.upper() == MAC.upper() and not fut.done():
            fut.set_result(dev)

    sc = BleakScanner(detection_callback=cb)
    await sc.start()
    try:
        return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        await sc.stop()


async def listen(dev, ctx):
    rx = []

    def on_note(handle, data):
        b = bytes(data)
        ptype = b[0] & 0x3F if b else -1
        line = f"[{stamp()}] 2ade <- {b.hex()} (proxytype=0x{ptype:02x})"
        # decode attempt with OUR NetKey (factory network -> expected: None)
        if ptype == 0x00 and len(b) > 1:
            dec = network.decode_network_pdu(ctx, b[1:])
            line += "  decode(our keys)=" + (
                "POSSIBLE!" if dec else "no (foreign network)")
        print(line, flush=True)
        rx.append(b)

    async with BleakClient(dev, timeout=20) as c:
        print(f"# connected: {c.is_connected}. Subscribing to 2ade ...", flush=True)
        await c.start_notify(PROXY_OUT, on_note)
        print(f"# Listening {DUR:.0f}s - NOW press through the modes on the remote!",
              flush=True)
        await asyncio.sleep(DUR)
        try:
            await c.stop_notify(PROXY_OUT)
        except Exception:
            pass
    return rx


async def main():
    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])

    print(f"# Searching for remote {MAC} - HOLD A BUTTON ...", flush=True)
    for _ in range(6):
        dev = await find(8.0)
        if dev:
            try:
                rx = await listen(dev, ctx)
                print(f"\n# === {len(rx)} notification(s) from the remote ===",
                      flush=True)
                if not rx:
                    print("# Nothing streamed. Without a valid filter the proxy "
                          "may not forward anything.", flush=True)
                return
            except Exception as e:
                print(f"# error ({e}), retry ...", flush=True)
        await asyncio.sleep(0.5)
    print("# Remote not reached. Was a button held? Move closer?", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

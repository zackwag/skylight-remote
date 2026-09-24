#!/usr/bin/env python3
"""
Squeeze the remote: connects to the remote control (proxy server 0x1828) and
reads out EVERYTHING readable - device name, appearance, device info (model/
firmware/manufacturer), custom characteristics + the full advertising
(manufacturer/service data).

The remote only transmits briefly on a button press -> the tool retries for
~40s. Meanwhile YOU must HOLD DOWN a button on the remote so it stays awake.

    sudo systemctl stop skylight-bridge
    python3 research/remote_probe.py [MAC] [duration_s]
    sudo systemctl start skylight-bridge
"""

import asyncio
import sys

from bleak import BleakClient, BleakScanner

MAC = sys.argv[1] if len(sys.argv) > 1 else sys.exit(
    "Usage: remote_probe.py <REMOTE_MAC> [duration_s]  (find MAC via scan_all.py)")
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 40.0


def show(data: bytes) -> str:
    a = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    return f"{data.hex():<48} |{a}|"


async def grab_adv(timeout=8.0):
    """Scans until MAC shows up, returns (device, advertisement)."""
    fut = asyncio.get_event_loop().create_future()

    def cb(dev, adv):
        if dev.address.upper() == MAC.upper() and not fut.done():
            fut.set_result((dev, adv))

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    try:
        return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        await scanner.stop()


async def dump_adv(adv):
    print(f"# --- Advertising ---", flush=True)
    print(f"#   local_name = {adv.local_name!r}  rssi={adv.rssi}", flush=True)
    for cid, data in (adv.manufacturer_data or {}).items():
        print(f"#   manufacturer 0x{cid:04x}: {bytes(data).hex()}", flush=True)
    for uuid, data in (adv.service_data or {}).items():
        print(f"#   service_data {uuid.split('-')[0][-4:]}: {bytes(data).hex()}",
              flush=True)
    print(f"#   service_uuids = {adv.service_uuids}", flush=True)


async def dump_gatt(dev):
    async with BleakClient(dev, timeout=20) as c:
        print(f"# connected: {c.is_connected}\n", flush=True)
        for svc in c.services:
            print(f"Service {svc.uuid}", flush=True)
            for ch in svc.characteristics:
                short = ch.uuid.split("-")[0][-4:]
                props = ",".join(ch.properties)
                if "read" in ch.properties:
                    try:
                        v = bytes(await c.read_gatt_char(ch))
                        print(f"    {short} [{props}] = {show(v)}", flush=True)
                    except Exception as e:
                        print(f"    {short} [{props}] = <read error: {e}>",
                              flush=True)
                else:
                    print(f"    {short} [{props}] = <not readable>", flush=True)


async def main():
    print(f"# Searching for remote {MAC} for up to {DUR:.0f}s - "
          f"NOW HOLD DOWN a button ...", flush=True)
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    while loop.time() - t0 < DUR:
        hit = await grab_adv(timeout=8.0)
        if hit:
            dev, adv = hit
            print(f"# >>> Remote found: {dev.name or '(no name)'}",
                  flush=True)
            await dump_adv(adv)
            try:
                await dump_gatt(dev)
                print("\n# done - everything readable above.", flush=True)
                return
            except Exception as e:
                print(f"# GATT connect failed ({e}), retry ...",
                      flush=True)
        await asyncio.sleep(0.5)
    print("# Remote not reached. Was a button held down? Move closer?",
          flush=True)


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
Broad BLE scan - lists ALL devices (not just the lamp), to find the original
remote control and see whether it can be provisioned.

Markers:
  0x1827  Mesh Provisioning  -> UNPROVISIONED, pairable (that's what we want!)
  0x1828  Mesh Proxy         -> already provisioned (e.g. our lamp)

Factory remotes often transmit only on a button press / in pairing mode. That's
why the scan runs longer and also shows weak/nameless devices. Tip: during the
scan, press buttons on the remote or put it into reset/pairing mode.

    sudo systemctl stop skylight-bridge
    python3 research/scan_all.py            # 20s
    python3 research/scan_all.py 40         # 40s
    sudo systemctl start skylight-bridge
"""

import asyncio
import sys

from bleak import BleakScanner

PROV = "1827"
PROXY = "1828"


def short(u: str) -> str:
    return u.split("-")[0][-4:]


async def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    seen = {}   # addr -> (name, rssi, set(short-uuids), service_data)

    def cb(dev, adv):
        uuids = {short(u) for u in (adv.service_uuids or [])}
        prev = seen.get(dev.address)
        # keep the strongest RSSI, accumulate UUIDs
        name = dev.name or adv.local_name or (prev[0] if prev else "")
        rssi = adv.rssi if not prev else max(prev[1], adv.rssi)
        if prev:
            uuids |= prev[2]
        seen[dev.address] = (name, rssi, uuids, adv.service_data or {})

    print(f"=== Broad BLE scan {dur:.0f}s - now press buttons on the remote "
          f"/ pairing mode ===", flush=True)
    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(dur)
    await scanner.stop()

    print(f"\n{len(seen)} device(s) seen:\n")
    # sorted by RSSI (nearest first)
    for addr, (name, rssi, uuids, sdata) in sorted(
            seen.items(), key=lambda kv: -kv[1][1]):
        flags = []
        if PROV in uuids:
            flags.append("<== PROVISIONING 0x1827 (PAIRABLE!)")
        if PROXY in uuids:
            flags.append("(Proxy 0x1828 - already in the network)")
        us = ",".join(sorted(uuids)) or "-"
        print(f"  {addr}  RSSI {rssi:>4} dBm  name={name or '(no name)':<18} "
              f"uuids={us}  {' '.join(flags)}")
        for u, d in (sdata or {}).items():
            print(f"        service_data {short(u)}: {d.hex()}")

    prov = [a for a, v in seen.items() if PROV in v[2]]
    print()
    if prov:
        print(f">>> {len(prov)} pairable device(s) (0x1827): {prov}")
        print("    If one of them is the remote -> add it to our network with "
              "provision.py, then press the mode buttons + mesh_monitor.py.")
    else:
        print(">>> No 0x1827 seen. The remote may transmit only briefly on a "
              "button press (repeat the scan with a button held) or cannot be "
              "provisioned.")


if __name__ == "__main__":
    asyncio.run(main())

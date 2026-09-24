#!/usr/bin/env python3
"""
Fake lamp: the Pi poses as 'BK_MESH_light' so the remote connects to US instead
of the real lamp - and we log every write.

That gets us the REAL bytes the remote sends per button (modes, dimming) -
ground truth, without sniffer hardware.

Replicates the services of the real lamp (from gatt_enum/fda0_probe):
  0xFDA0 (custom base ...9b12ea): fda4[r] fda6[r/w] fda7[r/w] fda8[r/w]
  0x1828 SIG Mesh Proxy: 2add[write-no-resp] 2ade[notify]
fda7/fda8 are pre-populated with the lamp's real values so the remote takes us
for genuine.

Flow (set by the orchestrator):
  1. real lamp powered off, bridge stopped
  2. Pi MAC spoofed to the lamp MAC (btmgmt public-addr)
  3. start this script -> advertises -> press the remote

    sudo ~/imp-venv/bin/python imp_lamp.py [runtime_s]
"""

import asyncio
import subprocess
import sys
from datetime import datetime

from bless import (BlessServer, BlessGATTCharacteristic,
                   GATTCharacteristicProperties as Props,
                   GATTAttributePermissions as Perm)

# bless' own LE advertisement is rejected by BlueZ 5.82 ("Failed to register
# advertisement"). bluetoothctl advertising, on the other hand, works. So we
# patch out bless' advertising (the GATT server stays) and advertise separately.
from bless.backends.bluezdbus.dbus.application import BlueZGattApplication


async def _skip_adv(self, adapter):
    print("# (bless advertising skipped)", flush=True)


async def _skip_stop_adv(self, adapter):
    pass


BlueZGattApplication.start_advertising = _skip_adv
BlueZGattApplication.stop_advertising = _skip_stop_adv

NAME = "BK_MESH_light"


def set_advertising(on: bool):
    if on:
        script = ("menu advertise\nname BK_MESH_light\nuuids 0x1828\n"
                  "back\nadvertise on\n")
    else:
        script = "advertise off\n"
    subprocess.run(["bluetoothctl"], input=script, text=True,
                   capture_output=True, timeout=15)

FDA0 = "0000fda0-0000-1000-8000-00805f9b12ea"
FDA4 = "0000fda4-0000-1000-8000-00805f9b12ea"
FDA6 = "0000fda6-0000-1000-8000-00805f9b12ea"
FDA7 = "0000fda7-0000-1000-8000-00805f9b12ea"
FDA8 = "0000fda8-0000-1000-8000-00805f9b12ea"

PROXY = "00001828-0000-1000-8000-00805f9b34fb"
PROXY_IN = "00002add-0000-1000-8000-00805f9b34fb"    # write-without-response
PROXY_OUT = "00002ade-0000-1000-8000-00805f9b34fb"   # notify

# Initial values of the 0xFDA0 chars. For maximum authenticity, enter YOUR
# lamp's values read out with fda0_probe.py here (fda7/fda8 are device-specific,
# hence not hardcoded in the repo).
INIT = {
    FDA4: b"",
    FDA6: b"",
    FDA7: b"",       # e.g. bytes.fromhex("....") from fda0_probe.py
    FDA8: bytes.fromhex("01"),
}

log_lines = []


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def on_read(characteristic: BlessGATTCharacteristic, **kwargs):
    print(f"[{stamp()}] READ  {characteristic.uuid[4:8]} -> "
          f"{bytes(characteristic.value).hex() or '-'}", flush=True)
    return characteristic.value


def on_write(characteristic: BlessGATTCharacteristic, value, **kwargs):
    hx = bytes(value).hex()
    line = f"[{stamp()}] WRITE {characteristic.uuid[4:8]} = {hx}"
    print(line, flush=True)
    log_lines.append(line)
    characteristic.value = value


async def main():
    runtime = int(sys.argv[1]) if len(sys.argv) > 1 else 60

    server = BlessServer(name=NAME)
    server.read_request_func = on_read
    server.write_request_func = on_write

    rw = Props.read | Props.write
    rperm = Perm.readable | Perm.writeable

    # PROXY (0x1828) FIRST: bless advertises only services[0] -> only this
    # 16-bit UUID ends up in the advertising (small enough). FDA0 is 128-bit (too
    # large for the adv) and is found only after the connect, via GATT.
    await server.add_new_service(PROXY)
    await server.add_new_characteristic(
        PROXY, PROXY_IN, Props.write | Props.write_without_response, b"",
        Perm.writeable)
    await server.add_new_characteristic(
        PROXY, PROXY_OUT, Props.notify, b"", Perm.readable)

    await server.add_new_service(FDA0)
    await server.add_new_characteristic(FDA0, FDA4, Props.read, INIT[FDA4], Perm.readable)
    await server.add_new_characteristic(FDA0, FDA6, rw, INIT[FDA6], rperm)
    await server.add_new_characteristic(FDA0, FDA7, rw, INIT[FDA7], rperm)
    await server.add_new_characteristic(FDA0, FDA8, rw, INIT[FDA8], rperm)

    await server.start()
    set_advertising(True)
    print(f"# Fake lamp '{NAME}' running {runtime}s (advertised: 0x1828). "
          f"Now press the remote (go through the modes, then dim).", flush=True)
    try:
        await asyncio.sleep(runtime)
    finally:
        set_advertising(False)
        await server.stop()

    print(f"\n# === {len(log_lines)} writes received ===", flush=True)
    for l in log_lines:
        print(l, flush=True)
    if not log_lines:
        print("# No write. Did the remote connect? A MAC spoof may be "
              "needed / the remote accepts only the real lamp.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

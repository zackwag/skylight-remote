#!/usr/bin/env python3
"""
Step Z - capture mesh adv packets (Pi onboard BLE, NO dongle needed).

Uses the BlueZ tools that are on the Pi anyway:
  - `btmon -w` writes a raw HCI capture (BTSnoop, monitor format),
  - `bluetoothctl scan on` lets BlueZ scan properly across all 3 adv channels
    (better than a hand-built single-channel socket).
Then we parse the capture, extract from each advertising report the AD structure
with type 0x2A (Mesh Message) / 0x2B (Mesh Beacon) / 0x29 (PB-ADV) and print the
payload as hex - copy-paste-ready for decode_capture.py.

    sudo python3 sniff_mesh.py 30 > capture.hex   # capture for 30 s
    python3 decode_capture.py --pdu-file capture.hex

While it runs: press brightness/color on the remote. One press is sent
repeatedly, many times.

Needs root (btmon monitor socket) -> start with sudo. Linux/BlueZ ONLY.
Self-test of the parser (runs everywhere, without BLE):

    python3 sniff_mesh.py --selftest
"""

import argparse
import struct
import subprocess
import sys
import tempfile

MESH_AD_TYPES = {0x2A: "mesh-message", 0x2B: "mesh-beacon", 0x29: "pb-adv"}


def extract_ad(adv_data: bytes):
    """Split a BLE AD structure [len][type][data...]. -> list (type, data)."""
    out, i = [], 0
    while i < len(adv_data):
        ln = adv_data[i]
        if ln == 0 or i + 1 + ln > len(adv_data):
            break
        out.append((adv_data[i + 1], adv_data[i + 2:i + 1 + ln]))
        i += 1 + ln
    return out


def mesh_payloads(adv_data: bytes):
    """-> list (ad_type, payload_hex) only for mesh-relevant AD types."""
    return [(t, d.hex()) for t, d in extract_ad(adv_data) if t in MESH_AD_TYPES]


def parse_btsnoop(blob: bytes):
    """Parse BTSnoop in btmon 'monitor' format. -> list (addr_hex, rssi,
    adv_data) from LE Advertising Reports (Legacy 0x02 AND Extended 0x0D)."""
    if blob[:8] != b"btsnoop\x00":
        raise ValueError("not a BTSnoop file")
    reports = []
    i = 16                                   # skip the file header
    while i + 24 <= len(blob):
        _orig, incl, flags, _drops = struct.unpack(">IIII", blob[i:i + 16])
        i += 24                              # + 8 bytes timestamp
        pkt = blob[i:i + incl]
        i += incl
        if flags & 0xFFFF != 0x0003:         # event packets only
            continue
        # HCI-Event ohne 0x04-Prefix: [evt][plen][params]
        if len(pkt) < 4 or pkt[0] != 0x3E:   # LE Meta Event
            continue
        sub = pkt[2]
        num = pkt[3]
        off = 4
        try:
            if sub == 0x02:                  # Legacy LE Advertising Report
                for _ in range(num):
                    addr = pkt[off + 2:off + 8][::-1].hex(":")
                    dlen = pkt[off + 8]
                    data = pkt[off + 9:off + 9 + dlen]
                    rssi = pkt[off + 9 + dlen]
                    reports.append((addr, rssi - 256 if rssi > 127 else rssi, data))
                    off += 9 + dlen + 1      # + RSSI
            elif sub == 0x0D:                # Extended Advertising Report
                for _ in range(num):
                    # evt_type(2) addr_type(1) addr(6) prim_phy(1) sec_phy(1)
                    # sid(1) tx(1) rssi(1) per_int(2) dir_addr_type(1)
                    # dir_addr(6) data_len(1) data(N)  = 24-byte header
                    addr = pkt[off + 2:off + 8][::-1].hex(":")
                    rssi = pkt[off + 8]
                    dlen = pkt[off + 23]
                    data = pkt[off + 24:off + 24 + dlen]
                    reports.append((addr, rssi - 256 if rssi > 127 else rssi, data))
                    off += 24 + dlen
        except IndexError:
            continue
    return reports


def _capture(seconds: int):
    snoop = tempfile.NamedTemporaryFile(suffix=".snoop", delete=False).name
    print(f"# Scanning {seconds}s (btmon -> {snoop}). Now press remote "
          f"buttons ...", file=sys.stderr)
    btmon = subprocess.Popen(["btmon", "-w", snoop],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        subprocess.run(["bluetoothctl", "--timeout", str(seconds), "scan", "on"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        btmon.terminate()
        try:
            btmon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            btmon.kill()
    with open(snoop, "rb") as f:
        return f.read()


def run_sniffer(seconds: int):
    reports = parse_btsnoop(_capture(seconds))
    seen = set()
    for _addr, _rssi, adv in reports:
        for ad_type, payload in mesh_payloads(adv):
            if payload not in seen:
                seen.add(payload)
                print(f"{payload}  # {MESH_AD_TYPES[ad_type]}")
    print(f"# {len(seen)} unique mesh payload(s) from {len(reports)} "
          f"adv reports.", file=sys.stderr)
    if not seen:
        print("# No SIG mesh (0x2A) found -> possibly proprietary Telink adv."
              " Diagnose with:  sudo python3 sniff_mesh.py --diag", file=sys.stderr)


def run_diag(seconds: int, watch_mac: str = ""):
    """Shows the AD types per sender and (for 0xFF) the company ID. That way we
    see whether a Telink device (Company 0x0211) or a new sender shows up on a
    button press - even if it is NOT SIG mesh."""
    from collections import defaultdict
    reports = parse_btsnoop(_capture(seconds))
    per = defaultdict(lambda: {"n": 0, "rssi": -999, "ad": set(),
                               "companies": set(), "sample": ""})
    for addr, rssi, adv in reports:
        d = per[addr]
        d["n"] += 1
        d["rssi"] = max(d["rssi"], rssi)
        for t, data in extract_ad(adv):
            d["ad"].add(t)
            if t == 0xFF and len(data) >= 2:           # Manufacturer Specific
                d["companies"].add(int.from_bytes(data[:2], "little"))
                if not d["sample"]:
                    d["sample"] = data.hex()
            if t in MESH_AD_TYPES and not d["sample"]:
                d["sample"] = data.hex()
    print(f"# {len(reports)} adv reports from {len(per)} senders\n", file=sys.stderr)
    # sorted by RSSI (proximity) - the remote near the Pi is at the top
    for addr, d in sorted(per.items(), key=lambda kv: -kv[1]["rssi"]):
        ads = " ".join(f"0x{t:02x}" for t in sorted(d["ad"]))
        comp = " ".join(f"0x{c:04x}" for c in sorted(d["companies"]))
        tag = ""
        if 0x0211 in d["companies"]:
            tag += "  <== TELINK(0x0211)!"
        if watch_mac and addr.upper() == watch_mac.upper():
            tag += "  <== LAMP"
        if any(t in MESH_AD_TYPES for t in d["ad"]):
            tag += "  <== SIG-MESH"
        print(f"{addr}  rssi={d['rssi']:>4}  n={d['n']:>3}  AD:[{ads}]  "
              f"comp:[{comp}]  {d['sample'][:40]}{tag}")


def selftest():
    # 1) AD-Extractor
    mesh = bytes.fromhex("68aabbccddeeff")
    blob = (bytes([2, 0x01, 0x06]) + bytes([1 + len(mesh), 0x2A]) + mesh
            + bytes([3, 0x09]) + b"BK")
    ok_ad = mesh_payloads(blob) == [(0x2A, mesh.hex())]
    print("AD-Extractor:", mesh_payloads(blob))

    # 2) BTSnoop parser: wrap an event record with a legacy adv report carrying
    #    a mesh-message AD into a minimal monitor BTSnoop.
    adv = bytes([2, 0x01, 0x06]) + bytes([1 + len(mesh), 0x2A]) + mesh
    report = (bytes([0x00, 0x01]) + bytes.fromhex("112233445566")
              + bytes([len(adv)]) + adv + bytes([0xC0]))       # +RSSI
    evt = bytes([0x3E, 2 + len(report), 0x02, 0x01]) + report  # LE Meta / sub 0x02
    rec_hdr = struct.pack(">IIII", len(evt), len(evt), 0x00000003, 0) + b"\x00" * 8
    snoop = b"btsnoop\x00" + struct.pack(">II", 1, 2001) + rec_hdr + evt
    got = parse_btsnoop(snoop)
    ok_snoop = (len(got) == 1 and got[0][0] == "66:55:44:33:22:11"
                and mesh_payloads(got[0][2]) == [(0x2A, mesh.hex())])
    print("BTSnoop-Parser:", [(a, mesh_payloads(adv)) for a, _r, adv in got])

    ok = ok_ad and ok_snoop
    print("SELF-TEST:", "OK" if ok else "FAILED!")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("seconds", nargs="?", type=int, default=30,
                    help="capture duration in seconds (default 30)")
    ap.add_argument("--selftest", action="store_true",
                    help="only test the parsers (runs everywhere)")
    ap.add_argument("--diag", action="store_true",
                    help="diagnostic: ALL senders + AD types + company IDs")
    ap.add_argument("--watch-mac", default="",
                    help="mark this MAC in the diag output (e.g. the lamp)")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.diag:
        run_diag(args.seconds, args.watch_mac)
    else:
        run_sniffer(args.seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())

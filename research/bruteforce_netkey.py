#!/usr/bin/env python3
"""
Step X - test NetKey candidates against a remote-control capture.

Purpose: the Skylight remote sends its (proprietary Telink vendor) messages as
BLE SIG Mesh broadcasts. We have them in the *factory* network with *factory*
keys that we don't know. This script tries known/plausible default NetKeys
against a captured network PDU. If one matches, the CCM MIC is valid -> we can
decrypt the entire remote traffic and read the vendor opcode for
brightness/color.

IMPORTANT / honest assessment:
  A NetKey created via SIG Mesh provisioning is normally RANDOMLY generated -
  then there is no "default" and this brute list won't match. It only matches if
  the manufacturer was sloppy (fixed key in the firmware image, all-zero, ASCII
  pattern). It costs nothing to try. If nothing matches, the next step is the
  vendor app / the firmware dump - the key or the vendor opcode is there
  directly.

Needs NO Pi and NO BLE - pure computation. Runs on a Mac as well as a Pi.

--- How to get the network PDU (--pdu) ---
Record the remote button press with an nRF sniffer + Wireshark. In the mesh adv
packet you find the AD structure [len][0x2A][network-pdu...]. Copy the bytes
AFTER 0x2A (the network PDU begins with the IVI/NID byte). This script also
strips a leading [len][0x2A] AD header automatically if you include it.

Example:
    python3 bruteforce_netkey.py --pdu 68eca487...  --iv 0,1
    python3 bruteforce_netkey.py --pdu-file capture.hex --keys extra_keys.txt
    python3 bruteforce_netkey.py --selftest
"""

# --- Path bootstrap: this tool lives in research/, while the stack + the
# config (skylight-mesh.json) live in the repo root one level up. ---
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)
_CFG = _os.path.join(_ROOT, "skylight-mesh.json")

import argparse
import sys

from meshlib.network import NetContext, decode_network_pdu


# --- Candidate NetKeys ---------------------------------------------------
# 16 bytes hex. Freely extendable. Brute is instant, so be generous.
def _ascii_key(s: str) -> str:
    """ASCII string padded to 16 bytes with zeros -> hex. For the
    'lazy manufacturer uses a readable string' case."""
    return s.encode()[:16].ljust(16, b"\x00").hex()


BUILTIN_CANDIDATES = [
    ("all-zero",            "00000000000000000000000000000000"),
    ("all-ff",              "ffffffffffffffffffffffffffffffff"),
    ("0102..10",            "0102030405060708090a0b0c0d0e0f10"),
    ("SIG-spec-sample",     "f7a2a44f8e8a8029064f173ddc1e2b00"),  # demo/self-test only
    # speculative ASCII patterns (names typical of the Telink SDK); cost nothing:
    ("ascii:telink_mesh1",  _ascii_key("telink_mesh1")),
    ("ascii:TelinkMeshAll", _ascii_key("TelinkMeshAll")),
    ("ascii:telink",        _ascii_key("telink")),
    ("ascii:123",           _ascii_key("123")),
    ("ascii:BK_MESH_light", _ascii_key("BK_MESH_light")),
]


def load_key_file(path: str):
    out = []
    with open(path) as f:
        for i, line in enumerate(f, 1):
            h = line.strip().replace(" ", "").replace(":", "")
            if not h or h.startswith("#"):
                continue
            if len(h) != 32:
                print(f"  ! {path}:{i} skipped (not 16-byte hex): {h}",
                      file=sys.stderr)
                continue
            out.append((f"{path}:{i}", h))
    return out


def strip_ad_header(pdu: bytes) -> bytes:
    """Removes a leading BLE AD header [len][0x2A|0x2B] if present, otherwise
    returns the bytes unchanged."""
    if len(pdu) >= 2 and pdu[1] in (0x2A, 0x2B) and pdu[0] == len(pdu) - 1:
        return pdu[2:]
    return pdu


def try_all(pdu: bytes, candidates, iv_indices):
    hits = []
    for name, key_hex in candidates:
        key = bytes.fromhex(key_hex)
        for iv in iv_indices:
            ctx = NetContext(net_key=key, iv_index=iv)
            # cheap pre-filter: the NID must match
            if pdu[0] & 0x7F != ctx.nid:
                continue
            res = decode_network_pdu(ctx, pdu)
            if res is not None:
                hits.append((name, key_hex, iv, res))
    return hits


def report(hits):
    if not hits:
        print("\n>> NO candidate matches.")
        print("   The factory NetKey is probably random. Next step:")
        print("   decompile the vendor app or dump the Telink firmware (SWS port).")
        return 1
    print(f"\n>> HITS ({len(hits)}):")
    for name, key_hex, iv, res in hits:
        ctl, ttl, seq, src, dst, transport = res
        line = (f"   key={key_hex} ({name}) iv={iv}  "
                f"ctl={ctl} ttl={ttl} seq={seq} "
                f"src=0x{src:04x} dst=0x{dst:04x}")
        print(line)
        # Note: transport is still APPKEY-encrypted. Here we only see the
        # lower-transport header (AKF/AID), NOT the opcode.
        seg = bool(transport[0] & 0x80)
        akf = (transport[0] >> 6) & 1
        aid = transport[0] & 0x3F
        print(f"        lower-transport: {'segmented' if seg else 'unsegmented'}"
              f" akf={akf} aid=0x{aid:02x}  (opcode still needs the AppKey)")
        print(f"        transport={transport.hex()}")
    print("\n   NetKey found -> now use decode_capture.py + AppKey to fully "
          "decrypt the traffic and pick out the vendor opcode.")
    return 0


def selftest():
    """Round-trip: build a PDU with a known key, then recover it by brute force."""
    from meshlib.network import encode_network_pdu

    key_hex = "f7a2a44f8e8a8029064f173ddc1e2b00"  # SIG-Spec-Sample (in Liste)
    ctx = NetContext(net_key=bytes.fromhex(key_hex), iv_index=0)
    # a plausible access message (Generic OnOff Set unacked = 0x8203)
    transport = b"\x00" + b"\x82\x03\x01\x00"  # AKF/AID=0 (DevKey-style demo)
    pdu = encode_network_pdu(ctx, ctl=0, ttl=5, seq=1,
                             src=0x0002, dst=0x0001, transport=transport)
    print(f"Self-test PDU: {pdu.hex()}")
    # decoys + the real key are in BUILTIN_CANDIDATES
    hits = try_all(pdu, BUILTIN_CANDIDATES, [0, 1])
    ok = any(h[1] == key_hex for h in hits)
    report(hits)
    print("\nSELF-TEST:", "OK - brute force finds the known key."
          if ok else "FAILED!")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--pdu", help="network PDU as hex (with/without AD header)")
    g.add_argument("--pdu-file", help="file with one PDU hex per line")
    g.add_argument("--selftest", action="store_true",
                   help="round-trip self-test without a capture")
    ap.add_argument("--keys", help="extra file with NetKey candidates "
                                    "(16-byte hex per line)")
    ap.add_argument("--iv", default="0,1",
                    help="IV indices, comma-separated (default: 0,1)")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    candidates = list(BUILTIN_CANDIDATES)
    if args.keys:
        candidates += load_key_file(args.keys)

    iv_indices = [int(x) for x in args.iv.split(",")]

    pdus = []
    if args.pdu:
        pdus.append(args.pdu)
    elif args.pdu_file:
        with open(args.pdu_file) as f:
            pdus = [ln.strip() for ln in f if ln.strip()
                    and not ln.startswith("#")]
    else:
        ap.error("specify --pdu, --pdu-file or --selftest")

    print(f"{len(candidates)} candidates x {len(iv_indices)} IV indices "
          f"against {len(pdus)} PDU(s).")
    rc = 1
    for raw in pdus:
        pdu = strip_ad_header(bytes.fromhex(raw.replace(" ", "").replace(":", "")))
        print(f"\n--- PDU {pdu.hex()} (nid=0x{pdu[0] & 0x7f:02x}) ---")
        if report(try_all(pdu, candidates, iv_indices)) == 0:
            rc = 0
    return rc


if __name__ == "__main__":
    sys.exit(main())

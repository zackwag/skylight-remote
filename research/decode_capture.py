#!/usr/bin/env python3
"""
Step Y - FULLY decrypt a remote capture and find vendor opcodes.

Chain per captured network PDU:
  1. Try NetKey candidates -> decrypt the network layer
     (yields src/dst + lower-transport PDU, but opcode still encrypted).
  2. Try AppKey candidates -> decrypt the access layer
     (yields the real opcode + params).
  3. Filter out and show vendor access messages (company-ID opcodes) -
     that's exactly where the remote's Telink brightness/color command sits.

Uses the candidate list from bruteforce_netkey.py (all-zero, ASCII patterns, ...)
plus optional extra files for NetKey and AppKey. An AID pre-filter ensures that
only AppKeys with a matching AID compute CCM at all.

Limit: only UNSEGMENTED access messages. Short commands (on/off, brightness,
color) almost always fit into an unsegmented PDU. Segmented ones (>11 bytes
access) would have to be reassembled first - deliberately only reported here,
not decoded.

Needs NO Pi and NO BLE - pure computation.

    python3 decode_capture.py --pdu-file capture.hex
    python3 decode_capture.py --pdu 68eca4...  --net-keys nk.txt --app-keys ak.txt
    python3 decode_capture.py --selftest
"""

# --- Path bootstrap: this tool lives in research/, while the stack + the
# config (skylight-mesh.json) live in the repo root one level up. ---
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)
_CFG = _os.path.join(_ROOT, "skylight-mesh.json")

import argparse
import sys

from meshlib import crypto
from meshlib.network import NetContext, decode_network_pdu, decrypt_access

from bruteforce_netkey import BUILTIN_CANDIDATES, load_key_file, strip_ad_header

TELINK_COMPANY = 0x0211  # for reference: Telink Semiconductor SIG company ID


def parse_vendor_or_sig(access: bytes):
    """-> dict with opcode info. Distinguishes 1-/2-byte SIG from 3-byte vendor
    (company ID little-endian, correctly interpreted)."""
    b0 = access[0]
    if b0 & 0x80 == 0:                                  # 1-byte SIG
        return {"kind": "sig", "opcode": b0, "params": access[1:]}
    if b0 & 0xC0 == 0x80:                                # 2-byte SIG
        return {"kind": "sig", "opcode": int.from_bytes(access[:2], "big"),
                "params": access[2:]}
    # 3-byte vendor: [0b11 + 6-bit op][company-id LE]
    return {"kind": "vendor", "vendor_op": b0 & 0x3F,
            "company": int.from_bytes(access[1:3], "little"),
            "params": access[3:]}


def app_candidates_for_aid(aid: int, app_keys):
    """Only AppKeys whose k4 AID matches the lower-transport AID (pre-filter)."""
    for name, key_hex in app_keys:
        key = bytes.fromhex(key_hex)
        if crypto.k4(key) == aid:
            yield name, key_hex, key


def decode_one(pdu, net_keys, app_keys, iv_indices):
    results = []
    for nk_name, nk_hex in net_keys:
        nk = bytes.fromhex(nk_hex)
        for iv in iv_indices:
            ctx = NetContext(net_key=nk, iv_index=iv)
            if pdu[0] & 0x7F != ctx.nid:
                continue
            net = decode_network_pdu(ctx, pdu)
            if net is None:
                continue
            ctl, ttl, seq, src, dst, transport = net
            if ctl == 1:            # control message (not access) -> ignore
                continue
            if transport[0] & 0x80:                       # segmented
                results.append({"net": (nk_name, nk_hex, iv), "seq": seq,
                                "src": src, "dst": dst, "segmented": True})
                continue
            akf = (transport[0] >> 6) & 1
            aid = transport[0] & 0x3F
            if akf == 0:
                # DevKey message (configuration) - usually not the target
                results.append({"net": (nk_name, nk_hex, iv), "seq": seq,
                                "src": src, "dst": dst, "devkey": True})
                continue
            for ak_name, ak_hex, ak in app_candidates_for_aid(aid, app_keys):
                access = decrypt_access(ctx, ak, True, seq, src, dst, transport)
                if access is None:
                    continue
                info = parse_vendor_or_sig(access)
                results.append({"net": (nk_name, nk_hex, iv),
                                "app": (ak_name, ak_hex), "seq": seq,
                                "src": src, "dst": dst, "access": access,
                                "info": info})
    return results


def report(pdu, results):
    print(f"\n--- PDU {pdu.hex()} (nid=0x{pdu[0] & 0x7f:02x}) ---")
    decoded = [r for r in results if "info" in r]
    if not decoded:
        seg = any(r.get("segmented") for r in results)
        net_only = any("app" not in r and not r.get("segmented")
                       and not r.get("devkey") for r in results)
        if seg:
            print("  Network decrypted, but the access is SEGMENTED "
                  "-> reassembly needed (not decoded here).")
        elif any(r.get("devkey") for r in results):
            print("  Network decrypted (DevKey/config message), but "
                  "no matching AppKey candidate.")
        elif net_only:
            print("  Network decrypted, but no AppKey candidate matches.")
        else:
            print("  No NetKey candidate matches.")
        return False
    for r in decoded:
        nk_name, nk_hex, iv = r["net"]
        ak_name, ak_hex = r["app"]
        info = r["info"]
        print(f"  [OK] netkey={nk_hex} ({nk_name}) appkey={ak_hex} ({ak_name}) iv={iv}")
        print(f"       src=0x{r['src']:04x} dst=0x{r['dst']:04x} seq={r['seq']}")
        if info["kind"] == "vendor":
            tag = " <-- TELINK" if info["company"] == TELINK_COMPANY else ""
            print(f"       >>> VENDOR  company=0x{info['company']:04x}{tag}  "
                  f"vendor-op=0x{info['vendor_op']:02x}  "
                  f"params={info['params'].hex()}")
        else:
            print(f"       SIG opcode=0x{info['opcode']:x} "
                  f"params={info['params'].hex()}")
    return True


def selftest():
    """Real end-to-end round-trip: encrypt a vendor message (Net+App), then
    recover it blindly over the candidate list."""
    from meshlib.network import encode_access, encode_network_pdu, build_transport_pdus

    net_hex = "f7a2a44f8e8a8029064f173ddc1e2b00"   # SIG-Spec-Sample (in Liste)
    app_hex = "0102030405060708090a0b0c0d0e0f10"   # (ebenfalls in Liste)
    ctx = NetContext(net_key=bytes.fromhex(net_hex), iv_index=0)

    # fictitious Telink vendor brightness command: op=0x05, company=0x0211, level=200
    access = encode_access(0xC00000 | (0x05 << 16) | TELINK_COMPANY, b"\xc8")
    seq, src, dst = 7, 0x0005, 0x0002
    tpdus = build_transport_pdus(ctx, bytes.fromhex(app_hex), True, seq, src, dst, access)
    assert len(tpdus) == 1, "test message should be unsegmented"
    pdu = encode_network_pdu(ctx, 0, 5, seq, src, dst, tpdus[0])
    print(f"Self-test PDU (encrypted): {pdu.hex()}")

    results = decode_one(pdu, BUILTIN_CANDIDATES, BUILTIN_CANDIDATES, [0, 1])
    ok = report(pdu, results)
    hit = next((r for r in results if r.get("info", {}).get("kind") == "vendor"), None)
    good = bool(hit and hit["info"]["company"] == TELINK_COMPANY
                and hit["info"]["vendor_op"] == 0x05
                and hit["info"]["params"] == b"\xc8")
    print("\nSELF-TEST:", "OK - full chain (Net+App+Vendor) reconstructed."
          if good else "FAILED!")
    return 0 if good else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--pdu")
    g.add_argument("--pdu-file")
    g.add_argument("--selftest", action="store_true")
    ap.add_argument("--net-keys", help="extra NetKey candidates (hex/line)")
    ap.add_argument("--app-keys", help="extra AppKey candidates (hex/line)")
    ap.add_argument("--iv", default="0,1")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    net_keys = list(BUILTIN_CANDIDATES) + (load_key_file(args.net_keys) if args.net_keys else [])
    app_keys = list(BUILTIN_CANDIDATES) + (load_key_file(args.app_keys) if args.app_keys else [])
    iv_indices = [int(x) for x in args.iv.split(",")]

    if args.pdu:
        raw_list = [args.pdu]
    elif args.pdu_file:
        with open(args.pdu_file) as f:
            raw_list = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    else:
        ap.error("specify --pdu, --pdu-file or --selftest")

    print(f"{len(net_keys)} NetKey x {len(app_keys)} AppKey candidates "
          f"x {len(iv_indices)} IV against {len(raw_list)} PDU(s).")
    rc = 1
    for raw in raw_list:
        pdu = strip_ad_header(bytes.fromhex(raw.replace(" ", "").replace(":", "")))
        if report(pdu, decode_one(pdu, net_keys, app_keys, iv_indices)):
            rc = 0
    return rc


if __name__ == "__main__":
    sys.exit(main())

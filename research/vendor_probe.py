#!/usr/bin/env python3
"""
Vendor model probe: bind app_key to the Telink vendor model and send
vendor opcodes (Company 0x0211) + decode responses.

Goal: find the brightness/color channel. From Telink's open SDK:
  0xC0 VD_RC_KEY_REPORT      (remote reports a button press)
  0xC1..0xC4 VD_GROUP_G_*    (group get/set/status)
  0xD0..0xD5 VD_MSG_ATTR_*   (attribute get/set/status)

This run: BIND + a battery of GET-like opcodes. Responses are decoded with the
app_key AND dev_key, segmented and unsegmented (incl. ASZMIC).
Changes nothing on the lamp except the (reversible) model binding.

IMPORTANT: stop the bridge first.

    python3 vendor_probe.py bind      # bind only
    python3 vendor_probe.py probe     # bind + GET battery (default)
    python3 vendor_probe.py send C0 0100   # a single raw vendor command
"""

# --- Path bootstrap: this tool lives in research/, while the stack + the
# config (skylight-mesh.json) live in the repo root one level up. ---
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)
_CFG = _os.path.join(_ROOT, "skylight-mesh.json")

import asyncio
import sys

from meshlib import crypto, network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg
from meshlib.proxy import MeshProxy

COMPANY = 0x0211
VENDOR_MODELS = (0x0000, 0x0001)
OP_MODEL_APP_BIND = 0x803D
OP_MODEL_APP_STATUS = 0x803E


def vendor_op(op_byte: int) -> int:
    return (op_byte << 16) | COMPANY


def _nonce(ntype: int, seq: int, src: int, dst: int, iv: int, aszmic: int) -> bytes:
    return (bytes([ntype, aszmic << 7]) + seq.to_bytes(3, "big")
            + src.to_bytes(2, "big") + dst.to_bytes(2, "big")
            + iv.to_bytes(4, "big"))


def _seq_auth(rseq: int, seq_zero: int) -> int:
    cand = (rseq & ~0x1FFF) | seq_zero
    if cand > rseq:
        cand -= 0x2000
    return cand


def _try_decrypt(cipher, seq, src, dst, iv, aszmic, keys):
    mlen = 8 if aszmic else 4
    for kname, key, ntype in keys:
        nonce = _nonce(ntype, seq, src, dst, iv, aszmic)
        try:
            access = crypto.ccm_decrypt(key, nonce, cipher, mlen)
            return kname, network.parse_access(access)
        except Exception:
            pass
    return None


async def listen(proxy, lamp, iv, keys, seconds: float):
    """Collects & decodes all access responses from the lamp in the time window."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + seconds
    segs, seg_n, seq_auth, msrc, mdst, szmic = {}, None, None, None, None, 0
    out = []
    while True:
        rem = deadline - loop.time()
        if rem <= 0:
            return out
        try:
            ctl, ttl, seq, src, dst, tr = await asyncio.wait_for(
                proxy._rx.get(), timeout=rem)
        except asyncio.TimeoutError:
            return out
        if ctl or src != lamp:
            continue
        if not (tr[0] & 0x80):                       # unsegmented
            r = _try_decrypt(tr[1:], seq, src, dst, iv, 0, keys)
            out.append(("unseg", r))
            continue
        hdr = int.from_bytes(tr[1:4], "big")
        szmic = (hdr >> 23) & 1
        seq_zero = (hdr >> 10) & 0x1FFF
        seg_o, seg_n = (hdr >> 5) & 0x1F, hdr & 0x1F
        segs[seg_o] = tr[4:]
        seq_auth, msrc, mdst = _seq_auth(seq, seq_zero), src, dst
        if len(segs) == seg_n + 1:
            cipher = b"".join(segs[i] for i in range(seg_n + 1))
            r = _try_decrypt(cipher, seq_auth, msrc, mdst, iv, szmic, keys)
            out.append(("seg", r))
            segs, seg_n = {}, None


def fmt(results):
    if not results:
        return "    (no response)"
    lines = []
    for kind, r in results:
        if r is None:
            lines.append(f"    [{kind}] <not decodable>")
        else:
            kname, (opcode, params) = r
            lines.append(f"    [{kind}/{kname}] opcode=0x{opcode:x} "
                         f"params={params.hex()}")
    return "\n".join(lines)


async def do_bind(proxy, cfg, dev_key, lamp, iv, keys):
    for model in VENDOR_MODELS:
        params = (lamp.to_bytes(2, "little") + (0).to_bytes(2, "little")
                  + COMPANY.to_bytes(2, "little") + model.to_bytes(2, "little"))
        print(f"BIND app_key -> Vendor 0x{COMPANY:04x}/0x{model:04x} ...")
        for _ in range(3):
            await proxy.send_access(cfg, dev_key, False, lamp,
                                    OP_MODEL_APP_BIND, params)
            res = await listen(proxy, lamp, iv, keys, 3.0)
            hit = [r for _, r in res if r and r[1][0] == OP_MODEL_APP_STATUS]
            if hit:
                st = hit[0][1][1]
                print(f"  Status: {st.hex()}  ({'OK' if st and st[0] == 0 else 'ERROR'})")
                break
        else:
            print("  no bind confirmation")


async def send_vendor(proxy, cfg, app_key, lamp, iv, keys, op_byte, params,
                      label=""):
    print(f">>> {label} op=0x{op_byte:02x} params={params.hex() or '-'}")
    await proxy.send_access(cfg, app_key, True, lamp, vendor_op(op_byte), params)
    print(fmt(await listen(proxy, lamp, iv, keys, 2.5)))


async def onoff(proxy, cfg, app_key, lamp, on: bool):
    wire = 0x00 if on else 0x01                      # firmware quirk: inverted
    cfg["tid"] = (cfg["tid"] + 1) & 0xFF
    await proxy.send_access(cfg, app_key, True, lamp, 0x8202,
                            bytes([wire, cfg["tid"]]))


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app_key = bytes.fromhex(cfg["app_key"])
    dev_key = bytes.fromhex(cfg["dev_key"])
    iv, lamp = cfg["iv_index"], cfg["unicast"]
    keys = (("app", app_key, 0x01), ("dev", dev_key, 0x02))

    async with MeshProxy(cfg["mac"], ctx, cfg["src"], log=print) as proxy:
        if mode in ("bind", "probe"):
            await do_bind(proxy, cfg, dev_key, lamp, iv, keys)

        if mode == "send":
            op_byte = int(sys.argv[2], 16)
            params = bytes.fromhex(sys.argv[3]) if len(sys.argv) > 3 else b""
            await send_vendor(proxy, cfg, app_key, lamp, iv, keys, op_byte,
                              params, "SEND")

        elif mode == "probe":
            print("\n--- GET battery (changes nothing, looks for responses) ---")
            battery = [
                (0xD0, b"", "ATTR_GET all"),
                (0xD0, bytes.fromhex("0000"), "ATTR_GET attr0"),
                (0xD0, bytes.fromhex("0100"), "ATTR_GET attr1"),
                (0xC1, b"", "GROUP_G_GET"),
                (0xC1, bytes.fromhex("ffff"), "GROUP_G_GET ffff"),
                (0xE1, b"", "USER_DEMO/LPN_GET"),
            ]
            for op_byte, params, label in battery:
                await send_vendor(proxy, cfg, app_key, lamp, iv, keys,
                                  op_byte, params, label)
                await asyncio.sleep(0.5)
            print("\nDone. Any non-empty responses above show the vendor "
                  "response format -> from that we build the brightness SET.")

        elif mode == "keys":
            print("Lamp ON (for visible brightness) ...")
            await onoff(proxy, cfg, app_key, lamp, True)
            await asyncio.sleep(2)
            seq = [
                (0xC0, bytes.fromhex("01"), "RC_KEY 01"),
                (0xC0, bytes.fromhex("02"), "RC_KEY 02"),
                (0xC0, bytes.fromhex("03"), "RC_KEY 03"),
                (0xC0, bytes.fromhex("04"), "RC_KEY 04"),
                (0xD2, bytes.fromhex("0000ff"), "ATTR_SET attr0=0xff"),
                (0xD2, bytes.fromhex("010064"), "ATTR_SET attr1=0x64"),
            ]
            print(f"=== {len(seq)} commands at ~5s intervals - WATCH THE LAMP ===")
            for i, (op, p, label) in enumerate(seq, 1):
                print(f"\n[{i}/{len(seq)}]  (look now)")
                await send_vendor(proxy, cfg, app_key, lamp, iv, keys, op, p, label)
                await asyncio.sleep(3)
            print("\nWhich number changed the lamp? (brighter/darker/"
                  "color/off/flicker)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

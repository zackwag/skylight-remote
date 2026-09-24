#!/usr/bin/env python3
"""
Test the factory NetKey against the remote's known Network ID.

A mesh node advertises its Network ID = k3(NetKey). k3 is one-way (not
reversible), but we can check EVERY candidate: k3(candidate) == target? If a
default matches -> factory NetKey found (without hardware, without ciphertext).

    python3 netid_crack.py <netid_hex>

The 8-byte Network ID is read from the target node's 0x1828 advertising service
data (byte 0 = 0x00 = Network ID type, then the 8 bytes). See dump_lamp_adv.py.
"""

# --- Path bootstrap: this tool lives in research/, while the stack + the
# config (skylight-mesh.json) live in the repo root one level up. ---
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)
_CFG = _os.path.join(_ROOT, "skylight-mesh.json")

import sys

from meshlib import crypto
from bruteforce_netkey import BUILTIN_CANDIDATES


def ascii_key(s: str) -> str:
    return s.encode()[:16].ljust(16, b"\x00").hex()


# broad default/guess list (16-byte hex)
EXTRA = [ascii_key(s) for s in (
    "telink_mesh1", "TelinkMeshAll", "telink", "123", "12345678",
    "telink_ble_mesh", "TelinkSigMesh", "BK_MESH_light", "BK_MESH",
    "Skylight", "skylight", "philips", "Philips", "signify", "Signify",
    "PhilipsSkylight", "hue", "Hue", "admin", "password", "casaAdmin",
    "1234567890123456", "0000000000000000",
)] + [
    "00000000000000000000000000000000",
    "ffffffffffffffffffffffffffffffff",
    "0102030405060708090a0b0c0d0e0f10",
    "000102030405060708090a0b0c0d0e0f",
    "1234567890abcdef1234567890abcdef",
    "deadbeefdeadbeefdeadbeefdeadbeef",
]


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 netid_crack.py <netid_hex>  (8 bytes)")
        return 2
    target = bytes.fromhex(sys.argv[1])
    cands = list(BUILTIN_CANDIDATES) + [(f"extra{i}", h) for i, h in enumerate(EXTRA)]
    print(f"Target Network ID: {target.hex()}  ({len(cands)} candidates)")
    for name, hexk in cands:
        try:
            key = bytes.fromhex(hexk)
        except ValueError:
            continue
        if len(key) != 16:
            continue
        if crypto.k3(key) == target:
            print(f"\n>>> HIT! Factory NetKey = {hexk}  ({name})")
            return 0
    print("\nNo default matches -> the factory NetKey is (as expected) "
          "random. Only a firmware dump yields it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

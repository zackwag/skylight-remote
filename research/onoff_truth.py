#!/usr/bin/env python3
"""Truth table for Generic OnOff: raw status bytes against the actual physical
state.

Background: skylight.py assumes the firmware inverts OnOff -- specifically
"incl. the status responses". Switching is demonstrably inverted, but measured
at the light sensor, get_power() returns the wrong state. This script separates
the two directions cleanly:

  SET  -> which wire byte actually turns the lamp on?
  GET  -> which wire byte reports the lamp in which state?

It prints the RAW bytes without any interpretation, plus the light sensor from
Home Assistant as an independent physical reference. That makes it possible to
map wire byte <-> brightness unambiguously.

Note: needs the proxy connection exclusively.
    sudo systemctl stop skylight-bridge
    HA_TOKEN=... python3 research/onoff_truth.py
    sudo systemctl start skylight-bridge

Env:
    HA_URL     default http://127.0.0.1:8123
    HA_TOKEN   long-lived token (without it, it runs but with no lux reference)
    LUX_ENTITY default sensor.bad_anwesenheitssensor_light_sensor_light_level
    SETTLE     seconds to wait for the sensor to catch up (default 12)
"""

import asyncio
import json
import os as _os
import sys as _sys
import urllib.request

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)

from meshlib.skylight import (  # noqa: E402
    OP_ONOFF_GET, OP_ONOFF_SET, SkylightClient,
)

HA_URL = _os.environ.get("HA_URL", "http://127.0.0.1:8123")
HA_TOKEN = _os.environ.get("HA_TOKEN", "")
LUX_ENTITY = _os.environ.get(
    "LUX_ENTITY", "sensor.bad_anwesenheitssensor_light_sensor_light_level")
SETTLE = float(_os.environ.get("SETTLE", "12"))


def lux() -> str:
    """Light sensor as an independent physical reference."""
    if not HA_TOKEN:
        return "n/a"
    req = urllib.request.Request(
        f"{HA_URL}/api/states/{LUX_ENTITY}",
        headers={"Authorization": f"Bearer {HA_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.load(r)["state"] + " lx"
    except Exception as e:
        return f"error: {e}"


def show(label: str, params: bytes) -> None:
    extra = ""
    if len(params) >= 3:
        extra = (f"  present=0x{params[0]:02x}"
                 f" target=0x{params[1]:02x} remaining=0x{params[2]:02x}")
    elif params:
        extra = f"  present=0x{params[0]:02x}"
    print(f"  {label:<26} -> [{params.hex(' ')}]{extra}", flush=True)


async def main() -> None:
    async with SkylightClient(log=lambda *_: None) as sky:
        print(f"\n=== Initial state (sensor: {lux()}) ===")
        show("GET", await sky._request(OP_ONOFF_GET, b""))

        for wire in (0x00, 0x01):
            print(f"\n=== SET wire=0x{wire:02x} ===")
            show(f"SET 0x{wire:02x}",
                 await sky._request(OP_ONOFF_SET,
                                    bytes([wire, sky._next_tid()])))
            print(f"  ... waiting {SETTLE:.0f}s for the sensor to catch up",
                  flush=True)
            await asyncio.sleep(SETTLE)
            show("GET after", await sky._request(OP_ONOFF_GET, b""))
            print(f"  PHYSICAL: {lux()}", flush=True)

        sky.save()
        print("\nDone. Seq saved.")


if __name__ == "__main__":
    asyncio.run(main())

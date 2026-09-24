"""Skylight client: encapsulates the lamp's mesh control in one place.

Used by the CLI (skylight.py) and the MQTT bridge (mqtt_bridge.py).

Deliberately lean: on the real lamp, only Generic OnOff has any effect (and that
INVERTED). Lightness/CTL/Level/scenes are acknowledged by the firmware but
ignored - the modes run exclusively over the remote's proprietary Telink vendor
protocol (unreachable). Details: see the README, section "What works (and what
doesn't)".
"""

import os

from . import network
from .proxy import MeshProxy
from .state import load_cfg, save_cfg

CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "skylight-mesh.json")

OP_ONOFF_GET = 0x8201
OP_ONOFF_SET = 0x8202
OP_ONOFF_STATUS = 0x8204

# BLE writes (without response) and mesh messages can get lost. Instead of
# dropping the whole proxy connection on the first miss (and leaving HA/HomeKit
# stuck on a stale "ON"), we resend the request several times with a short
# timeout before giving up.
STATUS_TIMEOUT = 3.0   # seconds per attempt
STATUS_ATTEMPTS = 3    # sends before we give up

# Firmware quirk (measured on the device, truth table: research/onoff_truth.py).
# The two directions do NOT use the same encoding:
#
#   SET   Wire 0x00 turns it ON, 0x01 turns it OFF -- inverted. The status
#         response to a SET merely echoes the sent wire byte back
#         (SET 0x00 -> [00 00 41]), so it is also inverted and says nothing
#         about the actual state.
#   GET   The status response to a GET, on the other hand, reports the real
#         state SPEC-COMPLIANTLY: 0x01 = on, 0x00 = off.
#
# Previously both directions were inverted. Switching worked that way, but
# reading consistently returned the opposite -- lamp off, GET returns 0x00,
# inverted -> HA showed "on".
ONOFF_SET_INVERTED = True

# Transition time (fade). A Generic OnOff Set may carry two OPTIONAL bytes after
# OnOff and TID: Transition Time and Delay. Without them the server uses its own
# Default Transition Time -- and here that is not 0: the lamp acknowledges a SET
# with [present, target, remaining] and reports 0x41 as remaining, i.e.
# "resolution 1 s, 1 step". It ramps the brightness up instead of switching.
# Measured at the bathroom's light sensor (2026-08-09): after the acknowledged
# "on", the illuminance rose from 0 to 65 lx over roughly 3 s.
#
# That is exactly the delay that comes across as "the lamp doesn't respond
# immediately" -- the switching chain before it only needs ~170 ms
# (sensor -> lamp acknowledges).
#
# MEASURED 2026-08-09, research/transition_probe.py: the firmware IGNORES the
# field. With Transition Time 0x00 (immediate) and 0x03 (0.3 s), the
# acknowledgment reports remaining=0x41 unchanged -- the same second as without
# the bytes. So the fade lines up with Lightness/CTL/Level/scenes: acknowledged
# but ineffective. The transition is not reachable via mesh.
#
# Default is therefore "default" = omit the fields, i.e. byte-for-byte identical
# to before. The switch stays in place so the measurement is reproducible and
# the dead end is documented -- not because it has any effect.
TRANSITION_MS = os.environ.get("SKYLIGHT_TRANSITION_MS", "default")


def transition_wire(ms: int) -> int:
    """Milliseconds -> Transition Time byte (Mesh 3.1.3).

    Bits 5-0 step count, bits 7-6 resolution (100 ms / 1 s / 10 s / 10 min).
    0x00 = zero steps = immediate, no transition.
    """
    for res_bits, step_ms in ((0b00, 100), (0b01, 1000), (0b10, 10_000),
                              (0b11, 600_000)):
        steps = round(ms / step_ms)
        if steps <= 62:
            return (res_bits << 6) | steps
    return 0b11 << 6 | 62  # longer than 620 min is not possible


def transition_params() -> bytes:
    """The two optional bytes -- or empty if the lamp should decide."""
    if TRANSITION_MS.strip().lower() in ("default", "none", ""):
        return b""
    # Delay (wait time BEFORE the transition) stays 0: we want to finish
    # earlier, not to start later.
    return bytes([transition_wire(int(TRANSITION_MS)), 0x00])


def onoff_wire(on: bool) -> int:
    """Parameter byte for an OnOff SET."""
    return int(on ^ ONOFF_SET_INVERTED)


def onoff_echo(wire: int) -> bool:
    """Status response to a SET -- echoed wire byte, i.e. inverted."""
    return bool(wire) ^ ONOFF_SET_INVERTED


def onoff_phys(wire: int) -> bool:
    """Status response to a GET -- real state, spec-compliant."""
    return bool(wire)


class SkylightClient:
    """Async context manager for a control session with the lamp."""

    def __init__(self, cfg: dict | None = None, log=lambda *_: None):
        self.cfg = cfg or load_cfg(CONFIG_FILE)
        self.ctx = network.NetContext(bytes.fromhex(self.cfg["net_key"]),
                                      self.cfg["iv_index"])
        self.app_key = bytes.fromhex(self.cfg["app_key"])
        self.dst = self.cfg["unicast"]
        self.log = log
        self._proxy = None
        # Remaining transition time from the last SET acknowledgment (raw).
        self.last_remaining = None

    async def __aenter__(self):
        self._proxy = MeshProxy(self.cfg["mac"], self.ctx, self.cfg["src"],
                                log=self.log)
        await self._proxy.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self._proxy.__aexit__(*exc)
        self.save()

    def save(self):
        save_cfg(CONFIG_FILE, self.cfg)

    def _next_tid(self) -> int:
        self.cfg["tid"] = (self.cfg["tid"] + 1) & 0xFF
        return self.cfg["tid"]

    async def _request(self, opcode: int, params: bytes) -> bytes:
        """Sends an OnOff message and waits robustly for the status.

        If the response fails to arrive, it is resent (up to STATUS_ATTEMPTS).
        Only when none of the attempts yields a response is the error
        propagated - at that point the connection is really gone and the caller
        marks the lamp as offline instead of holding a wrong state. params for
        an OnOff SET contains a TID; we keep it across all attempts so the
        server recognizes retries as a duplicate and doesn't switch twice.
        """
        last_err = None
        for _ in range(STATUS_ATTEMPTS):
            await self._proxy.send_access(
                self.cfg, self.app_key, True, self.dst, opcode, params)
            try:
                return await self._proxy.wait_status(
                    self.app_key, True, OP_ONOFF_STATUS,
                    timeout=STATUS_TIMEOUT)
            except TimeoutError as e:
                last_err = e
        raise last_err

    async def set_power(self, on: bool) -> bool:
        """Turns on/off, waits for the status. -> acknowledged state."""
        params = await self._request(
            OP_ONOFF_SET,
            bytes([onoff_wire(on), self._next_tid()]) + transition_params())
        # Status: present(1) [, target(1), remaining(1)]. During a running
        # transition the target state is authoritative, not the instantaneous
        # value. Note: this is the acknowledgment of the wire byte, not a
        # measurement -- the firmware only echoes back what we sent.
        #
        # remaining, by contrast, is meaningful: it is the remaining time of the
        # transition the lamp actually performs. If it stays at 0x41 despite a
        # set Transition Time, the firmware ignored the field -- meaning the fade
        # can't be turned off via mesh.
        if len(params) >= 3:
            self.last_remaining = params[2]
        return onoff_echo(params[1] if len(params) >= 3 else params[0])

    async def get_power(self) -> bool:
        """Queries the current state. -> True=on."""
        params = await self._request(OP_ONOFF_GET, b"")
        return onoff_phys(params[0])

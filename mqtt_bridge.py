#!/usr/bin/env python3
"""MQTT bridge: Home Assistant <-> Skylight (BLE mesh).

Holds a permanent proxy connection to the lamp, announces itself to HA via
MQTT discovery as a switchable light, and translates HA commands (on/off)
into mesh messages. State is reported back.

From the outside the Skylight can only do on/off (see README) - so we
deliberately expose it as a pure on/off light, so HA doesn't show anything
useless.

    MQTT_HOST (default 127.0.0.1), MQTT_USER (default skylight),
    MQTT_PASS (default: from ~/apps/mosquitto/mqtt-credentials.txt)
"""

import asyncio
import json
import os
import time

import paho.mqtt.client as mqtt

from meshlib.skylight import SkylightClient
from meshlib.state import load_cfg, save_cfg
from meshlib.skylight import CONFIG_FILE

TOPIC_SET = "skylight/set"
TOPIC_STATE = "skylight/state"
TOPIC_AVAIL = "skylight/availability"
DISCOVERY_TOPIC = "homeassistant/light/skylight/config"

# Purely event-driven by default (0 = no polling): state is saved after every
# command and once on every (re)connect. This covers command changes and power
# loss (the lamp comes back ON) without a constant BLE load. Only if you want to
# see a switch-off via the original remote promptly in HA do you set
# POLL_INTERVAL (seconds) > 0.
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "0"))
RECONNECT_DELAY = 5


def load_mqtt_pass() -> str:
    if os.environ.get("MQTT_PASS"):
        return os.environ["MQTT_PASS"]
    path = os.path.expanduser("~/apps/mosquitto/mqtt-credentials.txt")
    with open(path) as f:
        return f.read().strip()


class Bridge:
    def __init__(self):
        self.cfg = load_cfg(CONFIG_FILE)
        self.loop = None
        self.cmd_queue: asyncio.Queue = asyncio.Queue()
        # The command currently in flight. `cmd_queue.get()` REMOVES it from
        # the queue; if set_power() then fails on the dropped BLE link, it used
        # to be lost - the command vanished silently while HA had long since
        # reported 200. Here it stays until it demonstrably went through, and is
        # re-sent after the reconnect.
        self.pending = None
        # Track WANT and ACTUAL separately. Previously there was only one state:
        # the last one reported. If a switch command is lost on the radio link,
        # bridge and HA then believe the same wrong thing, and nobody notices -
        # the light stays off even though everything says 'on'. With the desired
        # value alongside, the discrepancy shows up on the next re-measurement
        # and is straightened out on its own.
        self.want = None
        self.state = "OFF"

        self.mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                              client_id="skylight-bridge")
        self.mq.username_pw_set(os.environ.get("MQTT_USER", "skylight"),
                                load_mqtt_pass())
        self.mq.will_set(TOPIC_AVAIL, "offline", retain=True)
        self.mq.on_connect = self._on_connect
        self.mq.on_message = self._on_message

    # ---------- MQTT (paho thread) ----------

    def _on_connect(self, client, userdata, flags, reason, props):
        print(f"MQTT connected ({reason})", flush=True)
        client.subscribe(TOPIC_SET)
        client.publish(DISCOVERY_TOPIC, json.dumps({
            "name": "Skylight",
            "unique_id": "skylight_" + self.cfg["mac"].replace(":", "").lower(),
            "schema": "json",
            "command_topic": TOPIC_SET,
            "state_topic": TOPIC_STATE,
            "availability_topic": TOPIC_AVAIL,
            "device": {
                "identifiers": ["skylight"],
                "name": "Philips Skylight",
                "manufacturer": "Signify",
                "model": "Skylight (Telink, Bluetooth SIG Mesh)",
            },
        }), retain=True)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload)
        except ValueError:
            print(f"Invalid JSON: {msg.payload!r}", flush=True)
            return
        if "state" in payload:
            asyncio.run_coroutine_threadsafe(
                self.cmd_queue.put(payload["state"] == "ON"), self.loop)

    def _publish_state(self):
        self.mq.publish(TOPIC_STATE, json.dumps({"state": self.state}),
                        retain=True)

    # ---------- Main loop ----------

    async def run(self):
        self.loop = asyncio.get_running_loop()
        self.mq.connect(os.environ.get("MQTT_HOST", "127.0.0.1"), 1883, 60)
        self.mq.loop_start()

        while True:
            try:
                async with SkylightClient(self.cfg, log=lambda *_: None) as sky:
                    print("Mesh proxy connected", flush=True)
                    self.mq.publish(TOPIC_AVAIL, "online", retain=True)
                    # One read per (re)connect: fully sufficient in event-driven
                    # mode. After a power loss of the lamp the connection drops;
                    # on reconnect we read the actual state (then ON, the physical
                    # default) - without constant polling.
                    self.state = "ON" if await sky.get_power() else "OFF"
                    self._publish_state()
                    sky.save()
                    last_poll = time.monotonic()

                    # First catch up on a command lost during a disconnect.
                    if self.pending is not None:
                        print(f"re-sending command: {self.pending}", flush=True)
                        on = await sky.set_power(self.pending)
                        self.want = "ON" if self.pending else "OFF"
                        self.pending = None
                        self.state = "ON" if on else "OFF"
                        self._publish_state()
                        sky.save()
                        last_poll = time.monotonic()

                    while True:
                        # POLL_INTERVAL=0 -> purely event-driven: we wait
                        # indefinitely for the next command (no polling).
                        timeout = None
                        if POLL_INTERVAL > 0:
                            timeout = max(1.0, POLL_INTERVAL
                                          - (time.monotonic() - last_poll))
                        try:
                            want_on = await asyncio.wait_for(
                                self.cmd_queue.get(), timeout=timeout)
                            # Mark as done only after success.
                            self.pending = want_on
                            on = await sky.set_power(want_on)
                            self.pending = None
                            self.want = "ON" if want_on else "OFF"
                            self.state = "ON" if on else "OFF"
                            self._publish_state()
                            # Reset the poll window after a command too.
                            # Otherwise the next poll is due immediately
                            # (last_poll would be ancient -> timeout 1s) and
                            # reads the lamp MID dimming transition; that
                            # intermediate value would then overwrite the
                            # just-correctly-reported state.
                            last_poll = time.monotonic()
                        except asyncio.TimeoutError:
                            # Re-measure - and if it deviates from the desired
                            # value, repeat the command. This is the real
                            # safeguard: a lost switch command corrects itself
                            # this way, at the latest after one poll interval.
                            on = await sky.get_power()
                            actual = "ON" if on else "OFF"
                            if self.want is not None and actual != self.want:
                                print(f"Discrepancy: want={self.want} actual={actual}"
                                      " - re-sending", flush=True)
                                on = await sky.set_power(self.want == "ON")
                                actual = "ON" if on else "OFF"
                            self.state = actual
                            self._publish_state()
                            last_poll = time.monotonic()
                        # Persist the seq number immediately: after a hard
                        # power loss the file must never lag more than
                        # SEQ_SAFETY_JUMP behind the lamp, otherwise its
                        # replay protection discards everything.
                        sky.save()
            except Exception as e:
                print(f"Mesh connection lost: {e!r} - reconnect in "
                      f"{RECONNECT_DELAY}s", flush=True)
                self.mq.publish(TOPIC_AVAIL, "offline", retain=True)
                save_cfg(CONFIG_FILE, self.cfg)
                await asyncio.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    asyncio.run(Bridge().run())

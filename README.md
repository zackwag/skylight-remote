# skylight-remote

Make the **Philips Skylight** LED ceiling light controllable via **Home
Assistant** — even though it ships with **no** smart-home interface (no Wi-Fi, no
official Zigbee/Matter, just the bundled remote control).

The lamp is a **Bluetooth SIG Mesh** node (2.4 GHz, Telink chip). This project
implements its own lightweight mesh stack in Python, takes the lamp into the
network (PB-GATT provisioning), connects as a **proxy**, and exposes it to Home
Assistant over **MQTT**.

**Status:** ✅ working. On/off is reliably controllable — both as a CLI *and* as
an MQTT bridge (systemd service on a Raspberry Pi 4). See [What works (and what
doesn't)](#what-works-and-what-doesnt).

---

## The device

| | |
|---|---|
| Product | Philips Skylight (Signify) |
| Radio | **Bluetooth LE / SIG Mesh, 2.4 GHz** |
| BLE name | `BK_MESH_light` |
| Chip | Telink |
| Provisioning | Mesh Provisioning Service `0x1827` |
| Proxy | Mesh Proxy Service `0x1828` |

The Composition Data reports standard SIG models (`Generic OnOff`,
`Light Lightness`, `Light CTL`, `Light HSL`, `Scene`, `Scheduler`, …) — but only
some of them actually do anything on the real lamp.

## What works (and what doesn't)

- ✅ **Generic OnOff** — on/off, reliable.
  **Firmware quirk:** the two directions use **different encodings** — measured
  on the device with
  [`research/onoff_truth.py`](research/onoff_truth.py):

  | Direction | Encoding |
  |---|---|
  | **SET** | inverted: wire `0x00` turns it **ON**, `0x01` turns it **OFF** |
  | Status in response to a SET | merely echoes the sent wire byte back (so also inverted) — **not** a measurement of the real state |
  | **GET** | spec-compliant: `0x01` = on, `0x00` = off |

  The code wraps this in `onoff_wire` / `onoff_echo` / `onoff_phys`; from the
  outside everything is normal. Anyone treating both directions the same way
  gets exactly the opposite on every read.
- ❌ **Lightness / CTL / HSL / Level / scenes / modes** — the firmware
  *acknowledges* these messages with a correct status, **but does not drive the
  LED with them**. Of the 22 SIG models, **only `Generic OnOff` is actually
  wired up**; all the others are a **shadow state**. Brightness, white tone,
  color, and the 6 modes (5 presets + "Day Rhythm") run exclusively over a
  **Telink vendor model** (`Company 0x0211`, Model `0x0000`), whose opcode +
  payload we could only obtain from a **firmware dump**. That's why we
  deliberately expose the lamp as a **pure on/off light**. Details + the ruled-
  out paths: see [The journey](#the-journey-so-future-me-knows-the-dead-ends).

## Architecture

```
Home Assistant ──MQTT──> skylight-remote (Raspberry Pi 4)
                              │  own SIG mesh stack (meshlib/)
                              ▼  BLE (Mesh Proxy 0x1828, via bleak)
                        BK_MESH_light (Telink, SIG Mesh)
```

The Pi holds the mesh keys (NetKey/AppKey/DevKey + the lamp's unicast address in
`skylight-mesh.json`), connects as a proxy, and maps MQTT commands onto mesh
messages. Why the Pi and not a separate gateway: Home Assistant already runs
there, and BLE is onboard.

---

## Usage

### CLI

```bash
python3 skylight.py on         # turn on
python3 skylight.py off        # turn off
python3 skylight.py toggle     # toggle
python3 skylight.py status     # query current state
python3 skylight.py scan       # BLE visibility + RSSI of the lamp
python3 skylight.py provision  # freshly take it into the mesh
```

### MQTT bridge

`mqtt_bridge.py` holds a permanent proxy connection and automatically announces
the lamp via **HA MQTT discovery** as `light.skylight` (JSON schema, on/off).

| Topic | Payload | |
|---|---|---|
| `skylight/set` | `{"state": "ON"｜"OFF"}` | command from HA |
| `skylight/state` | `{"state": "ON"｜"OFF"}` | state (retained) |
| `skylight/availability` | `online` / `offline` | LWT (retained) |

Configuration via env variables: `MQTT_HOST` (default `127.0.0.1`),
`MQTT_USER` (default `skylight`), `MQTT_PASS` (default from
`~/apps/mosquitto/mqtt-credentials.txt`), `POLL_INTERVAL` (default `0`).

**State logic — purely event-driven:** the state is saved after every command
and additionally read once on every (re)connect. This covers two cases without
continuous polling: switching via HA/HomeKit (trigger) and a **power loss of the
lamp** — in that case the proxy connection drops, and on reconnect the bridge
reads the actual state, which is then `ON` (the lamp's physical default). If no
response comes at all, the lamp is considered offline (HA "unavailable", HomeKit
"Not Responding") instead of hanging on a stale `ON`; reads are retried several
times beforehand on packet loss.

Only turning off via the **original remote control** (no command, no power loss)
is not detected in event-driven mode. If you want to see that promptly in HA,
set `POLL_INTERVAL` (seconds) > 0.

---

## Deployment (Raspberry Pi 4)

The service runs on the Raspberry Pi (host set via `$PI_HOST`, e.g. the Pi's
hostname/IP on the LAN) under `/home/pi/apps/skylight-remote` as the systemd unit
`skylight-bridge.service`.

```bash
# one-time
sudo apt install -y python3-pip bluez
pip3 install bleak paho-mqtt
git clone https://github.com/l-carta/skylight-remote.git ~/apps/skylight-remote
sudo cp ~/apps/skylight-remote/skylight-bridge.service /etc/systemd/system/
sudo systemctl enable --now skylight-bridge

# update
cd ~/apps/skylight-remote && git pull && sudo systemctl restart skylight-bridge
```

`skylight-mesh.json` (keys + counters) is **gitignored** and stays untouched on
every pull.

---

## Project structure

| File | Purpose |
|---|---|
| `skylight.py` | CLI — on/off/toggle/status/scan/provision |
| `mqtt_bridge.py` | MQTT ↔ mesh, HA discovery, systemd service |
| `provision.py` | freshly take the lamp into the mesh |
| `scan.py` | BLE scan, finds the lamp |
| `meshlib/crypto.py` | mesh crypto primitives (s1/k2, AES-CMAC/CCM) |
| `meshlib/network.py` | network/transport PDU encoding, segmentation |
| `meshlib/proxy.py` | mesh proxy connection over BLE (bleak) |
| `meshlib/provisioner.py` | PB-GATT provisioning |
| `meshlib/skylight.py` | `SkylightClient` — high-level control |
| `meshlib/state.py` | loading/saving `skylight-mesh.json` |
| `skylight-bridge.service` | systemd unit |
| `test_crypto.py` | crypto tests (Bluetooth Mesh spec test vectors) |

---

## Operation & pitfalls

**Replay protection:** the lamp **silently** discards mesh messages with an
already-seen sequence number. After a hard power loss the stored `seq` can lag
behind the lamp's state → the lamp stops responding. Countermeasures in the code:

- `mqtt_bridge.py` persists the `seq` after **every poll** (not just on
  disconnect).
- `state.py` additionally jumps forward by `SEQ_SAFETY_JUMP = 512` on load.

If it happens anyway: set `seq` in `skylight-mesh.json` far forward (the 24-bit
space goes up to 16.7 million) and restart the service.

## Security

`skylight-mesh.json` contains the lamp's NetKey/AppKey/DevKey → keep it secret,
**never commit it** (it's in `.gitignore`). Anyone who re-provisions the lamp
into their own network can temporarily deprive the original remote of control; a
factory reset of the lamp restores it.

---

## The journey (so "future me" knows the dead ends)

**Dead end: 433 MHz ❌** — first assumption: the remote transmits on 433.92 MHz
OOK, so we capture the signal with a CC1101 and replay it. Setup with an Arduino
MKR + CC1101. Lesson learned: the first module was 868 MHz (deaf on the 433
band), the second had a wobbly SMA contact — and the real reason a clean signal
never showed up: the "spikes" were **stray RF**. The Skylight doesn't transmit on
433 at all. **Takeaway:** verify the radio technology first, then buy hardware —
a BLE scan at the start would have saved hours.

**Breakthrough: it's Bluetooth ✅** — a BLE scan immediately shows
`BK_MESH_light` with the mesh provisioning service `0x1827`. First provisioned
with the **nRF Mesh** app and confirmed on/off, then rebuilt the mesh stack
itself in Python (`meshlib/`) — including our own PB-GATT provisioning, so we no
longer need any external tool.

**The hunt for brightness/modes ❌ (exhaustive, everything ruled out)** — on/off
works, but brightness/color/modes don't. Worked through systematically:

- **Decoded the Composition Data** (`read_composition.py`) → in the process
  found a real **bug in the stack**: `_app_nonce` wasn't setting the **ASZMIC
  bit**, so segmented messages (SZMIC=1) couldn't be decrypted (now fixed).
  Result: the node is `CID 0x0211` (Telink) with **2 vendor models**
  (`0x0211/0x0000`, `/0x0001`) alongside the SIG models.
- **Bound + tested all SIG models** (`model_probe.py`, `scene_probe.py`):
  Level, Lightness, CTL, CTL temperature, HSL, Hue, Saturation, **8 stored
  scenes via Scene Recall** — all respond with a status, **none moves the LED**.
- **Fuzzed all 64 vendor opcodes** `0xC0–0xFF` (`vendor_sweep.py`,
  `final_probe.py`), incl. `VD_RC_KEY_REPORT` (0xC0) with key codes and
  structured payloads → nothing.
- **On/off path** in all variants (byte as mode selector, repeated press,
  transition bytes) → nothing. **`0xFDA0` service** read *and* written → nothing.
- **Transition time (fade)** (`transition_probe.py`, 2026-08-09) → nothing. The
  lamp doesn't switch, it ramps up; that costs the noticeable wait, whereas the
  switching chain before it only needs ~170 ms. It *demonstrably knows*
  transitions — the SET acknowledgment `[present, target, remaining]` reports
  `remaining=0x41`, i.e. 1 s. But the optional Transition Time and Delay fields
  in the Generic OnOff Set change nothing: with `0x00` (immediate) and `0x03`
  (0.3 s), `remaining` stays at `0x41`. Acknowledged, ignored — just like
  Lightness/CTL/Level. **The fade cannot be turned off via mesh.**
- **Sniffing the remote** (`sniff_mesh.py`): it doesn't broadcast — it's a
  **proxy client on the factory network** (different Network ID) and connects
  via GATT. **Impersonation** (`imp_lamp.py`/`imp_capture.sh`, Pi as a fake lamp
  with a spoofed MAC): technically possible, but the remote sends only
  **factory-key-encrypted** bytes → unreadable. **Guessing the factory NetKey**
  against the known Network ID (`netid_crack.py`, `k3`) → no default matches, the
  key is random.
- **Online research**: no community RE (product is new, June 2026), no FCC docs.

**Conclusion:** the firmware drives brightness/color/modes **exclusively** via
the Telink vendor model, whose opcode+payload lives only in the chip. On the
software side, everything is exhausted. The only remaining path: a **firmware
dump of the lamp** (Telink TLSR, SWS debug interface) → yields the vendor opcode,
the payload format, *and* the keys in cleartext; after that, control would go
through the existing stack.

**Addendum: SDK identified + both documented vendor paths cleanly ruled out.** A
second, more thorough attempt raised the conclusion from "probably" to
**rigorously proven**:

- **SDK attribution.** The lamp is based on the Telink SIG Mesh SDK
  ([`Ai-Thinker-Open/Telink_SIG_Mesh`](https://github.com/Ai-Thinker-Open/Telink_SIG_Mesh),
  example `RGBCW_Ali_Mesh`, `mesh/vendor_model.{c,h}`). The lamp's two vendor
  models are `VENDOR_MD_LIGHT_S = 0x0000` and `VENDOR_MD_LIGHT_C = 0x0001` there
  — an exact match. So we know the **real** opcode/payload formats:
  - **Attribute mode** (`VENDOR_OP_MODE_SPIRIT`): `0xD0` GET · `0xD1` SET ·
    `0xD3` STATUS, payload `[tid][attr_type 2B LE][value]`. IDs include
    `ATTR_ONOFF=0x0100`, `ATTR_TARGET_TEMP=0x010c`, `ATTR_SCENE_MODE=0xf004`.
  - **Default mode** (`VENDOR_OP_MODE_DEFAULT`): `VD_RC_KEY_REPORT=0xC0`,
    payload `[code][00×7]` (8 bytes, from `vd_cmd_key_report`); response
    `STATUS_NONE` (never an acknowledgment, only the LED shows an effect).
- **Served both paths correctly → both dead** (`vendor_attr2.py`,
  `vendor_rc_sweep.py`): `ATTR_GET` across all candidate IDs with the *correct*
  structure → **no `0xD3` response** (attribute mode not active). All **256** key
  codes `0x00–0xFF` with the *correct* 8-byte payload → **no LED reaction**. (The
  earlier round had omitted the `tid` or sent only 1–2 bytes — the payloads were
  malformed, which did *not* fully explain the radio silence; now confirmed with
  the SDK structure: the path is genuinely dead.)
- **Passive full capture** (`mesh_monitor.py`, unfiltered, during on/off
  toggling): the lamp emits **only `Generic OnOff Status` (0x8204)** — no vendor
  publication, no heartbeat, no undecodable traffic. So there's nothing to
  passively grab either.
- **Composition freshly read:** **exactly one element** (element 0), 22 SIG + 2
  vendor models — no hidden second element we might have been transmitting past.
- **Remote not re-provisionable** (`scan_all.py`): a broad scan shows **no**
  `0x1827` advertisement, not even on a button press → the factory remote can't
  be taken into our network to capture its mode commands decodably.

**Sharpened conclusion:** the Telink scaffold is there, but **Philips replaced
the vendor command table with its own proprietary opcodes/payloads** — neither
the attribute nor the key-report path of the standard SDK has any effect. What
drives the modes on the wire lives only in the chip. The firmware dump remains
the only ground-truth path; deliberately **not** pursued (no physical access to
the lamp, read-protection risk).

**Addendum 2: The remote as an attack surface (provisionee path) —
exhaustively worked through, failed on the Pi hardware.** Idea: if the remote
provisions *us*, we get the **factory NetKey** in cleartext (ECDH) as the
provisionee, and with it we could decode/replay its mode commands.

- **The remote *is* a provisioner.** After a mesh `Config Node Reset`
  ([`node_reset.py`](research/node_reset.py)) the lamp becomes unprovisioned and
  is **immediately** taken back into the factory network by the remote. (The
  earlier "not re-provisionable" only applied to our *fake*, not to the
  mechanism.) Lamp↔network is an **either-or**: our network *xor* the factory
  network — both at once is impossible.
- **The remote is a TI CC2640** (manufacturer *Shenzhen Jingxun*, ARM) and
  exposes the **TI OAD firmware-update service** (`f000ffc0`/`ffc1`/`ffc2`,
  [`remote_probe.py`](research/remote_probe.py), [`remote_ffc0.py`](research/remote_ffc0.py)).
  A read on `ffc1` once leaked **ARM machine code** from an uninitialized OAD
  buffer (proof: firmware lives there) — but only a **fixed** value is
  reproducible, not a memory window. OAD is a *write* channel (signed images) →
  **no** free firmware read, and writing risks a brick. No viable no-solder dump.
- **Full provisionee attack** ([`imp_prov.py`](research/imp_prov.py),
  [`imp_capture_prov.sh`](research/imp_capture_prov.sh)): the Pi mimics the
  reset lamp **byte-for-byte** — spoofed MAC + device UUID from the Telink
  pattern (`<prefix><reversed MAC><suffix>`, derived from the adapter MAC) +
  correct `0x1827` advertising via `btmgmt`. Result: the remote **does not
  initiate provisioning** toward us — under *no* bearer:
  - **PB-GATT** cleanly ruled out (connectable, byte-verified adv, the remote
    still doesn't connect).
  - **PB-ADV** ([`pbadv_probe.sh`](research/pbadv_probe.sh), raw HCI, because
    BlueZ otherwise blocks the custom mesh AD types `0x2B`/`0x29`): the remote
    broadcasts only its proxy + Secure Network Beacon, **no** PB-ADV response.
    PB-ADV requires *simultaneous* tight transmitting **and** receiving at µs
    cadence — which the Broadcom radio in the Pi + BlueZ doesn't reliably
    deliver.
- **Proxy eavesdropping** ([`remote_listen.py`](research/remote_listen.py)): we
  *can* connect to the remote (proxy server) and subscribe to `2ade` — but
  without the factory NetKey no proxy filter can be set, so only **Secure
  Network Beacons** (factory Network ID) come in, no mode traffic.

**Open question:** whether it's really the *remote* that provisions — or a
**hub/gateway** (CG-KIT) in the setup. Unverified.

**Final conclusion:** on the Pi, the provisionee path is exhausted (PB-GATT ruled
out, PB-ADV not feasible on the hardware). Making progress would require either
the **firmware dump** (SWS) or a **dedicated nRF52840** (real PB-ADV/sniffing).
The modes remain in proprietary, signed firmware + a random factory key — not
solvable in software from the Pi.

### Diagnostic/research tools

The entire analysis toolkit from this hunt lives in
**[`research/`](research/)** (composition reader, model/vendor probers, GATT
enumeration, adv sniffer, NetKey tests, fake lamp). Device IDs come from
config/adapter at runtime, nothing is hardcoded. Not needed for normal
operation — details + how to run: [`research/README.md`](research/README.md).

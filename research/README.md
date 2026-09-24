# research/ — diagnostic & reverse-engineering tools

These scripts came out of the (unsuccessful) hunt for **brightness/color/mode
control** of the Skylight. They are **not needed for normal operation** — on/off
runs through the core code in the repo root (`skylight.py`, `mqtt_bridge.py`).
This is the toolkit for analysis and a later firmware-dump attempt. Background &
conclusion: see [main README, "The journey"](../README.md).

## Running

**Always start from the repo root** (not from `research/`), so the config is
found:

```bash
cd ~/apps/skylight-remote
python3 research/read_composition.py
```

A path bootstrap at the top of every tool appends the repo root to `sys.path`,
so that `from meshlib import …` and `skylight-mesh.json` are found cleanly.

**Important:** most mesh tools need the **proxy connection exclusively** — stop
the bridge first:

```bash
sudo systemctl stop skylight-bridge
python3 research/<tool>.py
sudo systemctl start skylight-bridge
```

## Overview

| Tool | Purpose |
|---|---|
| `read_composition.py` | read the Composition Data (with segment reassembly) |
| `model_probe.py` | bind all SIG models + test SET commands |
| `scene_probe.py` | Scene model: list stored scenes + recall |
| `vendor_probe.py` | bind the vendor model + send/decode opcodes |
| `vendor_sweep.py` | sweep the vendor opcode range with contrasting payloads |
| `vendor_attr2.py` | attribute probe with the **correct** SDK structure `[tid][attr 2B LE][value]`, looks for `ATTR_STATUS 0xD3` |
| `vendor_rc_sweep.py` | emulate `VD_RC_KEY_REPORT 0xC0` correctly (8-byte payload), code sweep 0x00–0xFF |
| `final_probe.py` | "loose ends": power level, RC key sweep, 0xFDA0 writes |
| `onoff_modes.py` | test on/off path variants as a mode selector |
| `gatt_enum.py` | list the lamp's GATT services/characteristics |
| `fda0_probe.py` | read the custom 0xFDA0 service |
| `mesh_monitor.py` | passive, **unfiltered** mesh capture (every element address + control), optionally with an on/off toggle to provoke it |
| `scan_all.py` | broad BLE scan — finds the remote / `0x1827` advertisements (pairable devices) |
| `dump_lamp_adv.py` | capture the lamp's actual advertising |
| `sniff_mesh.py` | passively capture mesh adv (btmon → BTSnoop parse) |
| `decode_capture.py` | fully decrypt a capture with NetKey+AppKey |
| `bruteforce_netkey.py` | test NetKey candidates against a network PDU |
| `netid_crack.py` | check default NetKeys against a known Network ID |
| `imp_lamp.py` | Pi as a fake lamp (bless GATT server) |
| `imp_capture.sh` / `imp_capture2.sh` | fake lamp + MAC spoof + cleanup orchestration |
| `node_reset.py` | `Config Node Reset 0x8049` — take the lamp out of our network (it becomes pairable again) |
| `remote_probe.py` | connect to the remote (proxy server) + read all readable GATT chars/adv |
| `remote_ffc0.py` | inspect the remote's TI OAD service `f000ffc0` (read-only) |
| `remote_listen.py` | subscribe to the remote's proxy (`2ade`) + capture (factory-key-encrypted) |
| `imp_prov.py` | Pi as an **unprovisioned** fake lamp (`0x1827`, btmgmt adv, UUID from adapter MAC) — logs the provisioning `INVITE` |
| `imp_capture_prov.sh` | `imp_prov.py` + MAC spoof to the lamp's MAC + cleanup |
| `pbadv_probe.sh` | send + scan a raw-HCI PB-ADV beacon (`0x2B`) (optional MAC spoof); checks whether the remote provisions over PB-ADV |

Self-tests without hardware:

```bash
python3 research/decode_capture.py --selftest
python3 research/bruteforce_netkey.py --selftest
python3 research/sniff_mesh.py --selftest
```

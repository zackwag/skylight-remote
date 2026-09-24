#!/bin/bash
# Raw-HCI PB-ADV probe: sends the Mesh Unprovisioned Device Beacon (AD 0x2B)
# over raw HCI (bluetoothd stopped -> no permission denied) AND scans
# simultaneously to see whether the remote responds with PB-ADV (Link Open).
#
# All IDs come at RUNTIME: device UUID = <prefix><reversed adapter MAC>
# <suffix>. With the argument "spoof", the adapter MAC is first set to the MAC
# from skylight-mesh.json (the real lamp MUST then be powered off!).
#
#   sudo -v ; ./research/pbadv_probe.sh [duration_s] [spoof]
set -u
DUR=${1:-45}
SPOOF=${2:-}
HCI=hci0

mac_now() { sudo btmgmt info 2>/dev/null | grep -o 'addr [0-9A-F:]*' | head -1 | cut -d' ' -f2; }
PI_MAC=$(mac_now)
LAMP_MAC=$(python3 -c 'import json;print(json.load(open("skylight-mesh.json"))["mac"])' 2>/dev/null || true)

cleanup() {
  echo "# --- Cleanup ---"
  sudo hcitool -i $HCI cmd 0x08 0x000A 00    >/dev/null 2>&1
  sudo hcitool -i $HCI cmd 0x08 0x000C 00 00 >/dev/null 2>&1
  sudo systemctl start bluetooth; sleep 1
  if [ -n "$SPOOF" ] && [ -n "$PI_MAC" ]; then
    sudo btmgmt power off >/dev/null 2>&1
    sudo btmgmt public-addr "$PI_MAC" >/dev/null 2>&1
    sudo btmgmt power on  >/dev/null 2>&1
  fi
  sudo systemctl start skylight-bridge
  echo "# bluetoothd/bridge back on, MAC: $(mac_now)"
}
trap cleanup EXIT

sudo systemctl stop skylight-bridge 2>/dev/null

if [ -n "$SPOOF" ]; then
  [ -n "$LAMP_MAC" ] || { echo "# No lamp MAC in skylight-mesh.json"; exit 1; }
  echo "# Spoofing MAC -> lamp (from config). The real lamp MUST be off!"
  sudo btmgmt power off >/dev/null 2>&1
  sudo btmgmt public-addr "$LAMP_MAC" >/dev/null 2>&1
  sudo btmgmt power on  >/dev/null 2>&1; sleep 1
fi

sudo systemctl stop bluetooth; sleep 1
sudo hciconfig $HCI up
TARGET_MAC=$(sudo hciconfig $HCI | grep -o 'BD Address: [0-9A-F:]*' | cut -d' ' -f3)
echo "# Adapter MAC: $TARGET_MAC"

# Derive the beacon adv data field (length + 31 bytes) for LE Set Advertising Data:
ADV_DATA=$(python3 - "$TARGET_MAC" <<'PY'
import sys
mac = bytes.fromhex(sys.argv[1].replace(":", ""))
uuid = bytes.fromhex("0064b4692d0900") + mac[::-1] + bytes.fromhex("000001")
sig = bytes.fromhex("142b00") + uuid + bytes.fromhex("0000")   # mesh beacon 0x2B
field = sig + bytes(31 - len(sig))
print(" ".join("%02x" % b for b in (bytes([len(sig)]) + field)))
PY
)

# non-connectable adv + beacon data + scan (passive)
sudo hcitool -i $HCI cmd 0x08 0x0006 A0 00 A0 00 03 00 00 00 00 00 00 00 00 07 00 >/dev/null
sudo hcitool -i $HCI cmd 0x08 0x0008 $ADV_DATA >/dev/null
sudo hcitool -i $HCI cmd 0x08 0x000A 01 >/dev/null
sudo hcitool -i $HCI cmd 0x08 0x000B 00 10 00 10 00 00 00 >/dev/null
sudo hcitool -i $HCI cmd 0x08 0x000C 01 00 >/dev/null
echo "# PB-ADV beacon + scan active."

sudo btmon > /tmp/pbadv.txt 2>/dev/null &
BTM=$!
echo "# === ${DUR}s: NOW repeatedly hold ON for 10s on the remote ==="
sleep "$DUR"
sudo kill $BTM 2>/dev/null; sleep 1

echo "# === btmon lines: $(wc -l < /tmp/pbadv.txt) ==="
echo "# === REAL PB-ADV / provisioning (normal proxy/Secure Beacon filtered out) ==="
grep -iaE "PB-ADV|Provisioning|Unprovisioned|Link (Open|ACK|Close)|Transaction Start" /tmp/pbadv.txt | head -60
echo "# (empty = no PB-ADV response from the remote)"

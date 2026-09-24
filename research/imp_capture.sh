#!/bin/bash
# Fake-lamp capture with guaranteed cleanup.
# Stops the bridge, spoofs the Pi MAC to the lamp MAC, starts the fake GATT
# server + advertising, and at the end ALWAYS resets the MAC + restarts the bridge.
#
#   sudo -v ; ./imp_capture.sh [runtime_s]
set -u
RUNTIME=${1:-60}
cd /home/pi/apps/skylight-remote
# Determine device identities at runtime (hardcode nothing):
LAMP_MAC=$(python3 -c 'import json;print(json.load(open("skylight-mesh.json"))["mac"])')
PI_MAC=$(sudo btmgmt info | grep -o 'addr [0-9A-F:]*' | head -1 | cut -d" " -f2)

cleanup() {
  echo "# --- Cleanup: advertising off, MAC restored, bridge on ---"
  printf 'advertise off\n' | bluetoothctl >/dev/null 2>&1
  sudo btmgmt power off  >/dev/null 2>&1
  sudo btmgmt public-addr "$PI_MAC" >/dev/null 2>&1
  sudo btmgmt power on   >/dev/null 2>&1
  sudo systemctl start skylight-bridge
  echo "# MAC restored: $(sudo btmgmt info | grep -o 'addr [0-9A-F:]*' | head -1)"
}
trap cleanup EXIT

echo "# Stopping bridge ..."
sudo systemctl stop skylight-bridge; sleep 2
printf 'advertise off\n' | bluetoothctl >/dev/null 2>&1   # clear stale adv

echo "# Spoofing MAC -> $LAMP_MAC ..."
sudo btmgmt power off >/dev/null 2>&1
sudo btmgmt public-addr "$LAMP_MAC" >/dev/null 2>&1
sudo btmgmt power on  >/dev/null 2>&1; sleep 1
echo "# MAC now: $(sudo btmgmt info | grep -o 'addr [0-9A-F:]*' | head -1)"

echo "# Starting fake lamp (${RUNTIME}s) ..."
sudo ~/imp-venv/bin/python research/imp_lamp.py "$RUNTIME"

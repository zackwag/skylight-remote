#!/bin/bash
# Provisionee attack with MAC spoof + guaranteed cleanup.
# Spoofs the Pi MAC to the lamp MAC and starts the provisionee logger
# (imp_prov.py, advertises 0x1827). Goal: the remote should recognize the
# "freshly reset lamp" and send a provisioning INVITE.
#
# IMPORTANT: the REAL lamp must be POWERED OFF (otherwise a MAC conflict)!
#
#   sudo -v ; ./research/imp_capture_prov.sh [runtime_s]
set -u
RUNTIME=${1:-90}
cd /home/pi/apps/skylight-remote
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
printf 'advertise off\n' | bluetoothctl >/dev/null 2>&1

echo "# Spoofing MAC -> $LAMP_MAC (the real lamp MUST be powered off!) ..."
sudo btmgmt power off >/dev/null 2>&1
sudo btmgmt public-addr "$LAMP_MAC" >/dev/null 2>&1
sudo btmgmt power on  >/dev/null 2>&1; sleep 1
echo "# MAC now: $(sudo btmgmt info | grep -o 'addr [0-9A-F:]*' | head -1)"

echo "# Starting provisionee logger (${RUNTIME}s) - NOW repeatedly hold ON for 10s ..."
sudo ~/imp-venv/bin/python research/imp_prov.py "$RUNTIME"

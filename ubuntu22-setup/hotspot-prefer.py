#!/usr/bin/env python3
"""Prefer a visible phone hotspot over the lab Wi-Fi; NetworkManager owns DHCP."""

import json
from pathlib import Path
import subprocess
import time


CONFIG = Path('/etc/radxa/hotspot-prefer.json')
STATE = Path('/run/radxa-hotspot-prefer.json')


def nm(*args, timeout=20):
    result = subprocess.run(
        ['nmcli', *args], capture_output=True, text=True, timeout=timeout,
        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'},
    )
    return result.returncode, result.stdout.rstrip('\n')


def main():
    config = json.loads(CONFIG.read_text())
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    code, devices = nm('-t', '-f', 'DEVICE,TYPE', 'device', 'status')
    if code:
        return
    interfaces = [row.rsplit(':', 1)[0] for row in devices.splitlines()
                  if row.rsplit(':', 1)[-1] == 'wifi']
    if not interfaces:
        return
    iface = interfaces[0]
    code, current = nm('-g', 'GENERAL.CON-UUID', 'device', 'show', iface)
    if code or current != config['lab_uuid']:
        STATE.unlink(missing_ok=True)
        return
    if time.monotonic() - state.get('last_attempt', -1000) < 180:
        return
    code, _ = nm('device', 'wifi', 'rescan', 'ifname', iface)
    if code:
        return
    code, visible = nm('-e', 'no', '-g', 'SSID', 'device', 'wifi', 'list',
                       'ifname', iface, '--rescan', 'no')
    if code:
        return
    if config['phone_ssid'] not in visible.splitlines():
        state['seen'] = 0
        STATE.write_text(json.dumps(state))
        return
    state['seen'] = state.get('seen', 0) + 1
    if state['seen'] < 2:
        STATE.write_text(json.dumps(state))
        return
    state.update(seen=0, last_attempt=time.monotonic())
    STATE.write_text(json.dumps(state))
    print('Phone hotspot visible twice; requesting connection.', flush=True)
    try:
        code, _ = nm('--wait', '25', 'connection', 'up', 'uuid', config['phone_uuid'],
                     'ifname', iface, timeout=35)
    except subprocess.TimeoutExpired:
        code = 1
    if code:
        print('Hotspot connection failed; restoring lab Wi-Fi.', flush=True)
        nm('--wait', '25', 'connection', 'up', 'uuid', config['lab_uuid'],
           'ifname', iface, timeout=35)


if __name__ == '__main__':
    try:
        main()
    except subprocess.TimeoutExpired:
        print('NetworkManager operation timed out; retry on next timer tick.', flush=True)

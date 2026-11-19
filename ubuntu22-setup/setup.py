#!/usr/bin/env python3
"""Staged setup for a fresh Ubuntu Rockchip 22.04 Server on ZERO 3W."""

import argparse
import datetime
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import subprocess
import tempfile
import uuid


HERE = Path(__file__).resolve().parent
STATE = Path('/var/lib/radxa-setup')
WIFI = Path('/etc/radxa/wifi-profiles.json')
APT_ENV = dict(os.environ, DEBIAN_FRONTEND='noninteractive', NEEDRESTART_MODE='l')
ROS_SOURCE_FILE = 'ros2-apt-source_1.3.0.jammy_all.deb'
ROS_SOURCE_SHA256 = '110b9a462d55252decb8b7c816f61c2ba0d9890ce5fb93ac504e97cae5860d76'
ROSDEP_DATA = Path('/opt/radxa-rosdep')


def run(args, **kwargs):
    print('+', ' '.join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def user_run(user, args, **kwargs):
    return run(['runuser', '-u', user, '--', *args], **kwargs)


def install(*packages):
    run(['apt-get', '-y', '--no-remove', 'install', '--no-install-recommends', *packages],
        env=APT_ENV)


def backup(path):
    if path.exists():
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        destination = path.with_name(path.name + '.before-radxa-' + stamp)
        shutil.copy2(path, destination)
        return destination


def write(path, contents, mode=0o644, owner=None, keep_backup=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError('Refusing to replace symlink: ' + str(path))
    if keep_backup and path.exists():
        backup(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    if isinstance(contents, bytes):
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(contents)
    else:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(contents)
    path.chmod(mode)
    if owner:
        os.chown(path, owner.pw_uid, owner.pw_gid)


def keyfile_string(value):
    # GLib keyfile string escapes. Escape all spaces to retain boundary spaces.
    return value.replace('\\', '\\\\').replace(' ', '\\s')


def wifi_profile(name, entry, priority, connection_uuid):
    ssid = entry['ssid']
    password = entry['password']
    if not 1 <= len(ssid.encode()) <= 32 or any(ord(ch) < 32 or ord(ch) == 127 for ch in ssid):
        raise ValueError('Invalid Wi-Fi SSID.')
    if not (re.fullmatch(r'[\x20-\x7e]{8,63}', password)
            or re.fullmatch(r'[a-fA-F0-9]{64}', password)):
        raise ValueError('Wi-Fi passwords must be WPA2 passphrases or hexadecimal PSKs.')
    return f'''[connection]
id={name}
uuid={connection_uuid}
type=wifi
autoconnect=true
autoconnect-priority={priority}

[wifi]
mode=infrastructure
ssid={keyfile_string(ssid)}

[wifi-security]
key-mgmt=wpa-psk
psk={keyfile_string(password)}
psk-flags=0

[ipv4]
method=auto

[ipv6]
method=auto
'''


def xrdp_config(original):
    first_session = re.search(r'^\[(?!Globals\]|Logging\]|LoggingPerLogger\]|Channels\])[^\]]+\]\s*$',
                              original, re.MULTILINE)
    if first_session is None:
        raise RuntimeError('Unexpected xrdp.ini format; no xrdp.ini changes made.')
    header = original[:first_session.start()]
    header = re.sub(r'^autorun=.*$', 'autorun=', header, flags=re.MULTILINE)
    return header.rstrip() + '''

[radxa-desktop]
name=Radxa XFCE
lib=libvnc.so
ip=127.0.0.1
port=5901
username=na
password=ask
'''


def ensure_swap():
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    if int(memory['SwapTotal'].split()[0]) >= 1024 * 1024:
        return
    swap = Path('/var/swap-radxa')
    if swap.exists():
        print('Existing /var/swap-radxa retained; verify swap using swapon --show.', flush=True)
        return
    if shutil.disk_usage('/var').free < 5 * 1024**3:
        raise RuntimeError('Need at least 5 GiB free before creating build swap.')
    run(['fallocate', '-l', '2G', swap])
    swap.chmod(0o600)
    run(['mkswap', swap])
    run(['swapon', swap])
    fstab = Path('/etc/fstab')
    original = fstab.read_text()
    if not any(line.split()[:1] == [str(swap)] for line in original.splitlines()):
        write(fstab, original.rstrip() + '\n/var/swap-radxa none swap sw 0 0\n')


def configure_desktop(account):
    home = Path(account.pw_dir)
    vncdir = home / '.vnc'
    vncdir.mkdir(mode=0o700, exist_ok=True)
    os.chown(vncdir, account.pw_uid, account.pw_gid)
    vncdir.chmod(0o700)
    if not (vncdir / 'passwd').exists():
        print('Set a separate VNC/RDP password (6-8 characters). Choose n for view-only.', flush=True)
        user_run(account.pw_name, ['tigervncpasswd', str(vncdir / 'passwd')])
    if not (vncdir / 'passwd').is_file():
        raise RuntimeError('VNC password not created.')
    (vncdir / 'passwd').chmod(0o600)
    write(vncdir / 'Xtigervnc-session', '''#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
export XDG_CURRENT_DESKTOP=XFCE
export XDG_SESSION_DESKTOP=xfce
export LIBGL_ALWAYS_SOFTWARE=1
exec /usr/bin/dbus-run-session -- /usr/bin/startxfce4
''', 0o700, account)
    # XFCE and VNC both use this one virtual display; no HDMI Xorg is started.
    service = f'''[Unit]
Description=Radxa shared XFCE desktop (TigerVNC)
After=network.target

[Service]
Type=simple
User={account.pw_name}
Group={account.pw_name}
PAMName=login
WorkingDirectory={home}
Environment=HOME={home}
Environment=USER={account.pw_name}
Environment=LOGNAME={account.pw_name}
ExecStart=/usr/bin/tigervncserver :1 -fg -geometry 1280x720 -depth 24 -localhost yes -SecurityTypes VncAuth -PasswordFile {vncdir}/passwd -xstartup {vncdir}/Xtigervnc-session
ExecStop=/usr/bin/tigervncserver -kill :1
Restart=on-failure
RestartSec=5
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
'''
    write('/etc/systemd/system/radxa-desktop.service', service)
    ini = Path('/etc/xrdp/xrdp.ini')
    original = ini.read_text()
    # Keep the distribution's global/channel settings, replace selectable sessions.
    write(ini, xrdp_config(original))
    run(['usermod', '-aG', 'ssl-cert', 'xrdp'])
    run(['usermod', '-aG', 'dialout,video', account.pw_name])
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', '--now', 'radxa-desktop.service', 'ssh', 'avahi-daemon'])
    run(['systemctl', 'enable', 'xrdp'])
    run(['systemctl', 'restart', 'xrdp'])
    run(['systemctl', 'is-active', 'radxa-desktop.service', 'xrdp', 'ssh'])


def merge_wifi_credentials(saved, access_points):
    """Prefer the repaired netplan credentials over the original first-boot copy."""
    data = {name: dict(saved[name]) for name in ('phone', 'lab')}
    for name, entry in data.items():
        current = access_points.get(entry['ssid']) or {}
        password = current.get('password')
        if password is None:
            password = (current.get('auth') or {}).get('password')
        if password is not None:
            entry['password'] = password
        wifi_profile('validate', entry, 0, str(uuid.uuid4()))
    if data['phone']['ssid'] == data['lab']['ssid']:
        raise ValueError('Phone and lab must have distinct SSIDs.')
    return data


def network():
    if (STATE / 'network-prepared').exists():
        print('Network transition already prepared. Reboot and verify both networks.', flush=True)
        return
    if not (STATE / 'base-ready').exists():
        raise RuntimeError('Finish the base installation before running the network stage.')
    if WIFI.exists():
        saved = json.loads(WIFI.read_text())
    else:
        saved = {
            'phone': {'ssid': input('Phone hotspot SSID: '),
                      'password': getpass.getpass('Phone hotspot password: ')},
            'lab': {'ssid': input('Lab SSID: '),
                    'password': getpass.getpass('Lab Wi-Fi password: ')},
        }
    # Capture locally: merged netplan output contains passwords and must not be logged.
    import yaml
    merged = yaml.safe_load(subprocess.check_output(['netplan', 'get'], text=True)) or {}
    interface = merged.get('network', {}).get('wifis', {}).get('wlan0', {})
    data = merge_wifi_credentials(saved, interface.get('access-points') or {})
    if not (HERE / 'hotspot-prefer.py').is_file():
        raise RuntimeError('Copy the whole ubuntu22-setup directory, not only setup.py.')
    install('network-manager')
    write(WIFI, json.dumps(data, ensure_ascii=False, indent=2) + '\n', 0o600)
    prepare_network(data)
    print('NETWORK PREPARED. Current connection retained until reboot. '
          'After reboot, verify lab Wi-Fi with the phone hotspot OFF, then test hotspot preference.',
          flush=True)


def prepare_network(data=None):
    marker = STATE / 'network-prepared'
    if marker.exists():
        print('Network transition already prepared; retaining its config.', flush=True)
        return
    if data is None:
        data = json.loads(WIFI.read_text())
    if data['phone']['ssid'] == data['lab']['ssid']:
        raise ValueError('Phone and lab must have distinct SSIDs.')
    namespace = uuid.UUID('95a4f4dd-c640-42e0-979f-88c5e4095b0c')
    ids = {name: str(uuid.uuid5(namespace, name)) for name in ('phone', 'lab')}
    profiles = {name: wifi_profile('radxa-' + name, data[name], priority, ids[name])
                for name, priority in [('phone', 100), ('lab', 10)]}
    for name, text in profiles.items():
        write('/etc/NetworkManager/system-connections/radxa-' + name + '.nmconnection',
              text, 0o600)
    write('/etc/radxa/hotspot-prefer.json', json.dumps({
        'phone_ssid': data['phone']['ssid'], 'phone_uuid': ids['phone'], 'lab_uuid': ids['lab'],
    }, indent=2) + '\n', 0o600)
    write('/usr/local/sbin/radxa-hotspot-prefer',
          (HERE / 'hotspot-prefer.py').read_text(), 0o755)
    write('/etc/systemd/system/radxa-hotspot-prefer.service', '''[Unit]
Description=Prefer phone hotspot when visible
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/radxa-hotspot-prefer
TimeoutStartSec=180
''')
    write('/etc/systemd/system/radxa-hotspot-prefer.timer', '''[Unit]
Description=Check phone hotspot availability

[Timer]
OnBootSec=40
OnUnitInactiveSec=30
AccuracySec=3

[Install]
WantedBy=timers.target
''')
    write('/etc/cloud/cloud.cfg.d/99-radxa-disable-network.cfg',
          'network: {config: disabled}\n', 0o600)
    netplan = Path('/etc/netplan')
    existing = [*netplan.glob('*.yaml'), *netplan.glob('*.yml')]
    for path in existing:
        if path.is_symlink():
            raise RuntimeError('Unexpected netplan symlink: ' + str(path))
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    archive = netplan / ('before-radxa-' + stamp)
    archive.mkdir(mode=0o700)
    destination = netplan / '90-radxa-networkmanager.yaml'
    try:
        for path in existing:
            shutil.move(str(path), archive / path.name)
        write(destination, 'network:\n  version: 2\n  renderer: NetworkManager\n', 0o600,
              keep_backup=False)
        run(['netplan', 'generate'])
    except Exception:
        destination.unlink(missing_ok=True)
        for path in archive.iterdir():
            shutil.move(str(path), netplan / path.name)
        subprocess.run(['netplan', 'generate'], check=False)
        raise
    # Do not apply/restart networking over the SSH connection used for setup.
    run(['systemctl', 'enable', 'NetworkManager'])
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', 'radxa-hotspot-prefer.timer'])
    write(marker, 'Prepared; takes effect after reboot.\n', 0o600, keep_backup=False)
    print('Network change prepared for reboot. Do not run netplan apply in this SSH session.', flush=True)


def base(account, keep_network=False):
    if not keep_network:
        if not WIFI.exists():
            raise RuntimeError('Missing /etc/radxa/wifi-profiles.json. Use --keep-network to retain existing Wi-Fi.')
        # Validate credentials before installing or changing anything.
        credentials = json.loads(WIFI.read_text())
        for name in ('phone', 'lab'):
            wifi_profile('validate', credentials[name], 0, str(uuid.uuid4()))
        if not (HERE / 'hotspot-prefer.py').is_file():
            raise RuntimeError('Copy the whole ubuntu22-setup directory, not only setup.py.')
    run(['apt-get', 'update'])
    if not keep_network:
        install('network-manager')
    install('openssh-server', 'wpasupplicant', 'avahi-daemon',
            'xfce4', 'xfce4-terminal', 'dbus-x11', 'x11-xserver-utils', 'xauth',
            'fonts-dejavu-core', 'xfonts-base', 'tigervnc-standalone-server',
            'tigervnc-common', 'tigervnc-tools', 'xrdp', 'ssl-cert',
            'build-essential', 'cmake', 'ninja-build', 'pkg-config', 'git', 'gdb',
            'python3-dev', 'python3-pip', 'python3-venv', 'python3-serial',
            'python3-numpy', 'python3-yaml', 'python3-empy', 'python3-pytest',
            'python3-setuptools', 'libeigen3-dev', 'libssl-dev', 'libasio-dev',
            'libtinyxml2-dev', 'libusb-1.0-0-dev', 'libspdlog-dev', 'curl', 'ca-certificates',
            'locales', 'software-properties-common', 'htop', 'rsync', 'minicom')
    ensure_swap()
    configure_desktop(account)
    if keep_network:
        print('Keeping the working Wi-Fi configuration; automatic hotspot switching deferred.', flush=True)
        write(STATE / 'network-kept', 'Existing Wi-Fi retained.\n', 0o600, keep_backup=False)
    else:
        prepare_network()
    write(STATE / 'base-ready', 'Base configured.\n', 0o600, keep_backup=False)
    if keep_network:
        print('BASE READY. Existing Wi-Fi retained. No reboot needed for the next setup stage.', flush=True)
    else:
        print('BASE READY. Run sudo reboot when ready. Keep the phone hotspot ON.', flush=True)


def install_ros_source():
    with tempfile.TemporaryDirectory(prefix='radxa-ros-') as temp:
        os.chmod(temp, 0o755)
        package = Path(temp) / ROS_SOURCE_FILE
        cached = HERE / ROS_SOURCE_FILE
        if cached.is_file():
            print('Using ROS source package copied from the laptop.', flush=True)
            shutil.copyfile(cached, package)
        else:
            run(['curl', '-fL', '--connect-timeout', '15', '--max-time', '120',
                 '--retry', '3', '-o', package,
                 'https://github.com/ros-infrastructure/ros-apt-source/releases/download/'
                 '1.3.0/' + ROS_SOURCE_FILE])
        # Matches the copy retrieved directly from the official release over HTTPS.
        # This checks file integrity; it is not a separately published signature.
        if hashlib.sha256(package.read_bytes()).hexdigest() != ROS_SOURCE_SHA256:
            raise RuntimeError('ROS source package integrity check failed; copy the original .deb again.')
        package.chmod(0o644)
        install(str(package))


def rosdep_environment():
    environment = dict(os.environ)
    index = ROSDEP_DATA / 'index-v4.yaml'
    if index.is_file():
        environment['ROSDISTRO_INDEX_URL'] = index.as_uri()
    return environment


def configure_rosdep(account):
    if not Path('/opt/ros/humble/setup.bash').is_file():
        raise RuntimeError('Install ROS 2 Humble packages first using the ros stage.')
    source = HERE / 'rosdep-data'
    manifest = json.loads((source / 'manifest.json').read_text())
    required = ['base.yaml', 'python.yaml', 'ruby.yaml', 'osx-homebrew.yaml',
                'humble-distribution.yaml', 'humble-cache.yaml.gz']
    for name in required:
        contents = (source / name).read_bytes()
        if hashlib.sha256(contents).hexdigest() != manifest['files'][name]['sha256']:
            raise RuntimeError('Local rosdep data integrity check failed: ' + name)
    ROSDEP_DATA.mkdir(parents=True, exist_ok=True)
    ROSDEP_DATA.chmod(0o755)
    for name in required:
        write(ROSDEP_DATA / name, (source / name).read_bytes(), keep_backup=False)
    index = {
        'type': 'index', 'version': 4,
        'distributions': {'humble': {
            'distribution': [(ROSDEP_DATA / 'humble-distribution.yaml').as_uri()],
            'distribution_cache': (ROSDEP_DATA / 'humble-cache.yaml.gz').as_uri(),
            'distribution_status': 'active', 'distribution_type': 'ros2',
            'python_version': 3,
        }},
    }
    # JSON is a YAML subset accepted by rosdistro's YAML reader.
    write(ROSDEP_DATA / 'index-v4.yaml', json.dumps(index, indent=2) + '\n')
    listing = '# Local snapshot from the official ROS rosdistro repository.\n'
    for name in ('osx-homebrew.yaml', 'base.yaml', 'python.yaml', 'ruby.yaml'):
        listing += 'yaml ' + (ROSDEP_DATA / name).as_uri()
        listing += ' osx\n' if name == 'osx-homebrew.yaml' else '\n'
    write('/etc/ros/rosdep/sources.list.d/20-default.list', listing)
    print('Updating rosdep from the local official-data snapshot; no GitHub downloads.', flush=True)
    user_run(account.pw_name, ['rosdep', 'update', '--rosdistro', 'humble'],
             env=rosdep_environment())
    # Future rosdep commands should use this same local Humble index.
    import yaml
    config = Path(account.pw_dir) / '.config/rosdistro/config.yaml'
    settings = yaml.safe_load(config.read_text()) if config.exists() else {}
    settings = settings or {}
    settings['index_url'] = (ROSDEP_DATA / 'index-v4.yaml').as_uri()
    write(config, yaml.safe_dump(settings, sort_keys=False), owner=account)
    for parent in (config.parent, config.parent.parent):
        os.chown(parent, account.pw_uid, account.pw_gid)
    bashrc = Path(account.pw_dir) / '.bashrc'
    existing = bashrc.read_text() if bashrc.exists() else ''
    lines = ['source /opt/ros/humble/setup.bash']
    for line in lines:
        if line not in existing.splitlines():
            existing = existing.rstrip() + '\n' + line + '\n'
    write(bashrc, existing, owner=account)
    write(STATE / 'ros-ready', 'ROS 2 Humble base and local rosdep configured.\n',
          0o600, keep_backup=False)
    print('ROS READY. Start a new terminal to load ROS 2 Humble.', flush=True)


def ros(account):
    run(['apt-get', 'update'])
    install('curl', 'ca-certificates', 'locales', 'software-properties-common')
    run(['add-apt-repository', '-y', 'universe'])
    run(['locale-gen', 'en_US.UTF-8'])
    run(['update-locale', 'LANG=en_US.UTF-8'])
    run(['apt-get', 'update'])
    # ROS documentation requires these Jammy components to be updated first.
    run(['apt-get', '-y', '--no-remove', 'install', '--only-upgrade',
         'systemd', 'udev', 'libudev1', 'libsystemd0'], env=APT_ENV)
    install_ros_source()
    run(['apt-get', 'update'])
    install('ros-humble-ros-base', 'python3-colcon-common-extensions',
            'python3-rosdep', 'python3-vcstool')
    configure_rosdep(account)


def px4(account, version):
    if not Path('/opt/ros/humble/setup.bash').exists():
        raise RuntimeError('Run the ros stage first.')
    # The release family must match the firmware, confirmed as PX4 1.17.0.
    if not re.fullmatch(r'1\.(14|15|16|17|18)', version or ''):
        raise ValueError('Specify the PX4 release family; this board uses --px4-version 1.17.')
    install('ros-humble-fastrtps', 'ros-humble-fastcdr', 'libspdlog-dev')
    home = Path(account.pw_dir)
    workspace = home / 'ros2_px4_ws'
    source = workspace / 'src'
    source.mkdir(parents=True, exist_ok=True)
    for path in (workspace, source):
        os.chown(path, account.pw_uid, account.pw_gid)
    branch = 'release/' + version
    for directory, remote, revision in [
        ('px4_msgs', 'https://github.com/PX4/px4_msgs.git', branch),
        ('Micro-XRCE-DDS-Agent', 'https://github.com/eProsima/Micro-XRCE-DDS-Agent.git', 'v2.4.2'),
    ]:
        destination = source / directory
        if not destination.exists():
            user_run(account.pw_name, ['git', 'clone', '--depth', '1', '--branch', revision,
                                      remote, str(destination)])
        else:
            if directory == 'px4_msgs':
                actual = subprocess.check_output(
                    ['runuser', '-u', account.pw_name, '--', 'git', '-C', str(destination),
                     'branch', '--show-current'], text=True).strip()
            else:
                actual = subprocess.check_output(
                    ['runuser', '-u', account.pw_name, '--', 'git', '-C', str(destination),
                     'describe', '--tags', '--exact-match'], text=True).strip()
            if actual != revision:
                raise RuntimeError(str(destination) + ' already exists at a different version; retained.')
    # Build against the installed Humble dependencies, not a second Fast DDS tree.
    user_run(account.pw_name, ['bash', '-c',
        'source /opt/ros/humble/setup.bash && '
        'rosdep install --from-paths src --ignore-src --rosdistro humble -r -y '
        '--skip-keys "fastcdr fastrtps"'], cwd=workspace, env=rosdep_environment())
    user_run(account.pw_name, ['bash', '-c',
        'source /opt/ros/humble/setup.bash && '
        'export CMAKE_BUILD_PARALLEL_LEVEL=1 MAKEFLAGS=-j1 && '
        'colcon build --executor sequential --cmake-args '
        '-DCMAKE_BUILD_TYPE=Release -DUAGENT_SUPERBUILD=OFF '
        '-DUAGENT_USE_SYSTEM_FASTDDS=ON -DUAGENT_USE_SYSTEM_FASTCDR=ON '
        '-DUAGENT_USE_SYSTEM_LOGGER=ON '
        '-DUAGENT_P2P_PROFILE=OFF -DUAGENT_BUILD_TESTS=OFF'], cwd=workspace)
    bashrc = home / '.bashrc'
    existing = bashrc.read_text()
    line = f'source {workspace}/install/setup.bash'
    if line not in existing.splitlines():
        write(bashrc, existing.rstrip() + '\n' + line + '\n', owner=account)
    write(STATE / 'px4-ready', 'PX4 messages: ' + branch + '\nAgent: v2.4.2\n',
          0o600, keep_backup=False)
    print('PX4 BUILD READY. UART and flight-controller parameters are NOT changed.', flush=True)


def check(account):
    for command in [
        ['systemctl', 'is-active', 'ssh', 'xrdp', 'radxa-desktop.service'],
        ['ip', '-br', 'address'], ['free', '-h'], ['swapon', '--show'],
        ['ss', '-ltn'], ['groups', account.pw_name],
    ]:
        subprocess.run(command, check=False)
    if (STATE / 'network-prepared').exists():
        subprocess.run(['systemctl', 'is-enabled', 'radxa-hotspot-prefer.timer'], check=False)
        subprocess.run(['nmcli', '-f', 'NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY',
                        'connection', 'show'], check=False)
    if Path('/opt/ros/humble/setup.bash').exists():
        user_run(account.pw_name, ['bash', '-c',
            'source /opt/ros/humble/setup.bash && printenv ROS_DISTRO && '
            'command -v colcon && gcc --version | head -n 1'])
    print('Checks complete. Reboot/headless/network-fallback tests still require the board.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['base', 'ros', 'rosdeps', 'px4', 'network', 'check', 'all'])
    parser.add_argument('--user', default=os.environ.get('SUDO_USER', 'ubuntu'))
    parser.add_argument('--px4-version')
    parser.add_argument('--keep-network', action='store_true',
                        help='Keep working Wi-Fi and defer NetworkManager/hotspot switching setup.')
    args = parser.parse_args()
    if args.stage in ('all', 'px4') and not re.fullmatch(r'1\.(14|15|16|17|18)', args.px4_version or ''):
        parser.error('PX4 1.17.0 requires --px4-version 1.17.')
    if os.geteuid() != 0:
        raise SystemExit('Use sudo python3 setup.py ' + args.stage)
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines()
                   if '=' in line)
    if release.get('ID', '').strip('"') != 'ubuntu' or release.get('VERSION_ID', '').strip('"') != '22.04':
        raise SystemExit('This script only supports the freshly flashed Ubuntu 22.04 image, not the old Debian system.')
    if platform.machine() not in ('aarch64', 'arm64'):
        raise SystemExit('Expected an ARM64 board.')
    account = pwd.getpwnam(args.user)
    if account.pw_uid == 0 or not re.fullmatch(r'[a-z_][a-z0-9_-]*', args.user):
        raise SystemExit('Choose a regular Linux user, normally ubuntu.')
    if ' ' in account.pw_dir:
        raise SystemExit('Home directories with spaces are not supported by the desktop service template.')
    STATE.mkdir(parents=True, exist_ok=True)
    if args.stage == 'base':
        base(account, keep_network=args.keep_network)
    elif args.stage == 'ros':
        ros(account)
    elif args.stage == 'rosdeps':
        configure_rosdep(account)
    elif args.stage == 'px4':
        px4(account, args.px4_version)
    elif args.stage == 'network':
        network()
    elif args.stage == 'all':
        base(account, keep_network=args.keep_network)
        ros(account)
        px4(account, args.px4_version)
        check(account)
        print('ALL READY. Desktop, VNC, build tools, ROS and PX4 messages installed.', flush=True)
    else:
        check(account)


if __name__ == '__main__':
    main()

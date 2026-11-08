#!/usr/bin/env python3
"""Opt-in private overlay account-FD test; no board or KVM acceptance.

Uses only a new /var/tmp directory and tmpfs in a fresh mount namespace. Never
mounts over the host's /etc or opens a device. The board-specific parent guard
is replaced by an equivalent fixture-directory fence; file reads and fdinfo
mount checks run the production implementation unchanged.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('overlay_accounts', BASE / 'scripts/guest-accounts.py')
A = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(A)


def verify():
    A.require(os.geteuid() == 0, 'root required')
    filesystem = subprocess.run(['findmnt', '-n', '-o', 'FSTYPE', '-T', '/var/tmp'],
                                check=True, capture_output=True, text=True).stdout.strip()
    A.require(filesystem in ('ext4', 'xfs', 'btrfs'), 'fixture requires disk-backed /var/tmp')
    with tempfile.TemporaryDirectory(prefix='a733-account-overlay-', dir='/var/tmp') as temporary:
        root = Path(temporary)
        lower, merged, ram = (root / name for name in ('lower', 'merged', 'ram'))
        for path in (lower, merged, ram):
            path.mkdir(mode=0o755)
        payloads = {'passwd': b'root:x:0:0:root:/root:/bin/bash\n',
                    'group': b'root:x:0:\n', 'shadow': b'root:!:::::::\n',
                    'gshadow': b'root:!::\n'}
        for name, data in payloads.items():
            (lower / name).write_bytes(data)
            (lower / name).chmod(0o600 if name in ('shadow', 'gshadow') else 0o644)
        mounts = []
        fd = None

        def mount(target, *args):
            subprocess.run(['mount', *args, str(target)], check=True)
            mounts.append(target)

        try:
            mount(lower, '--bind', str(lower))
            subprocess.run(['mount', '-o', 'remount,bind,ro', str(lower)], check=True)
            mount(ram, '-t', 'tmpfs', '-o', 'size=16M,nodev,nosuid,mode=0755', 'tmpfs')
            upper, work = ram / 'upper', ram / 'work'
            upper.mkdir()
            work.mkdir()
            mount(merged, '-t', 'overlay', '-o',
                  f'lowerdir={lower},upperdir={upper},workdir={work},xino=off', 'overlay')
            fd = os.open(merged, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info, mount_id = os.fstat(fd), A.held_mount_id(fd)

            def fixture_parent(held, before, expected):
                A.require(A.metadata(os.fstat(held)) == A.metadata(before) == A.metadata(merged.stat()),
                          'fixture parent changed')
                A.require(A.held_mount_id(held) == expected, 'fixture held mount changed')
                rows = [line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()
                        if line.split()[4] == str(merged)]
                A.require(len(rows) == 1 and int(rows[0][0]) == expected and
                          rows[0][rows[0].index('-') + 1] == 'overlay', 'fixture mount changed')

            records = []
            with patch.object(A, 'validate_etc', side_effect=fixture_parent):
                for stage in ('lower', 'copied-up'):
                    if stage == 'copied-up':
                        for name in payloads:
                            # Opening for write forces real copy-up without changing bytes.
                            with (merged / name).open('r+b') as stream:
                                stream.write(payloads[name])
                        info = os.fstat(fd)
                    for name, data in payloads.items():
                        A.require(A.read_database(name, fd, info, mount_id) == data, 'fixture data differs')
                        records.append({'stage': stage, 'name': name,
                                        'directory_device': info.st_dev, 'file_device': (merged / name).stat().st_dev,
                                        'mount_id': mount_id})
                A.require(all(row['file_device'] != row['directory_device'] for row in records),
                          'xino=off did not exercise distinct non-directory devices')
                mount(merged / 'passwd', '--bind', str(upper / 'passwd'))
                A.require((merged / 'passwd').stat().st_dev == (upper / 'passwd').stat().st_dev,
                          'bind fixture must share backing filesystem')
                with patch.object(A.os, 'read', wraps=os.read) as read:
                    try:
                        A.read_database('passwd', fd, info, mount_id)
                    except ValueError as error:
                        A.require(str(error) == 'foreign account database mount', 'unexpected bind refusal')
                    else:
                        raise ValueError('bind-over-file was accepted')
                    read.assert_not_called()
            return {'classification': 'private disk-lower/tmpfs-upper software fixture; no hardware acceptance',
                    'kernel': os.uname().release, 'lower_filesystem': filesystem,
                    'xino': 'off', 'reads': records, 'same_filesystem_bind_refused_before_read': True}
        finally:
            if fd is not None:
                os.close(fd)
            for target in reversed(mounts):
                subprocess.run(['umount', str(target)], check=True)


if __name__ == '__main__':
    if len(sys.argv) != 1 or os.geteuid() != 0:
        sys.exit(f'Usage: sudo python3 {sys.argv[0]}')
    os.execvp('unshare', ['unshare', '--mount', '--propagation', 'private', '--fork', sys.executable, '-c',
                         "import json,runpy,sys; print(json.dumps(runpy.run_path(sys.argv[1])['verify'](), indent=2))",
                         str(Path(__file__).resolve())])

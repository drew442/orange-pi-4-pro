#!/usr/bin/env python3
"""Opt-in root test of an ephemeral /etc overlay on private directory fixtures.

No device paths or physical storage operations. Always starts a private mount
namespace; fixture writes are confined to a new temporary directory and tmpfs.
"""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


def verify():
    if os.geteuid() != 0:
        raise ValueError('root required')
    parent = Path('/var/tmp')
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX):
        raise ValueError('staging parent must be root-controlled')
    with tempfile.TemporaryDirectory(prefix='a733-etc-overlay-', dir=parent) as temporary:
        root = Path(temporary)
        root.chmod(0o711)  # Allow the unprivileged read probe to traverse.
        lower, witness, run = (root / name for name in ('etc', 'lower-witness', 'run'))
        for path in (lower, witness, run):
            path.mkdir(mode=0o755)
        (lower / 'configuration').write_text('immutable configuration\n')
        (lower / 'configuration').chmod(0o644)
        mounts = []

        def mount(target, *arguments):
            subprocess.run(['mount', *arguments, str(target)], check=True)
            mounts.append(target)

        try:
            mount(lower, '--bind', str(lower))
            subprocess.run(['mount', '-o', 'remount,bind,ro', str(lower)], check=True)
            mount(witness, '--bind', str(lower))
            mount(run, '-t', 'tmpfs', '-o', 'size=64M,mode=0755,nodev,nosuid', 'tmpfs')
            upper, work = run / 'upper', run / 'work'
            upper.mkdir(mode=0o755)
            work.mkdir(mode=0o700)
            mount(lower, '-t', 'overlay', '-o', f'lowerdir={lower},upperdir={upper},workdir={work}', 'overlay')
            if lower.stat().st_mode & 0o777 != 0o755:
                raise ValueError('overlay /etc must remain searchable by unprivileged services')
            (lower / 'ephemeral').write_text('RAM only\n')
            if (witness / 'ephemeral').exists() or (witness / 'configuration').read_text() != 'immutable configuration\n':
                raise ValueError('lower directory changed')
            if (upper / 'ephemeral').read_text() != 'RAM only\n':
                raise ValueError('overlay writes did not reach RAM upper')
            usage = os.statvfs(run)
            if usage.f_blocks * usage.f_frsize != 64 * 1024**2:
                raise ValueError('tmpfs size differs from bound')
            child = os.fork()
            if child == 0:
                try:
                    os.setgroups([])
                    os.setgid(65534)
                    os.setuid(65534)
                    ok = (lower / 'configuration').read_text() == 'immutable configuration\n'
                    os._exit(0 if ok else 1)
                except BaseException:
                    os._exit(1)
            _, status = os.waitpid(child, 0)
            if status != 0:
                raise ValueError('unprivileged configuration read failed')
        finally:
            for target in reversed(mounts):
                subprocess.run(['umount', str(target)], check=True)
        if (lower / 'ephemeral').exists() or (lower / 'configuration').read_text() != 'immutable configuration\n':
            raise ValueError('fixture bytes changed after unmount')
        return {'classification': 'builder directory/tmpfs mount test; no board or KVM acceptance',
                'kernel': os.uname().release, 'overlay_mode': '0755', 'tmpfs_bytes': 64 * 1024**2,
                'unprivileged_read': 'passed', 'writes_in_tmpfs': True, 'lower_unchanged': True}


if __name__ == '__main__':
    if len(sys.argv) != 1 or os.geteuid() != 0:
        sys.exit(f'Usage: sudo python3 {sys.argv[0]}')
    os.execvp('unshare', ['unshare', '--mount', '--propagation', 'private', '--fork', sys.executable, '-c',
                         "import json,runpy,sys; print(json.dumps(runpy.run_path(sys.argv[1])['verify'](), indent=2))",
                         str(Path(__file__).resolve())])

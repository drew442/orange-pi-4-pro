#!/usr/bin/env python3
"""Prepare ten operator-performed USB-C disconnect cold boots of the known A733."""
import argparse
import base64
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import shutil
import stat
import sys
import time

REPO = Path(__file__).resolve().parents[1]
WARM_SHA256 = '431a5b054d7a5a304c61b0a00e160ad47a7868451ee06c9c5af3b17485d4a572'
MIB = 1024 * 1024
BUDGET = 8 * MIB
SERIES_SECONDS = 28800


def require(ok, why):
    if not ok:
        raise ValueError(why)


def warm_module():
    path = REPO / 'scripts/collect-known-warm-boots.py'
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'unsafe warm source')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == WARM_SHA256, 'warm source pin differs')
    spec = importlib.util.spec_from_file_location('cold_guard_source', path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def poweroff_source(warm, expected):
    return ('EXPECTED = ' + repr(expected) + '\nREBOOT = False\n' + warm.REMOTE +
            "\nrequire(read('/proc/sys/kernel/random/boot_id') == EXPECTED, 'boot changed before poweroff')\n"
            "subprocess.run(['systemctl', 'poweroff'], check=True, timeout=20)\n").encode()


def poweroff(warm, expected, timeout):
    return warm.TRANSPORT.bounded_command(warm.TRANSPORT.ssh_argv(), warm.LIMIT,
            timeout=timeout, source=poweroff_source(warm, expected))


class Evidence:
    def __init__(self, tag):
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', tag), 'invalid tag')
        parent = REPO / 'out'
        require(parent.is_dir() and not any(p.is_symlink() for p in (parent, *parent.parents)),
                'out must have real directory ancestry')
        require(shutil.disk_usage(parent).free >= 256 * MIB + BUDGET,
                'need 256 MiB free floor plus 8 MiB evidence reserve')
        self.path = parent / ('cold-boots-' + tag)
        self.path.mkdir(mode=0o700)
        self.fd = os.open(self.path / 'events.jsonl', os.O_WRONLY | os.O_CREAT |
                          os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.size = 0
        for path in (self.path, parent):
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def save(self, event, **fields):
        raw = (json.dumps(dict(event=event, hardware_acceptance=False,
                utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                monotonic=time.monotonic(), **fields), allow_nan=False) + '\n').encode()
        limit = BUDGET if event == 'failed-or-refused' else BUDGET - 65536
        require(self.size + len(raw) <= limit, '8 MiB evidence budget exhausted')
        require(shutil.disk_usage(self.path).free >= 256 * MIB + len(raw),
                'evidence filesystem free floor reached')
        view = memoryview(raw)
        while view:
            count = os.write(self.fd, view)
            require(count > 0, 'zero evidence write')
            self.size += count
            view = view[count:]
        os.fsync(self.fd)

    def close(self):
        os.close(self.fd)


class UART:
    """Hold the native archive inode and consume only bytes after a fresh offset."""
    def __init__(self, path):
        self.path = Path(path)
        require(not any(p.is_symlink() for p in (self.path, *self.path.parents)), 'unsafe UART path')
        self.fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(self.fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'unsafe UART archive')
        self.identity = (info.st_dev, info.st_ino)
        self.offset = info.st_size
        os.lseek(self.fd, self.offset, os.SEEK_SET)

    def mark(self):
        held, named = os.fstat(self.fd), self.path.stat(follow_symlinks=False)
        require((held.st_dev, held.st_ino) == (named.st_dev, named.st_ino) == self.identity
                and held.st_size >= self.offset and stat.S_ISREG(named.st_mode),
                'UART archive replaced/truncated')
        return dict(device=held.st_dev, inode=held.st_ino, offset=self.offset)

    def read(self):
        self.mark()
        data = os.read(self.fd, 65536)
        self.offset += len(data)
        self.mark()
        return data

    def close(self):
        os.close(self.fd)


def confirm(deadline, clock=time.monotonic):
    require(sys.stdin.isatty() and sys.stdout.isatty(), 'local interactive TTY required')
    print('Personally disconnect the A733 USB-C POWER cable for at least 10 seconds, '
          'then reconnect it. Have you personally completed that physical step '
          'for this cycle (yes/no)?', flush=True)
    line = b''
    started = clock()
    progress = started + 30
    while clock() < deadline:
        if clock() >= progress:
            print("Waiting for your physical USB-C power-cycle attestation (yes/no); eight-hour cap remains active.", flush=True)
            progress = clock() + 30
        ready, _, _ = select.select([sys.stdin], [], [], min(1, deadline - clock()))
        if ready:
            chunk = os.read(sys.stdin.fileno(), 1)
            require(chunk, 'operator input EOF; no attestation')
            line += chunk
            require(len(line) <= 16, 'invalid operator answer')
            if chunk == b'\n':
                require(line.strip().lower() == b'yes', 'operator did not attest physical cold power')
                require(clock() - started >= 10, 'physical disconnect attestation arrived before 10 seconds')
                return True
    raise ValueError('eight-hour series deadline while waiting for operator')


def collect(evidence, start, warm, *, probe=None, request=None, preserved=None,
            operator=confirm, uart_factory=None, clock=time.monotonic, sleep=time.sleep,
            tty=None):
    probe = probe or warm.transport
    request = request or (lambda expected, timeout: poweroff(warm, expected, timeout))
    preserved = preserved or warm.retained_outcome
    uart_factory = uart_factory or (lambda: UART(warm.TRANSPORT.UART))
    tty = tty or (lambda: sys.stdin.isatty() and sys.stdout.isatty())
    current, seen, completed = warm.canonical(start), {start}, 0
    deadline = clock() + SERIES_SECONDS

    def remaining(limit):
        require(clock() < deadline, 'eight-hour series deadline exceeded')
        return min(limit, deadline - clock())

    try:
        require(tty(), 'local interactive TTY required')
        evidence.save('preservation', retained=preserved(), warm_source_sha256=WARM_SHA256)
        for cycle in range(1, 11):
            remaining(1)
            preserved()
            raw = probe(current, timeout=remaining(30))
            evidence.save('snapshot', cycle=cycle, transport=raw)
            require(warm.decode(raw)['boot_id'] == current, 'pre-poweroff boot ID differs')
            uart = uart_factory()
            try:
                evidence.save('uart-start', cycle=cycle, **uart.mark())
                evidence.save('requested', cycle=cycle, boot_id=current,
                              command=['systemctl', 'poweroff'])
                print(f'Cold boot {cycle}/10: requesting clean poweroff from {current}', flush=True)
                off_deadline = min(deadline, clock() + 60)
                raw = request(current, remaining(min(30, off_deadline - clock())))
                evidence.save('request-response', cycle=cycle, transport=raw)
                admission, closed = warm.decode_reboot_admission(raw)
                require(admission['boot_id'] == current, 'poweroff admission boot differs')
                evidence.save('poweroff-admission', cycle=cycle,
                              command_exit='UNKNOWN' if closed else 0,
                              classification='transport-closed-before-command-exit' if closed else 'command-returned')
                tail = b''
                while True:
                    require(clock() < off_deadline, '60-second UART Power down deadline exceeded')
                    data = uart.read()
                    if data:
                        evidence.save('uart', cycle=cycle, data_b64=base64.b64encode(data).decode(), **uart.mark())
                        tail = (tail + data)[-65536:]
                        if re.search(rb'(?:^|\n)(?:\[[^\r\n]*\]\s*)?reboot: Power down\r?(?:\n|$)', tail):
                            break
                    print(f'Cold boot {cycle}/10: waiting for native UART Power down', flush=True)
                    sleep(min(1, max(0, off_deadline - clock())))
                evidence.save('power-down', cycle=cycle, **uart.mark())
                ack_started = clock()
                require(operator(deadline) is True, 'operator physical power attestation required')
                remaining(1)
                require(clock() - ack_started >= 10, 'operator physical disconnect interval below 10 seconds')
                evidence.save('operator-attestation', cycle=cycle, personally_performed=True,
                              action='USB-C power disconnected >=10 seconds and reconnected',
                              question='Personally completed this physical step for this cycle?', answer='yes')
                ready_deadline = min(deadline, clock() + 300)
                while True:
                    require(clock() < ready_deadline, '300-second new-boot deadline exceeded')
                    data = uart.read()
                    if data:
                        evidence.save('uart', cycle=cycle, data_b64=base64.b64encode(data).decode(), **uart.mark())
                    raw = probe(timeout=min(30, ready_deadline - clock()))
                    evidence.save('poll', cycle=cycle, transport=raw)
                    try:
                        ready = warm.decode(raw)
                    except (ValueError, KeyError, TypeError) as error:
                        if isinstance(error, warm.TerminalReleaseRefusal) or (raw.get('returncode') == 0
                                and not raw.get('timed_out') and not raw.get('truncated')):
                            raise
                        ready = None
                    require(clock() < ready_deadline, 'new boot observed after deadline')
                    if ready and ready['boot_id'] != current:
                        require(ready['boot_id'] not in seen, 'reused boot ID')
                        current = ready['boot_id']
                        seen.add(current)
                        completed += 1
                        evidence.save('observed', cycle=cycle, boot_id=current, **uart.mark())
                        print(f'Cold boot {cycle}/10: ready {current}; coordinator review required', flush=True)
                        break
                    print(f'Cold boot {cycle}/10: waiting for distinct ready boot', flush=True)
                    sleep(min(1, max(0, ready_deadline - clock())))
                evidence.save('uart-end', cycle=cycle, **uart.mark())
            finally:
                uart.close()
        evidence.save('complete', completed=completed, boot_ids=sorted(seen),
                      classification='coordinator-review-required; SD boundary hash review pending')
        return completed
    except Exception as error:
        evidence.save('failed-or-refused', completed=completed, error=str(error)[:500])
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--expected-start-boot-id')
    parser.add_argument('--tag')
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps(dict(mode='plan-only', target='root@192.168.1.116', cold_boots=10,
              hardware_acceptance=False, requires='local interactive operator; genuine USB-C disconnect >=10s each cycle; retained outcome, native UART, final SD boundary review',
              maximum_series_seconds=SERIES_SECONDS)))
        return 0
    evidence = None
    try:
        require(sys.stdin.isatty() and sys.stdout.isatty(), 'local interactive TTY required')
        require(args.tag and args.expected_start_boot_id, '--tag and --expected-start-boot-id required')
        warm = warm_module()
        warm.canonical(args.expected_start_boot_id)
        evidence = Evidence(args.tag)
        collect(evidence, args.expected_start_boot_id, warm)
        return 0
    except Exception as error:
        print('Cold boots refused/failed: ' + str(error)[:500], file=sys.stderr)
        return 1
    finally:
        if evidence:
            evidence.close()


if __name__ == '__main__':
    sys.exit(main())

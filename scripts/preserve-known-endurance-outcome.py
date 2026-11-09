#!/usr/bin/env python3
"""Preserve this fixed run's read-only evidence; never grant hardware acceptance."""
import argparse
import base64
import datetime
import inspect
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time

REPO = Path('/home/drew/orange-pi-4-pro')
OUTPUT = REPO / 'out/endurance-outcome-20261109-01'
FINAL_OUTPUT = REPO / 'out/endurance-outcome-20261109-final'
PHASES = ('collector-exit', 'scheduled-final')
UART = REPO / 'out/serial-recovery-20261109/serial-live.log'
BOOT = 'dd8d6f67-d297-4f3b-bc9a-8a41c9742051'
RUN = '59af604a-cf2e-4c8f-9fd5-e26b8283d14c'
COLLECTOR = 'a733-offboard-endurance-collector-20261109.service'
UNITS = ('a733-guest-endurance.service',
         'a733-endurance-host-cpu-release-20261109.service',
         'a733-endurance-release-exit-observer-20261109.service',
         'a733-management-watch-endurance-20261109.service',
         'a733-management-ssh.service')
FILES = ('release-exit-observer.jsonl', 'release-exit-observer.stderr',
         'management-watch.jsonl', 'management-watch.stderr',
         'host-cpu-release.stdout', 'host-cpu-release.stderr')
PROPERTIES = ('Id', 'LoadState', 'ActiveState', 'SubState', 'Result', 'MainPID',
              'ExecMainCode', 'ExecMainStatus', 'ExecMainStartTimestamp',
              'ExecMainExitTimestamp', 'ExecMainStartTimestampMonotonic',
              'ExecMainExitTimestampMonotonic', 'ActiveEnterTimestampMonotonic',
              'InactiveEnterTimestampMonotonic', 'InvocationID', 'NRestarts')
MIB = 1024 * 1024
TOTAL_LIMIT = 32 * MIB


def bounded_command(argv, limit, timeout=15, source=b''):
    """Bound both pipe streams and wall time; preserve partial bytes on failure."""
    result = {'argv': argv, 'returncode': None, 'timed_out': False,
              'truncated': False, 'stdout_b64': '', 'stderr_b64': ''}
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        result['error'] = str(exc)[:500]
        return result
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    def kill():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        for name in output:
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        if source:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, 'stdin')
        else:
            process.stdin.close()
        offset = 0
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result['timed_out'] = True
                break
            for key, _ in selector.select(remaining):
                if key.data == 'stdin':
                    try:
                        offset += os.write(key.fd, source[offset:offset + 4096])
                    except BrokenPipeError:
                        offset = len(source)
                    if offset == len(source):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                maximum = limit if key.data == 'stdout' else 65536
                dest = output[key.data]
                room = maximum - len(dest)
                dest.extend(chunk[:room])
                if len(chunk) > room:
                    result['truncated'] = True
                    kill()
            if result['truncated']:
                break
        if result['timed_out']:
            kill()
        try:
            result['returncode'] = process.wait(timeout=max(.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            result['timed_out'] = True
            kill()
            result['returncode'] = process.wait(timeout=2)
    finally:
        kill()
        process.wait(timeout=2)
        selector.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            if not stream.closed:
                stream.close()
    for name, data in output.items():
        result[name + '_b64'] = base64.b64encode(data).decode('ascii')
    return result


def file_tail(path, limit):
    result = {'path': str(path)}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('not a regular file')
            start = max(0, info.st_size - limit)
            os.lseek(fd, start, os.SEEK_SET)
            data = os.read(fd, limit)
            result.update(size_at_open=info.st_size, offset=start,
                          truncated=start > 0, data_b64=base64.b64encode(data).decode('ascii'))
        finally:
            os.close(fd)
    except (OSError, ValueError) as exc:
        result['error'] = str(exc)[:500]
    return result


def show(units):
    return ['systemctl', 'show', '--no-pager', *units,
            *['--property=' + name for name in PROPERTIES]]


def journal_capture(argv, byte_limit, record_limit):
    result = bounded_command(argv, byte_limit)
    records = base64.b64decode(result['stdout_b64']).count(b'\n')
    result.update(record_limit=record_limit, records_captured=records,
                  possible_truncation=(result['truncated'] or result['timed_out']
                                       or result['returncode'] != 0 or records >= record_limit))
    return result


def remote_snapshot(health_allowed=False):
    boot = file_tail('/proc/sys/kernel/random/boot_id', 128)
    actual = base64.b64decode(boot.get('data_b64', '')).decode('ascii', 'replace').strip()
    result = {'classification': 'unreviewed', 'hardware_acceptance': False,
              'expected_boot_id': BOOT, 'guest_run_id': RUN, 'boot': boot,
              'actual_boot_id': actual, 'boot_matches': actual == BOOT}
    result['units'] = bounded_command(show(UNITS), 65536)
    result['files'] = [file_tail('/run/corrected-kvm/' + name, 512 * MIB // 1024)
                       for name in FILES]
    # Pin journals to the expected boot even if the machine has since rebooted.
    result['guest_journal'] = journal_capture(
        ['journalctl', '--no-pager', '--boot=' + BOOT, '--unit=' + UNITS[0],
         '--output=json', '--lines=10000'], 8 * MIB, 10000)
    result['kernel_journal'] = journal_capture(
        ['journalctl', '--no-pager', '--boot=' + BOOT, '--dmesg', '--output=json',
         '--lines=2000'], MIB, 2000)
    result['dmesg'] = bounded_command(['dmesg', '--color=never'], MIB)
    result['manager_journal'] = journal_capture(
        ['journalctl', '--no-pager', '--boot=' + BOOT, '--output=json', '--lines=2000',
         '_PID=1'], MIB, 2000)
    if health_allowed:
        result['nvme_smart'] = bounded_command(
            ['nvme', 'smart-log', '/dev/nvme0', '--output-format=json'], 65536)
        result['nvme_errors'] = bounded_command(
            ['nvme', 'error-log', '/dev/nvme0', '-e', '1', '--output-format=json'], 65536)
    else:
        result['nvme_health_skipped'] = 'collector exit not independently established locally'
    return result


def remote_source(health_allowed=False):
    imports = ('import base64,json,os,selectors,signal,stat,subprocess,time\n')
    constants = ''.join(name + ' = ' + repr(globals()[name]) + '\n'
                        for name in ('BOOT', 'RUN', 'UNITS', 'FILES', 'PROPERTIES', 'MIB'))
    functions = '\n'.join(inspect.getsource(fn) for fn in
                          (bounded_command, file_tail, show, journal_capture, remote_snapshot))
    return (imports + constants + functions +
            '\nprint(json.dumps(remote_snapshot(' + repr(bool(health_allowed)) +
            '), separators=(",", ":")))\n').encode()


def ssh_argv():
    return ['ssh', '-T', '-i', '/home/drew/.ssh/id_ed25519',
            '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlias=192.168.1.116',
            '-o', 'ConnectTimeout=5', 'root@192.168.1.116', 'python3 -I -B -']


def preserve(phase='collector-exit'):
    output = {'collector-exit': OUTPUT, 'scheduled-final': FINAL_OUTPUT}[phase]
    # Refuse low capacity before creating any evidence. Keep the 256 MiB floor
    # even after the entire maximum-sized capture has been written.
    if shutil.disk_usage(output.parent).free < 256 * MIB + TOTAL_LIMIT:
        raise ValueError('output filesystem needs 256 MiB floor plus capture budget')
    output.mkdir(mode=0o700)
    used = 0
    def save(name, value):
        nonlocal used
        raw = json.dumps(value, separators=(',', ':'), allow_nan=False).encode() + b'\n'
        if used + len(raw) > TOTAL_LIMIT:
            raise ValueError('total artifact byte bound exceeded')
        fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        used += len(raw)
    summary = {'schema': 1, 'classification': 'unreviewed', 'hardware_acceptance': False,
               'expected_boot_id': BOOT, 'guest_run_id': RUN, 'phase': phase,
               'captured_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    collector = bounded_command(show((COLLECTOR,)), 65536)
    props = dict(line.split('=', 1) for line in
                 base64.b64decode(collector['stdout_b64']).decode('utf-8', 'replace').splitlines()
                 if '=' in line)
    status = props.get('ExecMainStatus')
    summary['collector_exit_status'] = status
    summary['collector_exit_meaning'] = {'1': 'failure', '2': 'pending_external_completion'}.get(
        status, 'unknown_or_other')
    save('collector.json', collector)
    save('uart-tail.json', file_tail(UART, MIB))
    health_allowed = (collector['returncode'] == 0 and not collector['truncated']
                      and not collector['timed_out']
                      and props.get('LoadState') == 'loaded'
                      and props.get('ActiveState') in ('inactive', 'failed')
                      and props.get('MainPID') == '0')
    summary['nvme_health_allowed_after_collector_exit'] = health_allowed
    remote = bounded_command(ssh_argv(), 20 * MIB, timeout=150,
                             source=remote_source(health_allowed))
    # Preserve the transport record itself; remote failures and partial JSON are
    # evidence too. No remote content can override the classification above.
    save('remote-transport.json', remote)
    summary['ssh_returncode'] = remote['returncode']
    summary['ssh_timed_out'] = remote['timed_out']
    summary['ssh_truncated'] = remote['truncated']
    save('summary.json', summary)
    fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=PHASES, default='collector-exit')
    args = parser.parse_args(argv)
    try:
        return preserve(args.phase)
    except Exception as exc:
        print('outcome preservation failed: ' + str(exc)[:500], file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

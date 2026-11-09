#!/usr/bin/env python3
"""Prepare, or explicitly request ten guarded clean warm reboots of one A733."""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import uuid

REPO = Path(__file__).resolve().parents[1]
BOOT = 'dd8d6f67-d297-4f3b-bc9a-8a41c9742051'
RUN = '59af604a-cf2e-4c8f-9fd5-e26b8283d14c'
LIMIT = 1024 * 1024
_spec = importlib.util.spec_from_file_location('preserve_endurance', REPO / 'scripts/preserve-known-endurance-outcome.py')
TRANSPORT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(TRANSPORT)

REMOTE = r'''
import fcntl, importlib.util, json, os, pathlib, re, stat, subprocess, sys, uuid
P = pathlib.Path
def require(ok, why):
    if not ok: raise ValueError(why)
def read(path, limit=1048576):
    with open(path, 'rb') as f: data = f.read(limit + 1)
    require(len(data) <= limit, 'oversized read')
    return data.decode('ascii').strip()
def command(argv):
    return subprocess.check_output(argv, timeout=10).decode('ascii')
require(os.geteuid() == 0, 'root required')
namespaces = {}
for name, kind, inode in [('pid', 0x20000000, 0xeffffffc), ('user', 0x10000000, 0xeffffffd), ('mnt', 0x20000, None)]:
    fd = os.open('/proc/self/ns/' + name, os.O_RDONLY | os.O_CLOEXEC)
    try:
        a, b, c = os.fstat(fd), os.stat('/proc/self/ns/' + name), os.stat('/proc/1/ns/' + name)
        require((a.st_dev, a.st_ino) == (b.st_dev, b.st_ino) == (c.st_dev, c.st_ino), 'PID1 namespace differs')
        require(fcntl.ioctl(fd, 0xb703) == kind and (inode is None or a.st_ino == inode), 'initial namespace required')
        namespaces[name] = {'dev': a.st_dev, 'inode': a.st_ino}
    finally: os.close(fd)
path = '/usr/lib/orangepi-hypervisor/host-management.py'
# Verify protected ancestry before importing the installed module.
for parent in reversed(P(path).parents):
    s = parent.lstat()
    require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o7022, 'unsafe module parent')
s = P(path).lstat()
require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1 and not s.st_mode & 0o7022, 'unsafe installed module')
spec = importlib.util.spec_from_file_location('a733_management', path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
config = m.runtime_guard()
require(m.current_address(config) == '192.168.1.116', 'management address differs')
boot = read('/proc/sys/kernel/random/boot_id')
require(str(uuid.UUID(boot)) == boot and (EXPECTED is None or boot == EXPECTED), 'boot ID differs')
require(os.uname().release == '6.6.98-a733-diag', 'kernel differs')
marker = read('/run/a733-state/.a733-state')
require(marker == 'a733-state-v1\n360dfb39-f49f-487e-b307-0bb887c854c5', 'STATE differs')
nv = '/sys/class/block/nvme0n1/'
require(read(nv + 'device/serial') == '134807190FC700149277' and read(nv + 'device/model') == 'SPCC M.2 PCIe SSD' and int(read(nv + 'size')) * 512 == 256060514304, 'NVMe differs')
sd = P('/sys/class/block/mmcblk1')
require(read(sd / 'device/cid') == '035344535530324780716f216e00a8f5' and read(sd / 'device/name') == 'SU02G' and int(read(sd / 'size')) * 512 == 1977614336 and read(sd / 'dev') == '179:0', 'SD differs')
devs = {read(sd / 'dev')} | {read(p / 'dev') for p in sd.iterdir() if (p / 'partition').exists()}
stats = [int(x) for x in read(sd / 'stat').split()]
require(len(stats) == 17 and not any(stats[4:8] + stats[11:17]), 'SD writes/discards/flushes')
# Census every visible task, including namespaces and unshared descriptor tables.
processes = [p for p in P('/proc').iterdir() if p.name.isdigit()]
require(any(p.name == '1' for p in processes), 'incomplete proc census')
for process in processes:
    try:
        tasks = list((process / 'task').iterdir())
    except FileNotFoundError:
        require(not process.exists(), 'live process census failed')
        continue
    require(bool(tasks), 'empty task census')
    for task in tasks:
        try:
            mounts = read(task / 'mountinfo')
            require(not any(line.split()[2] in devs for line in mounts.splitlines()), 'SD mounted')
            for fd in (task / 'fd').iterdir():
                try: target = os.readlink(fd)
                except FileNotFoundError: continue
                require(not re.search(r'kvm[-_:](vm|vcpu)', target), 'active KVM descriptor')
        except FileNotFoundError:
            require(not task.exists(), 'live task census failed')
units = json.loads(command(['systemctl', 'list-units', '--all', '--no-pager', '--output=json', 'a733*']))
observed = []
for row in units:
    name = row['unit']
    if not re.search(r'guest|endurance.*cpu|endurance.*exit-observer', name): continue
    props = dict(line.split('=', 1) for line in command(['systemctl', 'show', name, '-p', 'ActiveState', '-p', 'SubState', '-p', 'MainPID']).splitlines())
    require(props['MainPID'] == '0' and (props['ActiveState'] in ('inactive', 'failed') or (props['ActiveState'] == 'active' and props['SubState'] == 'exited')), 'workload unit running')
    observed.append({'unit': name, **props})
ntp = command(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value']).strip()
require(ntp == 'yes', 'NTP not ready')
require(read('/sys/devices/system/cpu/online') == '0-7', 'eight CPUs not online')
log = command(['dmesg', '--color=never'])
require('All CPU(s) started at EL2' in log and 'VHE mode initialized successfully' in log and 'HYP mode not available' not in log, 'EL2/KVM initialization absent')
fd = os.open('/dev/kvm', os.O_RDWR | os.O_CLOEXEC)
try: api, irq = fcntl.ioctl(fd, 0xae00, 0), fcntl.ioctl(fd, 0xae03, 0)
finally: os.close(fd)
require(api == 12 and irq == 1, 'KVM API/IRQCHIP differs')
require(read('/proc/sys/kernel/random/boot_id') == boot, 'boot changed during probe')
print(json.dumps({'boot_id': boot, 'guards_passed': True, 'hardware_acceptance': False, 'kvm_api': api, 'irqchip': irq, 'ntp': ntp, 'cpus': '0-7', 'sd_stat': stats, 'units': observed, 'el2': True, 'kernel': os.uname().release, 'namespaces': namespaces, 'management_address': '192.168.1.116', 'state_marker': marker, 'nvme': {'serial': read(nv + 'device/serial'), 'model': read(nv + 'device/model'), 'bytes': int(read(nv + 'size')) * 512}, 'sd': {'cid': read(sd / 'device/cid'), 'model': read(sd / 'device/name'), 'bytes': int(read(sd / 'size')) * 512}, 'initialization_log': [line for line in log.splitlines() if 'All CPU(s) started at EL2' in line or 'VHE mode initialized successfully' in line]}), flush=True)
if REBOOT:
    require(read('/proc/sys/kernel/random/boot_id') == EXPECTED, 'boot changed before reboot')
    subprocess.run(['systemctl', 'reboot'], check=True, timeout=20)
'''


def require(ok, why):
    if not ok:
        raise ValueError(why)


def canonical(value):
    require(str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0, 'canonical nonzero UUID required')
    return value


def no_capture_errors(value):
    if isinstance(value, dict):
        require('error' not in value, 'retained capture contains error')
        for child in value.values():
            no_capture_errors(child)
    elif isinstance(value, list):
        for child in value:
            no_capture_errors(child)


def capture_bytes(record, limit, *, journal=False):
    no_capture_errors(record)
    require(isinstance(record, dict) and 'error' not in record, 'capture error')
    require(type(record.get('returncode')) is int and record['returncode'] == 0 and
            record.get('timed_out') is False and record.get('truncated') is False,
            'incomplete retained capture')
    data = base64.b64decode(record['stdout_b64'], validate=True)
    stderr = base64.b64decode(record['stderr_b64'], validate=True)
    require(len(data) <= limit and len(stderr) <= 65536, 'oversized retained capture')
    if journal:
        require(record.get('possible_truncation') is False and
                type(record.get('records_captured')) is int and
                type(record.get('record_limit')) is int and
                0 <= record['records_captured'] < record['record_limit'] and
                record['records_captured'] == data.count(b'\n'), 'incomplete journal')
    return data


def tail_bytes(record, path, limit, *, allow_tail=False):
    no_capture_errors(record)
    require(isinstance(record, dict) and 'error' not in record and
            record.get('path') == str(path), 'missing/wrong retained file')
    size, offset = record['size_at_open'], record['offset']
    require(type(size) is int and type(offset) is int and 0 <= offset <= size,
            'invalid retained file extent')
    data = base64.b64decode(record['data_b64'], validate=True)
    require(len(data) <= limit and len(data) == size - offset and
            record.get('truncated') is (offset > 0) and
            (allow_tail or offset == 0), 'incomplete retained file')
    return data


def json_lines(data, *, tailed=False):
    lines = data.splitlines()
    if tailed:
        lines = lines[1:]  # A tail may start in the middle of one JSON record.
    require(bool(lines), 'empty retained JSON lines')
    result = [json.loads(line) for line in lines]
    require(all(isinstance(row, dict) for row in result), 'invalid retained JSON lines')
    return result


def preserved_snapshot(snap, uart_record):
    no_capture_errors(snap)
    require(snap['expected_boot_id'] == BOOT and snap['actual_boot_id'] == BOOT and
            snap['guest_run_id'] == RUN and snap['boot_matches'] is True,
            'retained host differs')
    units = capture_bytes(snap['units'], 65536).decode('utf-8')
    groups = [dict(line.split('=', 1) for line in group.splitlines() if '=' in line)
              for group in units.strip().split('\n\n')]
    by_id = {group.get('Id'): group for group in groups}
    require(set(TRANSPORT.UNITS) <= by_id.keys(), 'unit capture incomplete')
    for name in TRANSPORT.UNITS[:3]:
        unit = by_id[name]
        require(unit.get('MainPID') == '0' and
                (unit.get('ActiveState') in ('inactive', 'failed') or
                 (unit.get('ActiveState') == 'active' and unit.get('SubState') == 'exited')),
                'retained workload not stopped')
        require(all(re.fullmatch(r'-?[0-9]+', unit.get(field, '')) for field in
                    ('ExecMainCode', 'ExecMainStatus')), 'missing retained exit fields')
    files = snap['files']
    require(isinstance(files, list) and len(files) == len(TRANSPORT.FILES) and
            all(isinstance(record, dict) for record in files), 'file capture incomplete')
    by_path = {record.get('path'): record for record in files}
    require(len(by_path) == len(files), 'duplicate retained files')
    contents = {}
    for name in TRANSPORT.FILES:
        path = '/run/corrected-kvm/' + name
        contents[name] = tail_bytes(by_path[path], path, 512 * 1024,
                                   allow_tail=name == 'management-watch.jsonl')
    observer = json_lines(contents['release-exit-observer.jsonl'])
    require(any(row.get('event') == 'ready' and row.get('unit') == TRANSPORT.UNITS[0]
                for row in observer), 'observer readiness missing')
    require(any(row.get('event') == 'final' and row.get('unit') == TRANSPORT.UNITS[0]
                for row in observer) or bool(contents['release-exit-observer.stderr'].strip()),
            'observer outcome missing')
    watch = by_path['/run/corrected-kvm/management-watch.jsonl']
    json_lines(contents['management-watch.jsonl'], tailed=watch['offset'] > 0)
    guest = json_lines(capture_bytes(snap['guest_journal'], 8 * LIMIT, journal=True))
    require(all(row.get('_BOOT_ID', '').replace('-', '') == BOOT.replace('-', '')
                for row in guest), 'guest journal boot differs')
    require(any(re.search(r'exit|deactivat|failed|stopped|succeeded|finished',
                          str(row.get('MESSAGE', '')), re.I) for row in guest),
            'guest exit journal missing')
    for name in ('kernel_journal', 'manager_journal'):
        rows = json_lines(capture_bytes(snap[name], LIMIT, journal=True))
        require(all(row.get('_BOOT_ID', '').replace('-', '') == BOOT.replace('-', '')
                    for row in rows), 'journal boot differs')
    require(bool(capture_bytes(snap['dmesg'], LIMIT).strip()), 'empty dmesg')
    uart_ok = False
    if uart_record is not None:
        try:
            uart_ok = bool(tail_bytes(uart_record, TRANSPORT.UART, LIMIT, allow_tail=True))
        except (ValueError, KeyError, TypeError):
            pass
    if not uart_ok:
        fd = os.open(TRANSPORT.UART, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            require(stat.S_ISREG(os.fstat(fd).st_mode) and bool(os.read(fd, 1)),
                    'missing readable UART archive')
        finally:
            os.close(fd)


def retained_outcome():
    """Admit retained failed outcomes too, but never failed evidence captures."""
    for name in ('endurance-outcome-20261109-01', 'endurance-outcome-20261109-final'):
        directory = REPO / 'out' / name
        try:
            def load(name):
                path = directory / name
                require(not directory.is_symlink() and not path.is_symlink(), 'symlink preservation')
                with path.open('rb') as stream:
                    raw = stream.read(32 * LIMIT + 1)
                require(len(raw) <= 32 * LIMIT, 'oversized preservation')
                return json.loads(raw)
            summary = load('summary.json')
            no_capture_errors(summary)
            require(summary['expected_boot_id'] == BOOT and summary['guest_run_id'] == RUN and summary['phase'] in TRANSPORT.PHASES, 'wrong retained run')
            collector = load('collector.json')
            remote = load('remote-transport.json')
            props = dict(line.split('=', 1) for line in
                         capture_bytes(collector, 65536).decode().splitlines() if '=' in line)
            require(props.get('LoadState') == 'loaded' and props.get('ActiveState') in ('inactive', 'failed') and props.get('MainPID') == '0', 'collector not stopped')
            snap = json.loads(capture_bytes(remote, 20 * LIMIT))
            try:
                uart = load('uart-tail.json')
            except FileNotFoundError:
                uart = None
            preserved_snapshot(snap, uart)
            return str(directory)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    raise ValueError('native known-run outcome snapshot not retained; no reboot permitted')


def transport(expected=None, reboot=False, timeout=30):
    source = ('EXPECTED = ' + repr(expected) + '\nREBOOT = ' + repr(reboot) + '\n' + REMOTE).encode()
    return TRANSPORT.bounded_command(TRANSPORT.ssh_argv(), LIMIT, timeout=timeout, source=source)


def decode(record):
    require(record['returncode'] == 0 and not record['timed_out'] and not record['truncated'], 'SSH probe failed/timeout/oversized')
    raw = base64.b64decode(record['stdout_b64'], validate=True)
    require(len(raw) <= LIMIT, 'oversized response')
    data = json.loads(raw)
    canonical(data['boot_id'])
    require(data['guards_passed'] is True and data['hardware_acceptance'] is False, 'remote guards failed')
    require(data['kvm_api'] == 12 and data['irqchip'] == 1 and data['ntp'] == 'yes' and data['cpus'] == '0-7' and data['el2'] is True and data['kernel'] == '6.6.98-a733-diag', 'readiness differs')
    stats = data['sd_stat']
    require(len(stats) == 17 and all(type(x) is int and x >= 0 for x in stats) and not any(stats[4:8] + stats[11:17]), 'SD counters differ')
    return data


class Evidence:
    def __init__(self, tag):
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', tag), 'invalid tag')
        self.path = REPO / 'out' / ('warm-boots-' + tag)
        require(not self.path.parent.is_symlink() and self.path.parent.is_dir(), 'out must be a real existing directory')
        self.path.mkdir(mode=0o700)
        self.fd = os.open(self.path / 'events.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        for path in (self.path, self.path.parent):
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(fd)
            finally: os.close(fd)

    def save(self, event, **fields):
        raw = (json.dumps({'event': event, 'hardware_acceptance': False, **fields}, allow_nan=False) + '\n').encode()
        offset = 0
        while offset < len(raw):
            count = os.write(self.fd, raw[offset:])
            require(count > 0, 'zero evidence write')
            offset += count
        os.fsync(self.fd)

    def close(self):
        os.close(self.fd)


def collect(evidence, start, probe=transport, preserved=retained_outcome, clock=time.monotonic, sleep=time.sleep):
    current, seen, completed = canonical(start), {start}, 0
    try:
        retained = preserved()
        evidence.save('preservation', retained=retained)
        for cycle in range(1, 11):
            preserved()  # No subsequent action if retained evidence disappears.
            raw = probe(current)
            evidence.save('snapshot', cycle=cycle, transport=raw)
            require(decode(raw)['boot_id'] == current, 'pre-reboot boot ID differs')
            evidence.save('requested', cycle=cycle, boot_id=current, command=['systemctl', 'reboot'])
            print(f'Warm boot {cycle}/10: requesting clean reboot from {current}', flush=True)
            deadline = clock() + 300
            raw = probe(current, reboot=True)
            evidence.save('request-response', cycle=cycle, transport=raw)
            require(decode(raw)['boot_id'] == current, 'reboot admission differs')
            while True:
                require(clock() < deadline, '300-second new-boot deadline exceeded')
                raw = probe(timeout=min(30, max(.01, deadline - clock())))
                evidence.save('poll', cycle=cycle, transport=raw)
                try:
                    data = decode(raw)
                except (ValueError, KeyError, TypeError):
                    data = None
                if data and data['boot_id'] != current:
                    require(data['boot_id'] not in seen, 'reused boot ID; stopping')
                    require(clock() <= deadline, 'new boot observed after deadline')
                    current = data['boot_id']
                    seen.add(current)
                    completed += 1
                    evidence.save('observed', cycle=cycle, boot_id=current)
                    print(f'Warm boot {cycle}/10: validated {current}', flush=True)
                    break
                print(f'Warm boot {cycle}/10: waiting for distinct ready boot', flush=True)
                sleep(min(5, max(0, deadline - clock())))
        evidence.save('complete', completed=completed, boot_ids=sorted(seen), classification='coordinator-review-required')
        return completed
    except Exception as error:
        evidence.save('failed-or-refused', completed=completed, error=str(error)[:500])
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--expected-start-boot-id')
    parser.add_argument('--tag', default=time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({'mode': 'plan-only', 'target': 'root@192.168.1.116', 'clean_reboots': 10, 'hardware_acceptance': False, 'requires': 'retained native outcome, idle host and canonical --expected-start-boot-id', 'cold_boots': 'manual and unperformed'}))
        return 0
    evidence = None
    try:
        require(args.expected_start_boot_id is not None, '--expected-start-boot-id required')
        canonical(args.expected_start_boot_id)
        evidence = Evidence(args.tag)
        collect(evidence, args.expected_start_boot_id)
        return 0
    except Exception as error:
        print('Warm boots refused/failed: ' + str(error)[:500], file=sys.stderr)
        return 1
    finally:
        if evidence:
            evidence.close()


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Prepare, or explicitly request ten guarded clean warm reboots of one A733."""
import argparse
import base64
import importlib.util
import hashlib
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
STOP_RESULT = 'docs/evidence/eight-hour-endurance-result-20261109.json'
STOP_RESULT_SHA256 = '7168f8d1604c4aa375c862d045c07fd6b37be599c73196743b6ef63e0feaf679'
STOP_BASE = 'out/endurance-shortened-20261109-01/'
PRESTOP_PIN = (17939, '07fbbd203dbabf2858bc9807f1e3fab2c103045c081fe11e212493818995c7a1')
COLLECTOR = 'a733-offboard-endurance-collector-20261109.service'
_spec = importlib.util.spec_from_file_location('preserve_endurance', REPO / 'scripts/preserve-known-endurance-outcome.py')
TRANSPORT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(TRANSPORT)

# Fixed final normalized distribution; authenticated manifest and root-selection.py
# serialize(boot_selection) were checked when preparing this finite collector.
RELEASE_REFUSAL_CODE = 78
RELEASE_BINDING = {
    'manifest_sha256': '3cbe28d6154d1bdeb8d364bd0f8bf82b61fd40af7be27fbd49695771c4fad08f',
    'gzip_sha256': '3bc02f46d272dcac591d435fff2b1bc98d4fb048569fb2120746145929a8ad64',
    'raw_sha256': '1fa9144d7a1269066c91a94bfb105a9ac4e4083d4707dd6791e8ac2f26902cd9',
    'slot': 'A', 'root_device': '/dev/nvme0n1p2', 'root_id': '259:2',
    'boot_route': 'known-SU02G-SD; independent firmware audit and serial review required',
}
SELECTED_BYTES = (b'SCHEMA=1\nSLOT=A\nROOT_UUID=a7330000-0000-4000-8000-00000000000a\n'
                  b'ROOT_MARKER_SHA256=286494073ce3a0b69443f96389dbbce11ab015705f7e9f05c7a7cf06503da866\n'
                  b'KERNEL_RELEASE=6.6.98-a733-diag\n')
RELEASE_FILES = {
    '/usr/lib/orangepi-hypervisor/host-management.py': '66fbeaf8a939ad073f183b88a3910bfd62794ffa9c485f2c1f1adac5ab5f8728',
    '/usr/lib/orangepi-hypervisor/payload.json': '286494073ce3a0b69443f96389dbbce11ab015705f7e9f05c7a7cf06503da866',
    '/run/a733-state/boot/selected.conf': '3c76b96c328b4508ff407c8594352e04389664e97ced6f0a09284cade5bd027b',
}

REMOTE = ('RELEASE_BINDING = ' + repr(RELEASE_BINDING) + '\nRELEASE_FILES = ' + repr(RELEASE_FILES) +
          '\nSELECTED_BYTES = ' + repr(SELECTED_BYTES) + '\n') + r'''
import fcntl, hashlib, importlib.util, json, os, pathlib, re, stat, subprocess, sys, uuid
P = pathlib.Path
def require(ok, why):
    if not ok: raise ValueError(why)
def release_require(ok, why):
    if not ok:
        print('Final release refused: ' + why[:300], file=sys.stderr, flush=True)
        raise SystemExit(78)
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
def stamp(info):
    return tuple(getattr(info, field) for field in ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))
def capture_release_bytes(path, limit=1048576):
    # Root-owned ancestry and held metadata bind the hash to captured bytes.
    for parent in reversed(P(path).parents):
        info = parent.lstat()
        release_require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o7022, 'unsafe release parent')
    before = P(path).lstat()
    release_require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1 and not before.st_mode & 0o7022, 'unsafe release file')
    with open(path, 'rb') as stream:
        held = os.fstat(stream.fileno())
        release_require(stamp(held) == stamp(before), 'release file changed before read')
        data = stream.read(limit + 1)
        release_require(len(data) <= limit and stamp(os.fstat(stream.fileno())) == stamp(held), 'release file changed during read')
    release_require(stamp(P(path).lstat()) == stamp(before), 'release file changed after read')
    return data
def protected_bytes(path, limit=1048576):
    try:
        return capture_release_bytes(path, limit)
    except OSError:
        release_require(False, 'release file unavailable: ' + path)
path = '/usr/lib/orangepi-hypervisor/host-management.py'
module_bytes = protected_bytes(path)
file_hashes = {path: hashlib.sha256(module_bytes).hexdigest()}
release_require(file_hashes[path] == RELEASE_FILES[path], 'management module release differs')
spec = importlib.util.spec_from_file_location('a733_management', path)
m = importlib.util.module_from_spec(spec)
# Execute the captured authenticated bytes, without reopening the module path.
exec(compile(module_bytes, path, 'exec'), m.__dict__)
# Inspect the actual root before runtime_guard so a wrong final slot/device/RO
# policy is terminal even if that guard would also reject the mount layout.
root_rows = m.mount_table(read('/proc/self/mountinfo')).get('/', [])
release_require(len(root_rows) == 1, 'final A root mount missing or ambiguous')
root = root_rows[0]
release_require(root['source'] == RELEASE_BINDING['root_device'] and root['id'] == RELEASE_BINDING['root_id'] and root['root'] == '/' and 'ro' in root['flags'] and 'rw' not in root['flags'], 'final A root device differs')
for path in RELEASE_FILES:
    if path in file_hashes: continue
    data = protected_bytes(path, 4096 if path.endswith('selected.conf') else 1048576)
    file_hashes[path] = hashlib.sha256(data).hexdigest()
    release_require(file_hashes[path] == RELEASE_FILES[path], 'release file differs: ' + path)
    if path.endswith('selected.conf'):
        release_require(data == SELECTED_BYTES, 'final A selector differs')
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
print(json.dumps({'release_binding': RELEASE_BINDING, 'file_hashes': file_hashes, 'root_device': root['source'], 'root_id': root['id'], 'boot_id': boot, 'guards_passed': True, 'hardware_acceptance': False, 'kvm_api': api, 'irqchip': irq, 'ntp': ntp, 'cpus': '0-7', 'sd_stat': stats, 'units': observed, 'el2': True, 'kernel': os.uname().release, 'namespaces': namespaces, 'management_address': '192.168.1.116', 'state_marker': marker, 'nvme': {'serial': read(nv + 'device/serial'), 'model': read(nv + 'device/model'), 'bytes': int(read(nv + 'size')) * 512}, 'sd': {'cid': read(sd / 'device/cid'), 'model': read(sd / 'device/name'), 'bytes': int(read(sd / 'size')) * 512}, 'initialization_log': [line for line in log.splitlines() if 'All CPU(s) started at EL2' in line or 'VHE mode initialized successfully' in line]}), flush=True)
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


def retained_read(path, limit=32 * LIMIT):
    """Read regular, stable local evidence without following symlink ancestry."""
    path = Path(path)
    for parent in path.parents:
        require(not parent.is_symlink(), 'symlink preservation parent')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                before.st_size <= limit, 'unsafe/oversized preservation')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) <= limit and len(raw) == before.st_size and
                all(getattr(before, k) == getattr(after, k) == getattr(path.lstat(), k)
                    for k in fields), 'preservation changed during read')
        return raw
    finally:
        os.close(fd)


def operator_stop_proof(directory, captures):
    """Authenticate only the reviewed historical transient-unit GC exception."""
    raw = retained_read(REPO / STOP_RESULT, LIMIT)
    require(hashlib.sha256(raw).hexdigest() == STOP_RESULT_SHA256, 'stop result hash differs')
    result = json.loads(raw)
    require(result['host_boot_id'] == BOOT and result['guest_run_id'] == RUN and
            result['operator_stop'] is True, 'stop proof run differs')
    records = result['records']
    pins = {r['path']: r for r in records}
    require(len(pins) == len(records), 'duplicate stop proof records')

    def pinned(relative, supplied=None):
        pin = pins[relative]
        data = retained_read(REPO / relative, LIMIT * 32) if supplied is None else supplied
        require(len(data) == pin['bytes'] and hashlib.sha256(data).hexdigest() == pin['sha256'],
                'stop proof record hash differs')
        return data

    require(set(captures) == {'summary.json', 'collector.json', 'remote-transport.json',
                              'uart-tail.json'}, 'manual-stop raw captures incomplete')
    for name, data in captures.items():
        pinned(str(directory.relative_to(REPO) / name), data)
    policy = json.loads(pinned(STOP_BASE + 'policy-change.json'))
    require(policy['termination_classification'] ==
            'planned operator stop; guest-requested clean shutdown not established' and
            policy['hardware_acceptance'] is False, 'operator stop intent absent')
    native_raw = retained_read(REPO / STOP_BASE / 'native-prestop-units.txt', LIMIT)
    require((len(native_raw), hashlib.sha256(native_raw).hexdigest()) == PRESTOP_PIN,
            'native prestop hash differs')
    native = [dict(line.split('=', 1) for line in group.splitlines() if '=' in line)
              for group in native_raw.decode().strip().split('\n\n')]
    collectors = [row for row in native if row.get('Id') == COLLECTOR]
    require(len(collectors) == 1, 'native prestop collector absent/duplicate')
    collector = collectors[0]
    require(all(collector.get(k) == v for k, v in dict(LoadState='loaded',
            ActiveState='active', SubState='running', MainPID='3869075',
            InvocationID='e14e2bae19e04a9baa203eb1b2480627').items()) and
            '--boot-id ' + BOOT in collector['ExecStart'], 'native prestop collector differs')
    events = json_lines(pinned(STOP_BASE + 'native-stop-events.jsonl'))
    require([row.get('operation') for row in events] ==
            ['stop-attempt', 'stop-result', 'stop-attempt', 'stop-result'] and
            events[2]['unit'] == events[3]['unit'] == COLLECTOR and
            events[3]['returncode'] == 0 and type(events[3]['returncode']) is int and
            events[3]['stderr'] == '' and all(events[3]['state'].get(k) == v for k, v in
            dict(MainPID='0', ActiveState='inactive', SubState='dead').items()),
            'native collector stop incomplete')
    prestop = json.loads(pinned(STOP_BASE + 'board-prestop-03.json'))
    require(prestop['boot_id'] == BOOT, 'board stop boot differs')
    board = json_lines(pinned(STOP_BASE + 'board-stop-events.jsonl'))
    require([r.get('event') for r in board] == ['operator-stop-attempt',
            'operator-stop-result', 'guest-raw-exit-before-observer-release',
            'operator-stop-attempt', 'operator-stop-result', 'observer-after-guest-exit',
            'management-watch-state', 'management-watch-stop', 'all-terminal'],
            'board stop sequence incomplete')
    for attempt, stopped, unit, pid, status in ((board[0], board[1], TRANSPORT.UNITS[0], '2153', '0'),
                                               (board[3], board[4], TRANSPORT.UNITS[1], '2169', '2')):
        require(attempt['unit'] == stopped['unit'] == unit and
                attempt['before']['LoadState'] == 'loaded' and
                attempt['before']['MainPID'] == pid and stopped['returncode'] == 0 and
                type(stopped['returncode']) is int and stopped['stderr'] == '' and
                stopped['after']['MainPID'] == '0' and stopped['after']['ExecMainStatus'] == status,
                'board workload stop incomplete')
    require(board[2]['state'] == board[1]['after'] and
            board[2]['guest_requested_shutdown'] is False and
            board[5]['state']['MainPID'] == '0' and board[7]['returncode'] == 0 and
            all(row['MainPID'] == '0' for row in board[8]['units']), 'board exit receipt incomplete')


def retained_outcome():
    """Admit retained failed outcomes too, but never failed evidence captures."""
    for name in ('endurance-outcome-20261109-01', 'endurance-outcome-20261109-final'):
        directory = REPO / 'out' / name
        try:
            def load(name):
                path = directory / name
                raw = retained_read(path)
                captures[name] = raw
                return json.loads(raw)
            captures = {}
            summary = load('summary.json')
            no_capture_errors(summary)
            require(summary['expected_boot_id'] == BOOT and summary['guest_run_id'] == RUN and summary['phase'] in TRANSPORT.PHASES, 'wrong retained run')
            collector = load('collector.json')
            remote = load('remote-transport.json')
            props = dict(line.split('=', 1) for line in
                         capture_bytes(collector, 65536).decode().splitlines() if '=' in line)
            collected = props.get('LoadState') == 'not-found'
            if collected:
                require(all(props.get(k) == v for k, v in dict(Id=COLLECTOR,
                        ActiveState='inactive', SubState='dead', MainPID='0', Result='success',
                        ExecMainCode='0', ExecMainStatus='0', InvocationID='').items()),
                        'unsupported garbage-collected collector state')
            else:
                require(props.get('LoadState') == 'loaded' and props.get('ActiveState') in ('inactive', 'failed') and props.get('MainPID') == '0', 'collector not stopped')
            snap = json.loads(capture_bytes(remote, 20 * LIMIT))
            try:
                uart = load('uart-tail.json')
            except FileNotFoundError:
                uart = None
            preserved_snapshot(snap, uart)
            if collected:
                operator_stop_proof(directory, captures)
            return str(directory)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    raise ValueError('native known-run outcome snapshot not retained; no reboot permitted')


def transport(expected=None, reboot=False, timeout=30):
    source = ('EXPECTED = ' + repr(expected) + '\nREBOOT = ' + repr(reboot) + '\n' + REMOTE).encode()
    return TRANSPORT.bounded_command(TRANSPORT.ssh_argv(), LIMIT, timeout=timeout, source=source)


class TerminalReleaseRefusal(ValueError):
    pass


def decode(record):
    if record.get('returncode') == RELEASE_REFUSAL_CODE:
        raise TerminalReleaseRefusal('remote final release refused (exit 78)')
    require(record['returncode'] == 0 and not record['timed_out'] and not record['truncated'], 'SSH probe failed/timeout/oversized')
    raw = base64.b64decode(record['stdout_b64'], validate=True)
    require(len(raw) <= LIMIT, 'oversized response')
    data = json.loads(raw)
    canonical(data['boot_id'])
    require(data['guards_passed'] is True and data['hardware_acceptance'] is False, 'remote guards failed')
    require(data['kvm_api'] == 12 and data['irqchip'] == 1 and data['ntp'] == 'yes' and data['cpus'] == '0-7' and data['el2'] is True and data['kernel'] == '6.6.98-a733-diag', 'readiness differs')
    require(data.get('release_binding') == RELEASE_BINDING and data.get('file_hashes') == RELEASE_FILES and
            data.get('root_device') == RELEASE_BINDING['root_device'] and
            data.get('root_id') == RELEASE_BINDING['root_id'], 'final release binding differs')
    stats = data['sd_stat']
    require(len(stats) == 17 and all(type(x) is int and x >= 0 for x in stats) and not any(stats[4:8] + stats[11:17]), 'SD counters differ')
    return data


def decode_reboot_admission(record):
    """Validate guards after the known SSH close without claiming command exit."""
    if record.get('returncode') != 255:
        return decode(record), False
    require(record.get('timed_out') is False and record.get('truncated') is False,
            'reboot admission SSH timeout/oversized')
    require(base64.b64decode(record['stderr_b64'], validate=True) ==
            b'Connection to 192.168.1.116 closed by remote host.\r\n',
            'unexpected reboot admission SSH close')
    # Normalize a copy solely to reuse JSON/guard validation. The original
    # returncode remains 255 in retained evidence; systemctl's exit is unknown.
    data = decode({**record, 'returncode': 0})
    require(data['management_address'] == '192.168.1.116' and
            data['state_marker'] == 'a733-state-v1\n360dfb39-f49f-487e-b307-0bb887c854c5' and
            data['nvme'] == {'serial': '134807190FC700149277', 'model': 'SPCC M.2 PCIe SSD', 'bytes': 256060514304} and
            data['sd'] == {'cid': '035344535530324780716f216e00a8f5', 'model': 'SU02G', 'bytes': 1977614336},
            'reboot admission identity differs')
    namespaces = data['namespaces']
    require(set(namespaces) == {'pid', 'user', 'mnt'} and
            namespaces['pid'] == {'dev': 4, 'inode': 0xeffffffc} and
            namespaces['user'] == {'dev': 4, 'inode': 0xeffffffd} and
            set(namespaces['mnt']) == {'dev', 'inode'} and
            namespaces['mnt']['dev'] == 4 and
            type(namespaces['mnt']['inode']) is int and namespaces['mnt']['inode'] > 0,
            'reboot admission namespace proof differs')
    require(type(data['units']) is list and all(
            set(row) == {'unit', 'MainPID', 'ActiveState', 'SubState'} and
            row['MainPID'] == '0' and
            (row['ActiveState'] in ('inactive', 'failed') or
             (row['ActiveState'] == 'active' and row['SubState'] == 'exited')) and
            isinstance(row['unit'], str) for row in data['units']),
            'reboot admission workload running')
    log = data['initialization_log']
    require(type(log) is list and all(isinstance(line, str) for line in log) and
            any('All CPU(s) started at EL2' in line for line in log) and
            any('VHE mode initialized successfully' in line for line in log) and
            not any('HYP mode not available' in line for line in log),
            'reboot admission initialization log differs')
    return data, True


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
            admission, transport_closed = decode_reboot_admission(raw)
            require(admission['boot_id'] == current, 'reboot admission differs')
            if transport_closed:
                evidence.save('reboot-admission-transport-closed', cycle=cycle, boot_id=current,
                              classification='transport-closed-before-command-exit',
                              command_exit='UNKNOWN', completed=completed)
            while True:
                require(clock() < deadline, '300-second new-boot deadline exceeded')
                raw = probe(timeout=min(30, max(.01, deadline - clock())))
                evidence.save('poll', cycle=cycle, transport=raw)
                try:
                    data = decode(raw)
                except (ValueError, KeyError, TypeError) as error:
                    if isinstance(error, TerminalReleaseRefusal):
                        raise
                    # Transport failures while booting can recover; a successful
                    # JSON response with a release mismatch must stop this series.
                    if raw.get('returncode') == 0 and not raw.get('timed_out') and not raw.get('truncated'):
                        raise
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

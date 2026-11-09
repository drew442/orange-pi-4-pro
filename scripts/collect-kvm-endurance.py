#!/usr/bin/env python3
"""Collect read-only evidence. This program never grants hardware acceptance."""
import argparse
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import time
import uuid

PAYLOAD_SIZE = 1024 * 1024
MAX_METADATA = 65536
MAX_FRAME = PAYLOAD_SIZE + MAX_METADATA + 64
ZERO_DISK_SHA = hashlib.sha256(bytes(16 * PAYLOAD_SIZE)).hexdigest()
ZERO_PAYLOAD_SHA = hashlib.sha256(bytes(PAYLOAD_SIZE)).hexdigest()
DURATIONS = {'smoke': 60, 'release': 28800}
PROFILE = 'a733-kvm-endurance-v1'
PROGRESS_TIMEOUT = 120
MEMORY_MAX = '536870912'
SD_IDENTITY = {'device': 'mmcblk1', 'major_minor': '179:0',
               'cid': '035344535530324780716f216e00a8f5',
               'model': 'SU02G', 'size_bytes': 1977614336}

# Executed with Python isolated mode on the host, with no user-controlled source.
REMOTE = 'EXPECTED_SD = ' + repr(SD_IDENTITY) + '\n' + r'''
import glob, json, os, pathlib, re, subprocess, sys, urllib.request
P = pathlib.Path
def read(path):
    return P(path).read_text()[:32768].strip()
def checked_sd_identity(base):
    identity = {'device': P(base).name, 'major_minor': read(base + '/dev'),
        'cid': read(base + '/device/cid'), 'model': read(base + '/device/name'),
        'size_bytes': int(read(base + '/size')) * 512}
    if identity != EXPECTED_SD:
        raise ValueError('SD sysfs identity mismatch')
    return identity
def command(argv):
    return subprocess.check_output(argv, timeout=5).decode()
def fetch(path, limit):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise ValueError('HTTP redirect forbidden')
    opener.add_handler(NoRedirect())
    with opener.open('http://127.0.0.1:18081/' + path, timeout=5) as response:
        if response.status != 200:
            raise ValueError('HTTP status')
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError('HTTP size')
        return body
unit = json.loads(sys.argv[1])['unit']
props = dict(line.split('=', 1) for line in command(['systemctl', 'show', unit,
    '-p', 'ActiveState', '-p', 'SubState', '-p', 'MainPID', '-p', 'MemoryMax']).splitlines())
m = {'boot_id': read('/proc/sys/kernel/random/boot_id'), 'unit': props,
     'proc_stat': read('/proc/stat'), 'meminfo': read('/proc/meminfo'),
     'smart': {'state': 'not_proved', 'reason': 'SMART ioctl not implemented'}}
m['thermal'] = []
for zone in sorted(glob.glob('/sys/class/thermal/thermal_zone*')):
    trips = []
    for kind in glob.glob(zone + '/trip_point_*_type'):
        if read(kind) == 'critical':
            trips.append(int(read(kind[:-5] + '_temp')))
    m['thermal'].append({'zone': zone, 'type': read(zone + '/type'),
                         'temp': int(read(zone + '/temp')), 'critical': trips})
m['cpufreq'] = {p: read(p) for p in glob.glob('/sys/devices/system/cpu/cpufreq/policy*/scaling_cur_freq')}
m['network'] = []
for net in glob.glob('/sys/class/net/*'):
    if read(net + '/address').lower() == '10:f4:ae:90:5e:57':
        m['network'].append({'name': P(net).name, 'speed': read(net + '/speed'),
            'duplex': read(net + '/duplex'), 'rx_bytes': int(read(net + '/statistics/rx_bytes')),
            'tx_bytes': int(read(net + '/statistics/tx_bytes'))})
m['blocks'] = {}
mounts = read('/proc/self/mountinfo').splitlines()
for name in ('mmcblk1', 'nvme0n1'):
    base = '/sys/class/block/' + name
    identity = checked_sd_identity(base) if name == 'mmcblk1' else None
    devs = [read(base + '/dev')] + [read(p) for p in glob.glob(base + '/' + name + '*/dev')]
    m['blocks'][name] = {'stat': [int(x) for x in read(base + '/stat').split()],
        'mounted': any(line.split()[2] in devs for line in mounts)}
    if identity is not None:
        m['blocks'][name]['identity'] = identity
# Linux block stat fields: Documentation/block/stat.rst (writes completed and sectors written).
log = command(['dmesg', '--color=never'])
m['nvme_errors'] = sorted(set(line for line in log.splitlines() if 'nvme' in line.lower()
    and re.search(r'error|timeout|reset|abort|failed|critical', line, re.I)))
pid = int(props['MainPID'])
if props['ActiveState'] != 'active' or pid <= 0:
    m['endpoint_state'] = 'pending_external_completion'
    payload = b''
else:
    base = '/proc/' + str(pid)
    m['process'] = {'pid': pid, 'status': read(base + '/status'),
        'exe': os.readlink(base + '/exe'), 'argv': P(base + '/cmdline').read_bytes().decode().split('\0')[:-1],
        'fds': [os.readlink(p) for p in glob.glob(base + '/fd/*')]}
    m['guest'] = json.loads(fetch('cgi-bin/status', 8192))
    payload = fetch('payload.bin', 1048576)
    if len(payload) != 1048576:
        raise ValueError('short payload')
    m['endpoint_state'] = 'running'
data = json.dumps(m, allow_nan=False, separators=(',', ':')).encode()
if len(data) > 65536:
    raise ValueError('metadata too large')
sys.stdout.buffer.write(('ENDURANCE1 %d %d\n' % (len(payload), len(data))).encode() + payload + data)
'''


def finite_tree(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('nonfinite JSON')
    if isinstance(value, dict):
        for item in value.values():
            finite_tree(item)
    if isinstance(value, list):
        for item in value:
            finite_tree(item)


def decode_frame(raw):
    if len(raw) > MAX_FRAME:
        raise ValueError('oversized frame')
    header, sep, body = raw.partition(b'\n')
    match = re.fullmatch(rb'ENDURANCE1 (0|1048576) ([1-9][0-9]{0,4})', header)
    if not sep or not match:
        raise ValueError('malformed frame header')
    payload_len, meta_len = map(int, match.groups())
    if meta_len > MAX_METADATA or len(body) != payload_len + meta_len:
        raise ValueError('frame length')
    payload_sha = hashlib.sha256(body[:payload_len]).hexdigest() if payload_len else None
    data = json.loads(body[payload_len:])
    if not isinstance(data, dict):
        raise ValueError('metadata object required')
    finite_tree(data)
    data['payload_bytes'] = payload_len
    data['payload_sha256'] = payload_sha
    return data


def bounded_ssh(argv, source, timeout=30):
    # Pipe stderr to /dev/null so authentication details cannot enter evidence.
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    selector = None
    try:
        process.stdin.write(source.encode())
        process.stdin.close()
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline, output = time.monotonic() + timeout, bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise ValueError('SSH probe timeout')
            chunk = os.read(process.stdout.fileno(), min(65536, MAX_FRAME + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > MAX_FRAME:
                raise ValueError('SSH response oversized')
        if process.wait(timeout=max(0.01, deadline - time.monotonic())):
            raise ValueError('SSH probe failed')
        return decode_frame(output)
    finally:
        if selector is not None:
            selector.close()
        if process.poll() is None:
            process.kill()
        process.wait()
        if process.stdout:
            process.stdout.close()
        if not process.stdin.closed:
            process.stdin.close()


def number(data, key, minimum=0):
    value = data[key]
    if type(value) is not int or value < minimum:
        raise ValueError('invalid ' + key)
    return value


def validate_process(proc):
    fields = dict(line.split(':', 1) for line in proc['status'].splitlines() if ':' in line)
    for key in ('Uid', 'Gid'):
        if fields[key].split() != ['60002'] * 4:
            raise ValueError('guest credentials')
    for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
        if int(fields[key], 16):
            raise ValueError('guest capabilities')
    if fields['NoNewPrivs'].strip() != '1':
        raise ValueError('guest NoNewPrivs')
    if Path(proc['exe']).name != 'qemu-system-aarch64':
        raise ValueError('guest executable')
    argv = proc['argv']
    def option(key):
        if argv.count(key) != 1:
            raise ValueError('guest option ' + key)
        return argv[argv.index(key) + 1]
    if option('-accel') != 'kvm' or option('-cpu') != 'host':
        raise ValueError('guest acceleration')
    if option('-machine') not in ('virt,gic-version=host', 'virt,accel=kvm,gic-version=host'):
        raise ValueError('guest machine')
    if option('-smp') != '4' or option('-m') not in ('256', '256M'):
        raise ValueError('guest limits')
    if 'tcg' in ' '.join(argv).lower():
        raise ValueError('TCG forbidden')
    fds = proc['fds']
    if sum('kvm-vcpu' in fd for fd in fds) != 4 or not any('kvm-vm' in fd for fd in fds) or not any(fd == 'anon_inode:kvm-arm-vgic-v3' for fd in fds):
        raise ValueError('VM/vCPU/VGIC descriptor evidence missing')


def validate(data, boot_id, mode, baseline=None, previous=None):
    if data['unit'].get('MemoryMax') != MEMORY_MAX:
        raise ValueError('guest unit MemoryMax must be 536870912')
    if data['boot_id'] != boot_id:
        raise ValueError('host boot changed')
    sd = data['blocks']['mmcblk1']
    if sd.get('identity') != SD_IDENTITY:
        raise ValueError('SD sysfs identity mismatch')
    if sd['mounted'] or len(sd['stat']) < 11 or any(sd['stat'][4:8]) or any(sd['stat'][11:]):
        raise ValueError('SD mounted or written')
    if len(data['network']) != 1 or not data['thermal']:
        raise ValueError('network/thermal evidence missing')
    if previous and any(data['network'][0][key] < previous['network'][0][key] for key in ('rx_bytes', 'tx_bytes')):
        raise ValueError('physical network counters decreased')
    for zone in data['thermal']:
        if not zone['critical'] or any(zone['temp'] >= trip for trip in zone['critical']):
            raise ValueError('thermal critical limit missing/exceeded')
    data['nvme_kernel_error_assessment'] = ('coordinator_review_required'
        if data['nvme_errors'] else 'no_errors_observed')
    if baseline is not None and set(data['nvme_errors']) != set(baseline['nvme_errors']):
        raise ValueError('NVMe kernel error evidence changed')
    if data['endpoint_state'] != 'running':
        return 'pending_external_completion'
    validate_process(data['process'])
    guest = data['guest']
    if guest['schema'] != 1 or guest['profile'] != PROFILE or guest['mode'] != mode:
        raise ValueError('guest profile/mode')
    if str(uuid.UUID(guest['run_id'])) != guest['run_id']:
        raise ValueError('guest run ID')
    if guest['required_seconds'] != DURATIONS[mode] or guest['state'] not in ('running', 'finishing'):
        raise ValueError('guest state/duration')
    for key in ('elapsed_seconds', 'cycles', 'disk_counter'):
        number(guest, key)
    if guest['cpu_workers'] != 4 or guest['cpu_workers_alive'] != 4:
        raise ValueError('guest CPU workers')
    if guest['disk_sha256'] != ZERO_DISK_SHA or data['payload_bytes'] != PAYLOAD_SIZE or data['payload_sha256'] != ZERO_PAYLOAD_SHA:
        raise ValueError('guest data hash')
    if previous and 'guest' in previous:
        old = previous['guest']
        if guest['run_id'] != old['run_id']:
            raise ValueError('guest reboot')
        if any(guest[key] < old[key] for key in ('elapsed_seconds', 'cycles', 'disk_counter')):
            raise ValueError('guest counters decreased')
    return 'readonly_evidence'


class ProgressWatchdog:
    """Require both guest clock and workload cycles to advance in local time."""
    def __init__(self):
        self.last_values = {}
        self.last_progress = {}

    def check(self, data, now):
        ages = {}
        # disk_counter is a per-run identity, not a cycle progress counter.
        for key in ('elapsed_seconds', 'cycles'):
            value = data['guest'][key]
            if key not in self.last_values or value > self.last_values[key]:
                self.last_progress[key] = now
            self.last_values[key] = value
            ages[key] = round(now - self.last_progress[key], 3)
        data['collector_progress'] = {'timeout_seconds': PROGRESS_TIMEOUT,
                                      'seconds_since_advance': ages}
        if any(now - advanced > PROGRESS_TIMEOUT for advanced in self.last_progress.values()):
            raise ValueError('guest progress stalled beyond 120 collector-monotonic seconds')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--key', required=True, type=Path)
    parser.add_argument('--boot-id', required=True)
    parser.add_argument('--mode', choices=DURATIONS, required=True)
    parser.add_argument('--guest-name', choices=['endurance'], default='endurance')
    parser.add_argument('--unit', default='a733-guest-endurance.service')
    parser.add_argument('--interval', type=int, choices=[30], default=30)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    ipaddress.IPv4Address(args.host)
    if str(uuid.UUID(args.boot_id)) != args.boot_id:
        parser.error('canonical boot UUID required')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}\.service', args.unit):
        parser.error('invalid service name')
    if not args.key.is_file():
        parser.error('SSH key missing')
    if not args.output.parent.is_dir() or shutil.disk_usage(args.output.parent).free < 256 * PAYLOAD_SIZE:
        parser.error('output parent needs 256 MiB free')
    # Fresh private directory prevents overwrites and symlink traversal at destination.
    args.output.mkdir(mode=0o700)
    evidence = args.output / 'samples.jsonl'
    remote_arg = json.dumps({'unit': args.unit}, separators=(',', ':'))
    # shell quote the fixed JSON after validation; no caller data becomes source code.
    command = "python3 -I -B - '" + remote_arg + "'"
    argv = ['ssh', '-T', '-i', str(args.key.resolve()), '-o', 'IdentitiesOnly=yes',
        '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlias=192.168.1.116',
        '-o', 'ConnectTimeout=10', 'root@' + args.host, command]
    started = time.monotonic()
    baseline = previous = None
    watchdog = ProgressWatchdog()
    maximum = DURATIONS[args.mode] // args.interval + 3
    with evidence.open('x', encoding='utf-8') as stream:
        os.chmod(evidence, 0o600)
        def record(value):
            value.update(schema=1, mode=args.mode, hardware_acceptance=False,
                         collector_elapsed_seconds=round(time.monotonic() - started, 3))
            stream.write(json.dumps(value, allow_nan=False, separators=(',', ':')) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        for index in range(maximum):
            data = None
            try:
                data = bounded_ssh(argv, REMOTE)
                result = validate(data, args.boot_id, args.mode, baseline, previous)
                if result == 'readonly_evidence':
                    watchdog.check(data, time.monotonic())
                record({'result': result, 'sample': data})
                if result == 'pending_external_completion':
                    return 2
                baseline = baseline or data
                previous = data
                if time.monotonic() - started >= DURATIONS[args.mode] + 30:
                    record({'result': 'pending_external_completion', 'reason': 'external console/exit/duration proof required'})
                    return 2
            except Exception as error:
                failure = {'result': 'failed', 'reason': type(error).__name__ + ': ' + str(error)[:300]}
                if data is not None:
                    failure['sample'] = data
                record(failure)
                return 1
            time.sleep(max(0, started + (index + 1) * args.interval - time.monotonic()))
        record({'result': 'pending_external_completion', 'reason': 'sample bound reached'})
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

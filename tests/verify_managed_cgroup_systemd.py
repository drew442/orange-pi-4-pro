#!/usr/bin/python3
"""Opt-in disposable Ubuntu builder probe; never KVM or deployment evidence.

Run only after independent review from a root-owned, unwritable frozen checkout.
Supply a precreated private directory on the explicitly pinned builder device:
  sudo python3 -I -B tests/verify_managed_cgroup_systemd.py \
    --expected-machine-id HEX --expected-output-device MAJOR:MINOR \
    --output-dir /conservative/absolute/path
No account database, artifacts, KVM devices, controller files or migrations are
written. PID1 alone creates and removes the owned test cgroups. Failure receipts
remain in the output directory. There is deliberately no force/cleanup mode.
"""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid

SERVICE = 'a733-managed-budgetprobe.service'
SLICE = 'a733-guests.slice'
PARENT = 'a733.slice'
RUNTIME = Path('/run/systemd/system')
FRAGMENT = RUNTIME / SLICE
CGROOT = Path('/sys/fs/cgroup')
PARENTCG = CGROOT / PARENT
SLICECG = PARENTCG / SLICE
SERVICECG = SLICECG / SERVICE
SELF = Path(__file__).absolute()
BASE = SELF.parent.parent
SOURCES = (SELF, BASE / 'scripts/guest-cgroup-budget.py',
           BASE / 'configs/guest_boot_units.py', BASE / 'configs/guest_config.py',
           BASE / 'configs/guest_identity.py')
CONFIG = dict(schema=1, name='budgetprobe', uid=60002, gid=60002,
              memory_mib=128, memory_max_mib=256, cpus=1,
              **{key: dict(bytes=512, sha256='0' * 64)
                 for key in ('kernel', 'initrd', 'disk')})
LIMIT = 1024 * 1024


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def protected(path, directory=False, mode=None):
    info = path.lstat()
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == info.st_gid == 0 and not info.st_mode & 0o022,
            'unprotected node: ' + str(path))
    if mode is not None:
        require(stat.S_IMODE(info.st_mode) == mode, 'unexpected mode: ' + str(path))
    return info


def chain(path):
    for node in reversed((path, *path.parents)):
        protected(node, directory=True)


def bounded(path, limit=LIMIT):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        data = os.read(fd, limit + 1)
        require(len(data) <= limit, 'read exceeds bound: ' + str(path))
        return data
    finally:
        os.close(fd)


def load_guard():
    spec = importlib.util.spec_from_file_location('actual_budget_probe', SOURCES[1])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(argv, timeout=20):
    # Fixed executable names and caller-constructed bounded arguments only.
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C',
                               'SYSTEMD_COLORS': '0', 'SYSTEMD_PAGER': ''}) as proc:
        chunks = {proc.stdout: bytearray(), proc.stderr: bytearray()}
        deadline = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                for pipe in chunks:
                    selector.register(pipe, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, 'command timeout: ' + repr(argv))
                    for key, unused in selector.select(min(remaining, 1)):
                        data = os.read(key.fileobj.fileno(), 65536)
                        if not data:
                            selector.unregister(key.fileobj)
                        else:
                            chunks[key.fileobj].extend(data)
                            require(len(chunks[key.fileobj]) <= LIMIT,
                                    'command output exceeds bound')
            proc.wait(timeout=max(0.01, deadline - time.monotonic()))
        except BaseException:
            proc.kill()
            proc.wait(timeout=5)
            raise
        out, err = bytes(chunks[proc.stdout]), bytes(chunks[proc.stderr])
    require(proc.returncode == 0, 'command failed: ' + repr(argv) + ': ' +
            err[:2048].decode('utf-8', 'replace'))
    return out.decode('utf-8', 'strict')


def ctl(*args):
    return command(['/usr/bin/systemctl', '--no-pager', *args])


def properties(unit):
    text = ctl('show', unit, '--property=Id,Names,LoadState,ActiveState,SubState,'
               'FragmentPath,DropInPaths,MainPID,Result,ExecMainCode,ExecMainStatus,'
               'Description,Slice,MemoryMax,MemorySwapMax,TasksMax,ControlGroup')
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def jobs():
    return ctl('list-jobs', '--all', '--no-legend', '--plain')


def loaded():
    text = ctl('list-units', '--all', '--no-legend', '--plain')
    return {line.split()[0] for line in text.splitlines() if line.split()}


def dropin_names(names):
    result = set()
    for name in names:
        stem, kind = name.rsplit('.', 1)
        result.update((kind + '.d', name + '.d'))
        for index, character in enumerate(stem):
            if character == '-':
                result.add(stem[:index + 1] + '.' + kind + '.d')
    return result


def definitions(paths, names):
    found = []
    count = 0
    dropins = dropin_names(names)
    for root in paths:
        if not root.exists():
            continue
        # No symlink traversal, including directories. Inspect aliases by link target.
        for parent, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                count += 1
                require(count <= 100000, 'unit search exceeds bound')
                path = Path(parent) / name
                target = os.readlink(path) if path.is_symlink() else ''
                if name in dropins or any(name == item or name.startswith(item + '.') or
                                          item in Path(target).parts for item in names):
                    found.append(str(path))
    return sorted(set(found))


def inert_implicit(state):
    return (state.get('LoadState') in ('not-found', 'loaded') and
            state.get('ActiveState') == 'inactive' and state.get('SubState') == 'dead'
            and not state.get('FragmentPath') and not state.get('DropInPaths')
            and not state.get('ControlGroup'))


def absent(paths, names, initial_loaded=None):
    require(not definitions(paths, names), 'preexisting/runtime unit definitions')
    if initial_loaded is not None:
        require(not initial_loaded.intersection(names), 'named units initially loaded')
    pending = jobs().splitlines()
    require(not any(name in line.split() for name in names for line in pending),
            'named systemd jobs exist')
    for name in names:
        state = properties(name)
        require(not state.get('DropInPaths') and not state.get('FragmentPath'),
                'named unit definitions resolve: ' + name)
        require(inert_implicit(state) if name.endswith('.slice') else
                state.get('LoadState') == 'not-found', 'named unit resolves: ' + name)
    for path in (SLICECG, SERVICECG):
        require(not path.exists(), 'named cgroup exists: ' + str(path))


def stop_or_prove_gone(action, unit, cgroup, benign):
    try:
        ctl(action, unit)
    except BaseException as error:
        # Do not repeat a mutating request. Only a fresh absence proof permits progress.
        state = properties(unit)
        require(state.get('LoadState') == 'not-found' and not cgroup.exists(),
                'failed ' + action + '; unit/cgroup disappearance unproved: ' + repr(error))
        benign.append(dict(action=action, unit=unit, error=repr(error),
                           classification='fresh not-found and cgroup absent'))


class OwnedFragment:
    """Retain exclusive inode immediately; validate even an incomplete prefix."""
    def __init__(self, path, data):
        self.path, self.data, self.fd = path, data, None
        self.created, self.complete = None, None

    def create(self):
        blocked = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
        old = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL |
                              os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
            self.created = identity(os.fstat(self.fd))
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old)

    def validate(self):
        require(self.fd is not None and self.created is not None,
                'exclusive fragment ownership unavailable')
        info = os.fstat(self.fd)
        named = self.path.lstat()
        require(identity(info) == identity(named) and
                identity(info)[:4] == self.created[:4] and
                stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) in (0o600, 0o444),
                'exclusive fragment inode/metadata changed')
        content = os.pread(self.fd, LIMIT, 0)
        require(info.st_size == len(content) <= len(self.data) and
                content == self.data[:len(content)], 'fragment partial prefix differs')
        if self.complete is not None:
            require(identity(info) == self.complete and content == self.data,
                    'completed fragment changed')

    def finish(self):
        self.validate()
        full_write(self.fd, self.data)
        os.fchmod(self.fd, 0o444)
        self.complete = identity(os.fstat(self.fd))
        self.validate()
        read_fd = os.open(self.path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            require(identity(os.fstat(read_fd)) == self.complete,
                    'fragment replaced while reopening')
        except BaseException:
            os.close(read_fd)
            raise
        blocked = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
        old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
        try:
            old_fd = self.fd
            self.fd = read_fd  # Cleanup always retains the live ownership descriptor.
            os.close(old_fd)  # Exactly one close attempt; never retry an uncertain FD.
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)


def prove_unpublished_inactive():
    # list-units does not load/parse a newly created incomplete unit fragment.
    text = ctl('list-units', '--all', '--no-legend', '--plain')
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0] == SERVICE:
            raise RuntimeError('unexpected service object before publication')
        if fields and fields[0] == SLICE:
            require(len(fields) >= 4 and fields[2:4] == ['inactive', 'dead'],
                    'unpublished slice has activity')
    require(not any(name in line.split() for name in (SERVICE, SLICE)
                    for line in jobs().splitlines()), 'unpublished unit has jobs')
    require(not SLICECG.exists() and not SERVICECG.exists(),
            'unpublished unit has actual cgroup')


def full_write(fd, data):
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        require(written > 0, 'zero-length write')
        offset += written


def snapshot_cgroup(path):
    if not path.exists():
        return None
    return {'children': sorted(p.name for p in path.iterdir() if p.is_dir()),
            'procs': bounded(path / 'cgroup.procs', 65536).decode(),
            'events': bounded(path / 'cgroup.events', 4096).decode()}


def identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def probe():
    """Executed only as the fixed transient command; imports precede UID drop."""
    require(os.getuid() == os.geteuid() == 0, 'probe must initially be root')
    guard = load_guard()
    # Force libc setup and all imports before a root 0700 checkout becomes hidden.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.restype = ctypes.c_int
    def prctl(option, arg=0):
        require(libc.prctl(option, arg, 0, 0, 0) == 0,
                'prctl failed: ' + str(ctypes.get_errno()))
    last = int(bounded('/proc/sys/kernel/cap_last_cap', 128))
    require(0 <= last <= 63, 'unexpected capability range')
    os.setgroups([])
    prctl(47, 4)  # PR_CAP_AMBIENT_CLEAR_ALL
    for capability in range(last + 1):
        prctl(24, capability)  # PR_CAPBSET_DROP
    os.setresgid(60002, 60002, 60002)
    os.setresuid(60002, 60002, 60002)
    prctl(38, 1)  # PR_SET_NO_NEW_PRIVS
    status = bounded('/proc/self/status', 65536).decode()
    security = {line.split(':', 1)[0]: line.split(':', 1)[1].strip()
                for line in status.splitlines()
                if line.startswith(('Uid:', 'Gid:', 'Groups:', 'Cap', 'NoNewPrivs:'))}
    require(os.getresuid() == (60002,) * 3 and os.getresgid() == (60002,) * 3
            and os.getgroups() == [], 'credential drop differs')
    require(all(int(security[key], 16) == 0 for key in
                ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')) and
            security['NoNewPrivs'] == '1', 'capability drop differs')
    before = sorted(os.listdir('/proc/self/fd'))
    budget = guard.CgroupBudget(CONFIG)
    try:
        budget.check()
        controllers = {str(path): bounded(path, 128).decode()
                       for directory in (SLICECG, SERVICECG)
                       for path in (directory / name for name in
                                    ('memory.max', 'memory.swap.max', 'pids.max', 'cgroup.type'))}
        membership = bounded('/proc/self/cgroup', 4096).decode()
        retained = len(budget._fds)
    finally:
        budget.close()
    after = sorted(os.listdir('/proc/self/fd'))
    require(before == after, 'descriptor set not restored')
    print(json.dumps(dict(result='pass', security=security, controllers=controllers,
                          membership=membership, retained_fds=retained,
                          fd_before=before, fd_after=after), sort_keys=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-machine-id', required=True)
    parser.add_argument('--expected-output-device', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    require(os.getuid() == os.geteuid() == 0, 'actual root required')
    require(re.fullmatch('[0-9a-f]{32}', args.expected_machine_id), 'invalid machine ID pin')
    require(bounded('/etc/machine-id', 128).decode().strip() == args.expected_machine_id,
            'builder machine ID differs')
    require(os.uname().machine != 'aarch64', 'A733/ARM host execution refused')
    require('ID=ubuntu' in bounded('/usr/lib/os-release', 65536).decode().splitlines(),
            'Ubuntu builder required')
    require(bounded('/proc/1/comm', 128).strip() == b'systemd', 'native systemd required')
    for namespace in ('mnt', 'pid', 'user', 'cgroup'):
        require(os.readlink('/proc/self/ns/' + namespace) ==
                os.readlink('/proc/1/ns/' + namespace), 'PID1 namespace differs')
    require(bounded('/proc/self/uid_map', 4096).split() ==
            [b'0', b'0', b'4294967295'], 'native root user namespace required')
    output = args.output_dir
    for path in (output, SELF):
        require(re.fullmatch(r'/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', str(path))
                and all(part not in ('.', '..') for part in path.parts), 'unsafe path')
    chain(output)
    info = protected(output, directory=True, mode=0o700)
    require(re.fullmatch(r'[0-9]+:[0-9]+', args.expected_output_device), 'invalid device pin')
    require(str(os.major(info.st_dev)) + ':' + str(os.minor(info.st_dev)) ==
            args.expected_output_device and Path('/sys/dev/block/' +
            args.expected_output_device).exists(), 'output must be on pinned block device')
    pins = {}
    for source in SOURCES:
        chain(source.parent)
        protected(source)
        pins[str(source.relative_to(BASE))] = hashlib.sha256(bounded(source)).hexdigest()
    guard = load_guard()
    guard.mount_identity(bounded('/proc/self/mountinfo'))
    cg_fd = os.open(CGROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        require(guard.filesystem(cg_fd) == guard.MAGIC, 'native cgroup2 required')
    finally:
        os.close(cg_fd)
    # Parent directories are pinned before any mutation, /run must actually be RAM.
    for path in (Path('/run'), Path('/run/systemd'), RUNTIME):
        protected(path, directory=True)
    fd = os.open('/run', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        require(guard.filesystem(fd) == 0x01021994 and
                os.stat('/run').st_dev == os.stat(RUNTIME).st_dev,
                'unit runtime must be protected /run tmpfs')
    finally:
        os.close(fd)
    runtime_info = identity(RUNTIME.stat())[:5]
    paths_text = command(['/usr/bin/systemd-analyze', 'unit-paths'])
    paths = [Path(line) for line in paths_text.splitlines()]
    require(paths and len(paths) <= 64 and all(p.is_absolute() for p in paths)
            and RUNTIME in paths and Path('/run/systemd/transient') in paths,
            'unexpected unit search paths')
    initially_loaded = loaded()  # Before any show query can synthesize implicit slices.
    absent(paths, (SERVICE, SLICE), initial_loaded=initially_loaded)
    ancestor_before = dict(properties=properties(PARENT), cgroup=snapshot_cgroup(PARENTCG),
                           definitions=definitions(paths, (PARENT,)))
    parent_existed = (PARENT in initially_loaded or ancestor_before['cgroup'] is not None or
                      bool(ancestor_before['definitions']) or
                      not inert_implicit(ancestor_before['properties']))
    require(not any(PARENT in line.split() for line in jobs().splitlines()),
            'ancestor has a pending job')
    memtotal = [line for line in bounded('/proc/meminfo', 65536).decode().splitlines()
                if line.startswith('MemTotal:')]
    require(len(memtotal) == 1 and re.fullmatch(r'MemTotal:\s+[0-9]+ kB', memtotal[0]),
            'invalid MemTotal')
    memory = int(memtotal[0].split()[1]) * 1024
    fragment_bytes = guard.UNITS.render_slice(memory).encode()
    token = 'Owned disposable cgroup probe ' + uuid.uuid4().hex
    receipt = dict(scope='Ubuntu builder kernel guard only; no launch/KVM/boot authority',
                   source_pins=pins, machine_id=args.expected_machine_id,
                   output_device=args.expected_output_device, memtotal_bytes=memory,
                   ancestor_before=ancestor_before, token=token, result='incomplete')
    report_path = output / 'receipt.json'
    log_path = output / 'probe.log'
    report_fd = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                        os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    log_fd = None
    fragment = OwnedFragment(FRAGMENT, fragment_bytes)
    service_attempted = False
    fragment_finished = False
    publish_attempted = False
    errors = []
    benign_cleanup_races = []
    def save():
        data = (json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode()
        require(len(data) < LIMIT, 'receipt exceeds bound')
        os.lseek(report_fd, 0, 0)
        full_write(report_fd, data)
        os.ftruncate(report_fd, len(data))
        os.fsync(report_fd)
    def owned_fragment():
        fragment.validate()
        require(identity(RUNTIME.stat())[:5] == runtime_info, 'runtime parent changed')
    def owned_service():
        state = properties(SERVICE)
        require(state.get('Description') == token and
                state.get('FragmentPath') == '/run/systemd/transient/' + SERVICE and
                not state.get('DropInPaths') and state.get('Slice') == SLICE,
                'transient ownership uncertain')
        return state
    def interrupted(signum, unused):
        raise InterruptedError('received signal ' + str(signum))
    previous_handlers = {number: signal.signal(number, interrupted)
                         for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        save()
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        log_info = identity(os.fstat(log_fd))[:5]
        require(not definitions(paths, (SERVICE, SLICE)), 'unit definitions appeared')
        fragment.create()
        fragment.finish()
        fragment_finished = True
        owned_fragment()
        publish_attempted = True  # A reload failure can still have published the unit.
        ctl('daemon-reload')
        state = properties(SLICE)
        require(state.get('FragmentPath') == str(FRAGMENT) and not state.get('DropInPaths')
                and state.get('MemoryMax') == str(guard.UNITS._budget(memory))
                and state.get('MemorySwapMax') == '0' and state.get('TasksMax') == '4096',
                'effective slice properties differ')
        # Recheck generic/prefix drop-ins immediately before starting any service.
        require(set(definitions(paths, (SERVICE, SLICE))) <= {str(FRAGMENT)},
                'unit definitions/drop-ins appeared before start')
        require(not properties(SERVICE).get('DropInPaths'), 'service drop-ins appeared')
        service_attempted = True
        command(['/usr/bin/systemd-run', '--quiet', '--unit=' + SERVICE,
                 '--slice=' + SLICE, '--description=' + token,
                 '--property=Type=exec', '--property=RemainAfterExit=yes',
                 '--property=MemoryMax=256M', '--property=MemorySwapMax=0',
                 '--property=TasksMax=256', '--property=RuntimeMaxSec=300',
                 '--property=TimeoutStartSec=30', '--property=TimeoutStopSec=30',
                 '--property=CPUQuota=200%', '--property=NoNewPrivileges=yes',
                 '--property=UMask=0077', '--property=StandardInput=null',
                 '--property=StandardOutput=append:' + str(log_path),
                 '--property=StandardError=inherit', '--',
                 '/usr/bin/python3', '-I', '-B', str(SELF), '--probe'])
        deadline = time.monotonic() + 300
        while True:
            state = owned_service()
            print('budget probe: ' + state.get('ActiveState', '?') + '/' +
                  state.get('SubState', '?'), flush=True)
            if state.get('MainPID') == '0' and state.get('SubState') in ('exited', 'failed', 'dead'):
                break
            require(time.monotonic() < deadline, 'probe polling deadline exceeded')
            time.sleep(2)
        receipt['terminal_service'] = state
        require(state.get('MainPID') == '0' and state.get('SubState') == 'exited'
                and state.get('Result') == 'success' and state.get('ExecMainCode') == '1'
                and state.get('ExecMainStatus') == '0', 'probe terminal state failed')
        require(identity(log_path.lstat())[:5] == log_info, 'log ownership changed')
        raw = bounded(log_path, 65536)
        receipt['probe'] = json.loads(raw)
        require(receipt['probe']['result'] == 'pass', 'probe result differs')
        receipt['result'] = 'probe-passed-cleanup-pending'
    except BaseException as error:
        errors.append(repr(error))
    finally:
        # Keep cleanup sequential; any ownership uncertainty stops dependent cleanup.
        try:
            if service_attempted:
                state = properties(SERVICE)
                if state.get('LoadState') != 'not-found':
                    owned_service()
                    stop_or_prove_gone('stop', SERVICE, SERVICECG, benign_cleanup_races)
                    state = properties(SERVICE)
                    require(state.get('LoadState') == 'not-found' or
                            (state.get('MainPID') == '0' and
                             state.get('ActiveState') in ('inactive', 'failed')),
                            'owned service has not exited')
                    if state.get('ActiveState') == 'failed':
                        owned_service()
                        stop_or_prove_gone('reset-failed', SERVICE, SERVICECG,
                                           benign_cleanup_races)
            require(not SERVICECG.exists(), 'service cgroup remains after cleanup')
            if fragment.created is not None:
                owned_fragment()
                if not publish_attempted and not service_attempted:
                    # Incomplete text can be syntactically invalid. Never ask PID1
                    # to load/stop it: prove inactivity from existing data, unlink
                    # the exclusively owned prefix, then reload the clean namespace.
                    prove_unpublished_inactive()
                    receipt['fragment_cleanup_phase'] = (
                        'finished-unpublished' if fragment_finished else 'incomplete-unpublished')
                    owned_fragment()
                    FRAGMENT.unlink()
                else:
                    observation = snapshot_cgroup(SLICECG)
                    require(observation is None or (observation['children'] == [] and
                            observation['procs'] == ''), 'slice has foreign children/processes')
                    require(not any(SLICE in line.split() for line in jobs().splitlines()),
                            'slice still has jobs')
                    stop_or_prove_gone('stop', SLICE, SLICECG, benign_cleanup_races)
                    owned_fragment()
                    FRAGMENT.unlink()
                ctl('daemon-reload')
                if not parent_existed:
                    observation = snapshot_cgroup(PARENTCG)
                    require(observation is None or (observation['children'] == [] and
                            observation['procs'] == ''), 'new ancestor not empty')
                    require(not definitions(paths, (PARENT,)) and
                            not any(PARENT in line.split() for line in jobs().splitlines()),
                            'new ancestor no longer exclusively owned')
                    state = properties(PARENT)
                    require(not state.get('DropInPaths') and not state.get('FragmentPath'),
                            'new ancestor definition appeared')
                    if publish_attempted and state.get('LoadState') != 'not-found':
                        stop_or_prove_gone('stop', PARENT, PARENTCG, benign_cleanup_races)
                absent(paths, (SERVICE, SLICE))
                if not parent_existed:
                    require(not PARENTCG.exists() and inert_implicit(properties(PARENT)),
                            'new ancestor remains active')
                receipt['cleanup'] = 'verified'
                receipt['ancestor_after'] = dict(properties=properties(PARENT),
                                                cgroup=snapshot_cgroup(PARENTCG))
        except BaseException as error:
            errors.append('cleanup: ' + repr(error))
        receipt['benign_cleanup_races'] = benign_cleanup_races
        receipt['errors'] = errors
        receipt['result'] = 'pass' if not errors and receipt.get('cleanup') == 'verified' else 'fail'
        try:
            save()
        finally:
            for fd in (fragment.fd, log_fd, report_fd):
                if fd is not None:
                    os.close(fd)
            for number, handler in previous_handlers.items():
                signal.signal(number, handler)
    print(json.dumps(dict(result=receipt['result'], receipt=str(report_path), errors=errors)), flush=True)
    return 0 if receipt['result'] == 'pass' else 1


if __name__ == '__main__':
    if sys.argv[1:] == ['--probe']:
        probe()
    else:
        sys.exit(main())

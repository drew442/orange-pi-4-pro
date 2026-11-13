#!/usr/bin/python3
"""External disposable-fixture observation and one supported stop; no acceptance credit.

The live phase observes metadata without guest exclusion or disk authentication.
Only a later, separate qualify-managed-boot invocation authenticates completion.
Never use this helper for production guest lifetime policy.
"""
import argparse
import base64
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid

QUALIFIER_SHA256 = '938ef0355df0a764160c63e5d1c0532818888ec8135fba962830472f41025ae0'
QUALIFIER_BYTES = 15424
COMMAND = ['/usr/bin/python3', '-I', '-B', '/usr/lib/a733-tools/scripts/manage-guests.py', 'stop', 'autokvm']
OBSERVE_SECONDS = 135
STOP_TIMEOUT = 450
TOTAL_TIMEOUT = 900
OUTPUT_LIMIT = 256 * 1024
GUEST = 'autokvm'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def retain(raw):
    return dict(bytes=len(raw), sha256=sha(raw), data_base64=base64.b64encode(raw).decode('ascii'))


def private_ram(directory):
    require(directory.is_absolute() and '..' not in directory.parts, 'absolute RAM directory required')
    for parent in reversed((directory,) + tuple(directory.parents)):
        info = os.lstat(parent)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0 and
                not info.st_mode & 0o022, 'unsafe RAM directory ancestry')
    info = os.lstat(directory)
    require(stat.S_IMODE(info.st_mode) == 0o700 and info.st_dev == os.stat('/run').st_dev,
            'root-private /run tmpfs directory required')
    rows = Path('/proc/self/mountinfo').read_text().splitlines()
    require(any(row.split()[4] == '/run' and row.split(' - ', 1)[1].split()[0] == 'tmpfs'
                for row in rows), '/run must be tmpfs')


def load_installed():
    """Authenticate the sibling qualifier before its installed-closure checks execute."""
    directory = Path(__file__).absolute().parent
    private_ram(directory)
    path = directory / 'qualify-managed-boot.py'
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0 and
                not before.st_mode & 0o022 and before.st_nlink == 1 and
                before.st_dev == os.stat(directory).st_dev and before.st_size == QUALIFIER_BYTES,
                'unsafe staged qualifier')
        raw = os.read(fd, QUALIFIER_BYTES + 1)
        require(len(raw) == QUALIFIER_BYTES and sha(raw) == QUALIFIER_SHA256 and
                os.fstat(fd) == before and os.lstat(path) == before, 'staged qualifier differs')
    finally:
        os.close(fd)
    namespace = {'__name__': 'completion_qualifier', '__file__': str(path)}
    exec(compile(raw, str(path), 'exec'), namespace)
    return namespace['load_installed']()


def native(M, boot):
    require(os.getresuid() == (0, 0, 0), 'root required')
    require(M.A._boot_id() == boot, 'boot identity changed')
    M.P.installed_guard(boot)
    require(not list(Path('/sys/class/block').glob('mmcblk*')), 'SD must be absent')
    root = M.P.CONTEXT.MANAGEMENT.mount_table(Path('/proc/self/mountinfo').read_text())['/'][0]
    require('ro' in root['flags'] and 'rw' not in root['flags'], 'read-only repaired B required')
    # The authenticated repaired-B marker pins slot B; additionally bind physical p3 root.
    require(os.stat('/').st_dev == os.stat('/dev/nvme0n1p3').st_rdev, 'NVMe B p3 root required')


def read_manifest(M, controller, guard, stack):
    directory = stack.enter_context(M.L.STORE.RootDirectory(controller.context.guest_fd, GUEST, guard=guard))
    return directory.read('enrollment.json', M.P.POLICY.MANIFEST.LIMIT)


class ObservationCleanupError(RuntimeError):
    def __init__(self, primary, errors):
        self.primary, self.errors = primary, errors
        super().__init__('observation cleanup failed: ' + repr(errors))


def observe(M, boot, expected_run=None):
    """Metadata snapshot only; no disk opens, guest locks, console or QMP access."""
    native(M, boot)
    P, L, A = M.P, M.L, M.A
    admission = lease = None
    try:
        with ExitStack() as stack:
            policy = stack.enter_context(P.BOOT_POLICY.Policy(boot))
            admission = A.Admission.open_existing(boot, P.NORMAL.local_kvm_gid(), P.NORMAL.manager_state)
            require(type(admission) is A.Admission and admission.bootstrap_owner is None,
                    'existing admission required')
            lease = admission.admit()
            require(type(lease) is A.Lease and not lease.exclusive and lease.admission is admission,
                    'shared admission required')
            controller = stack.enter_context(L.StateController(policy.value['storage'], lease, boot_id=boot))

            def guard():
                policy.check()
                admission.recheck()
                lease.recheck()
                controller.context.guard()

            guard()
            status = controller.status()
            observed = status['heads'][GUEST]
            require(not observed.get('unavailable'), 'current head unavailable')
            head = observed['head']
            entry = M.entry_for(status['registry'], GUEST)
            require(head['status'] == 'running' and head['name'] == GUEST and
                    head['uid'] == head['gid'] == 60002 and head['last_boot_id'] == boot and
                    (expected_run is None or head['run_id'] == expected_run), 'current fixture head differs')
            store = controller._head_store(stack, GUEST, guard)
            runs = stack.enter_context(store.child('runs'))
            raw = runs.read(head['run_id'] + '.json', P.HEAD.LIMIT)
            run = L.validate_run(raw)
            require(L.canonical_run(run) == raw and run['automatic'] is True and
                    run['boot_id'] == boot and run['fixture_profile'] == 'managed-power-key-v1' and
                    run['shutdown_profile'] == P.REGISTRY.SHUTDOWN_PROFILE and
                    all(run[k] == head[k] for k in ('name', 'run_id', 'uid', 'gid', 'storage', 'manifest_sha256')),
                    'current automatic fixture admission differs')
            admitted = P.HEAD.admit(run['previous_head'], run['manifest_sha256'], run['run_id'], boot,
                                    expected_head_sha256=run['previous_head_sha256'], automatic=True)
            running = P.HEAD.mark_running(admitted, expected_head_sha256=sha(P.HEAD.canonical(admitted)))
            require(head == running, 'observed run/head transition differs')
            require(entry['enabled'] is True and all(entry[k] == run[k] for k in
                    ('name', 'uid', 'gid', 'manifest_sha256', 'fixture_profile', 'shutdown_profile')),
                    'current registry differs')
            manifest_raw = read_manifest(M, controller, guard, stack)
            require(sha(manifest_raw) == run['manifest_sha256'], 'observed manifest pin differs')
            manifest = P.POLICY.MANIFEST.validate_manifest(manifest_raw)
            config = manifest['config']
            F = P.CP.FIXTURE
            require(config['name'] == GUEST and config['uid'] == config['gid'] == 60002 and
                    config['kernel'] == F.KERNEL and config['initrd'] == F.INITRD and
                    config['disk']['bytes'] == F.LOCK['disk_bytes'] and
                    config['network'] == dict(mode='isolated', host_tcp_port=18081, guest_tcp_port=8080),
                    'reviewed fixture config differs')
            require(manifest['guest_uuid'] == run['storage']['guest_uuid'], 'manifest storage differs')
            unit = M.D.properties('a733-managed-autokvm.service',
                                  ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'Job'))
            require(unit['LoadState'] == 'loaded' and unit['ActiveState'] == 'active' and
                    unit['SubState'] == 'running' and unit['Job'] == '' and
                    unit['MainPID'].isdecimal() and int(unit['MainPID']) > 1, 'managed supervisor differs')
            supervisor = int(unit['MainPID'])
            parent, _ = A._process(supervisor)
            require(A._status(supervisor).get('Uid') == ['0'] * 4, 'supervisor root identity differs')
            candidates = []
            for path in Path('/proc').iterdir():
                if not path.name.isdecimal():
                    continue
                try:
                    identity, ppid = A._process(int(path.name))
                    if ppid != supervisor:
                        continue
                    credentials = A._status(identity['pid'])
                    if credentials.get('Uid') == ['60002'] * 4 and credentials.get('Gid') == ['60002'] * 4:
                        candidates.append(identity)
                except (FileNotFoundError, ProcessLookupError):
                    continue
            require(len(candidates) == 1, 'one managed QEMU child required')
            child = candidates[0]
            proc = Path('/proc') / str(child['pid'])
            cmdline = (proc / 'cmdline').read_bytes().split(b'\0')
            require(os.readlink(proc / 'exe') == '/usr/bin/qemu-system-aarch64' and
                    any(cmdline[i:i + 2] == [b'-accel', b'kvm'] for i in range(len(cmdline))),
                    'actual QEMU -accel kvm required')
            cgroup = (proc / 'cgroup').read_bytes()
            require(cgroup == Path('/proc', str(supervisor), 'cgroup').read_bytes() and
                    b'/a733-managed-autokvm.service\n' in cgroup, 'managed cgroup differs')
            census = F.census(child['pid'], config['cpus'], 18081)
            require(A._process(child['pid']) == (child, supervisor) and
                    A._process(supervisor)[0] == parent, 'process identity changed')
            require(store.read('head.json', P.HEAD.LIMIT) == P.HEAD.canonical(head) and
                    runs.read(head['run_id'] + '.json', P.HEAD.LIMIT) == raw and
                    controller.status() == status, 'observed metadata changed')
            guard()
            native(M, boot)
            return dict(boot_id=boot, run_id=head['run_id'], head_sha256=sha(P.HEAD.canonical(head)),
                        run_sha256=sha(raw), manifest_sha256=sha(manifest_raw),
                        registry_sha256=status['registry_sha256'], supervisor=parent, qemu=child,
                        cpus=config['cpus'], cgroup=retain(cgroup), census=census)
    finally:
        # A cleanup failure refuses the observation, before any stop intent.
        primary, errors = sys.exc_info()[1], []
        for label, resource in (('lease', lease), ('admission', admission)):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as error:
                    errors.append(dict(resource=label, error=type(error).__name__ + ': ' + str(error)[:512]))
        if errors:
            raise ObservationCleanupError(str(primary)[:1024] if primary else None, errors) from primary


def stable_interval(snapshot, probe, *, clock=time.monotonic, sleep=time.sleep):
    baseline = snapshot()
    response = probe()
    require(snapshot() == baseline, 'identity changed across HTTP probe')
    start = clock()  # Head publication alone is not evidence that QEMU executed.
    while clock() - start < OBSERVE_SECONDS:
        sleep(max(0, min(15, OBSERVE_SECONDS - (clock() - start))))
        require(snapshot() == baseline, 'identity changed during observation')
    require(snapshot() == baseline and clock() - start >= OBSERVE_SECONDS,
            'stable 135-second interval incomplete')
    return baseline, dict(http=response, stable_seconds=clock() - start)


def publish(directory, name, value):
    private_ram(directory)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                      0o600, dir_fd=fd)
        try:
            raw = (json.dumps(value, sort_keys=True) + '\n').encode()
            position = 0
            while position < len(raw):
                count = os.write(out, raw[position:])
                require(count > 0, 'short receipt write')
                position += count
            os.fsync(out)
        finally:
            os.close(out)
        os.fsync(fd)
    finally:
        os.close(fd)


def stop_identity(observation):
    identity = dict(boot_id=observation['boot_id'], run_id=observation['run_id'],
                    main_pid=observation['supervisor']['pid'],
                    main_start_ticks=observation['supervisor']['start_ticks'])
    for key in ('boot_id', 'run_id'):
        require(str(uuid.UUID(identity[key])) == identity[key] and uuid.UUID(identity[key]).int != 0,
                'canonical nonzero bound stop UUID required')
    for key in ('main_pid', 'main_start_ticks'):
        require(type(identity[key]) is int and 0 < identity[key] <= (1 << 63) - 1,
                'positive bound supervisor identity required')
    return identity


def stop_command(identity):
    require(set(identity) == {'boot_id', 'run_id', 'main_pid', 'main_start_ticks'},
            'all four native stop identity fields required')
    return COMMAND + [part for key in ('boot_id', 'run_id', 'main_pid', 'main_start_ticks')
                      for part in ('--expected-' + key.replace('_', '-'), str(identity[key]))]


def native_receipt(receipt, expected):
    require(type(receipt) is dict and receipt.get('success') is True and
            receipt.get('stage') == 'complete' and receipt.get('stopped') is True and
            receipt.get('operation') == 'stop' and receipt.get('name') == GUEST and
            receipt.get('expected_identity') == expected and
            receipt.get('intent_published') is True and
            type(receipt.get('intent')) is dict and receipt['intent'].get('durable') is True and
            receipt.get('signal_attempted') is True and receipt.get('signal_acknowledged') is True and
            all(receipt.get(key) is False for key in ('queue_attempted', 'queue_acknowledged',
                'queue_uncertain', 'intent_uncertain', 'signal_uncertain', 'stop_uncertain',
                'pid1_stop_timeout_armed', 'metadata_confers_run_trust')) and
            receipt.get('completed_cohort_qualification_required') is True and
            receipt.get('cleanup_errors') == [], 'native bound stop refused or incomplete; never retry')
    require(receipt.get('unit_status') == dict(LoadState='loaded', ActiveState='inactive',
            SubState='dead', MainPID='0', Result='success', Job='', ExecMainCode='1',
            ExecMainStatus='0'), 'native bound stop terminal checkpoint closure differs; never retry')


def stop_once(expected):
    """Exactly one subprocess; bounded complete output, with uncertainty retained."""
    command = stop_command(expected)
    result = dict(command=command, expected_identity=expected, attempted=True, uncertain=True)
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    process = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        deadline = time.monotonic() + STOP_TIMEOUT
        with selectors.DefaultSelector() as poll:
            poll.register(process.stdout, selectors.EVENT_READ, 'stdout')
            poll.register(process.stderr, selectors.EVENT_READ, 'stderr')
            while poll.get_map():
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'stop timeout; never retry')
                events = poll.select(min(1, remaining))
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        poll.unregister(key.fileobj)
                    else:
                        room = OUTPUT_LIMIT - sum(map(len, output.values()))
                        output[key.data].extend(chunk[:room])
                        if len(chunk) > room:
                            result['output_incomplete'] = True
                        require(len(chunk) <= room, 'stop output overflow; retained prefix; never retry')
            result['returncode'] = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        result['receipt'] = json.loads(bytes(output['stdout']))
        require(result['returncode'] == 0, 'native bound stop failed; never retry')
        native_receipt(result['receipt'], expected)
        result['uncertain'] = False
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)[:1024]
    finally:
        if process is not None:
            errors = []
            try:
                if process.poll() is None:
                    process.kill()
                result['returncode'] = process.wait(timeout=2)
            except BaseException as error:
                errors.append(dict(resource='process', error=str(error)[:1024]))
            # Descendants may retain writers after the manager has exited.
            # Never block waiting for them; always close both independent readers.
            for key, stream in (('stdout', process.stdout), ('stderr', process.stderr)):
                if stream is None:
                    continue
                try:
                    os.set_blocking(stream.fileno(), False)
                    while True:
                        try:
                            chunk = os.read(stream.fileno(), 4096)
                        except BlockingIOError:
                            result['output_incomplete'] = True
                            result['uncertain'] = True
                            break
                        if not chunk:
                            break
                        room = OUTPUT_LIMIT - sum(map(len, output.values()))
                        output[key].extend(chunk[:room])
                        if len(chunk) > room:
                            result['output_incomplete'] = True
                            result['uncertain'] = True
                            break
                except BaseException as error:
                    errors.append(dict(resource=key + '-drain', error=str(error)[:1024]))
                finally:
                    try:
                        stream.close()
                    except BaseException as error:
                        errors.append(dict(resource=key + '-close', error=str(error)[:1024]))
            if errors:
                result['cleanup_errors'] = errors
                result['uncertain'] = True
        result.update({key: retain(bytes(value)) for key, value in output.items()})
    return result


def complete(boot, expected_counter, expected_run=None):
    result = dict(success=False, stage='source', hardware_acceptance=False, power_authorized=False,
                  disk_or_run_trust=False, completed_cohort_authenticated=False, guest=GUEST,
                  expected_boot_id=boot, expected_counter=expected_counter, cleanup_errors=[])
    try:
        M = load_installed()
        result['stage'] = 'observe'
        baseline, evidence = stable_interval(lambda: observe(M, boot, expected_run),
            lambda: M.P.CP.FIXTURE.http(18081, expected_counter, confirm=False))
        result.update(observation=baseline, evidence=evidence, stage='intent')
        # Reopen snapshots release ALL guest/admission resources before stop.
        require(observe(M, boot, baseline['run_id']) == baseline, 'final identity changed')
        directory = Path(__file__).absolute().parent
        name = 'complete-' + boot + '-' + baseline['run_id']
        expected = stop_identity(baseline)
        publish(directory, name + '.intent.json', dict(operation='one-supported-fixture-stop',
                boot_id=boot, run_id=baseline['run_id'], expected_counter=expected_counter,
                observation=baseline, expected_identity=expected, command=stop_command(expected)))
        result['intent'] = name + '.intent.json'
        result['stage'] = 'stop'
        result['stop'] = stop_once(expected)
        result['stage'] = 'retain-stop'
        publish(directory, name + '.receipt.json', result['stop'])
        require(not result['stop']['uncertain'], 'stop uncertain or refused; never retry')
        result.update(success=True, stage='await-separate-qualification')
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)[:1024]
        if isinstance(error, ObservationCleanupError):
            result['cleanup_errors'] = error.errors
            result['primary_error'] = error.primary
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--complete', action='store_true')
    parser.add_argument('--expected-boot-id')
    parser.add_argument('--expected-run-id')
    parser.add_argument('--expected-counter', type=int)
    args = parser.parse_args(argv)
    if not args.complete:
        result = dict(operation='plan', completion_requested=False, guest=GUEST,
                      hardware_acceptance=False, power_authorized=False,
                      disk_or_run_trust=False, observation_seconds=OBSERVE_SECONDS,
                      stop_command=COMMAND,
                      required_stop_identity_arguments=['--expected-boot-id', '--expected-run-id',
                          '--expected-main-pid', '--expected-main-start-ticks'],
                      name_only_stop_permitted=False, total_timeout_seconds=TOTAL_TIMEOUT)
    else:
        try:
            require(args.expected_boot_id is not None and
                    str(uuid.UUID(args.expected_boot_id)) == args.expected_boot_id,
                    '--complete requires canonical --expected-boot-id')
            require(args.expected_run_id is None or str(uuid.UUID(args.expected_run_id)) == args.expected_run_id,
                    'canonical expected run UUID required')
            require(args.expected_counter is not None and 0 <= args.expected_counter <= 99999999,
                    '--complete requires --expected-counter')
            require(sys.flags.isolated == sys.flags.dont_write_bytecode == 1, 'requires python3 -I -B')
        except (ValueError, AttributeError) as error:
            parser.error(str(error))
        def deadline(*unused):
            raise TimeoutError('helper 15-minute deadline exceeded; never retry an existing intent')
        previous = signal.signal(signal.SIGALRM, deadline)
        signal.alarm(TOTAL_TIMEOUT)
        try:
            result = complete(args.expected_boot_id, args.expected_counter, args.expected_run_id)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get('success', result.get('operation') == 'plan') else 1


if __name__ == '__main__':
    sys.exit(main())

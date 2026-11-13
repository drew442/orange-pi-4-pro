#!/usr/bin/python3
"""Root direct-child/QMP supervision, without checkpoint or service authority.

Create controllers through LIFECYCLE so ManagedRun capability identity is shared.
The caller owns the run, controller and lease through subsequent stopped-disk
inspection and durable checkpoint reconciliation. StoppedRun is only a shutdown
outcome: it does not attest filesystem cleanliness or authorize publication.
"""
from contextlib import ExitStack
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import threading
import time


def module(name, relative):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


LIFECYCLE = module('supervisor_lifecycle', 'scripts/guest-lifecycle.py')
PROCESS = module('supervisor_process', 'scripts/guest-process-evidence.py')
TRANSPORT = module('supervisor_transport', 'scripts/guest-qmp-channel.py')
ADMISSION = LIFECYCLE.ADMISSION
ENROLL = LIFECYCLE.CONTEXT.ENROLL
PERMIT = ADMISSION.PERMIT
PAYLOAD = '/usr/lib/orangepi-hypervisor/payload.json'
QEMU = '/usr/bin/qemu-system-aarch64'
SETPRIV = '/usr/bin/setpriv'
PYTHON = '/usr/bin/python3'
LAUNCHER = str(Path(__file__).resolve().with_name('launch-managed-guest.py'))
CONSOLE_LIMIT = EVIDENCE_LIMIT = 8 * 1024 * 1024
# Publication authenticates native storage repeatedly. Leave a bounded 60-second
# child-side cushion beyond the parent's publication/release budget. These are
# pre-exec deadlines, independent of QMP and the unlimited running guest lifetime.
PERMIT_PUBLICATION_TIMEOUT = 120
BARRIER_TIMEOUT = 180
CONNECT_TIMEOUT = 10
GRACEFUL_TIMEOUT = 60
EXIT_TIMEOUT = 10
TERM_TIMEOUT = KILL_TIMEOUT = 5


class SupervisionError(RuntimeError):
    """Retains partial publication, process and cleanup evidence."""
    def __init__(self, receipt, cause):
        self.receipt = copy.deepcopy(receipt)
        self.cause = cause
        super().__init__(f"{receipt['stage']}: {type(cause).__name__}: {cause}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Console:
    """One reader thread; never performs authority, QMP, child wait or close I/O."""
    def __init__(self, fd):
        self.fd = fd
        self._data = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.eof = False
        self.error = None
        self.thread = threading.Thread(target=self._collect, name='a733-console', daemon=True)

    def start(self):
        os.set_blocking(self.fd, False)
        self.thread.start()

    def _collect(self):
        try:
            while not self._stop.is_set():
                if not select.select([self.fd], [], [], 0.05)[0]:
                    continue
                try:
                    chunk = os.read(self.fd, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    self.eof = True
                    return
                with self._lock:
                    remaining = CONSOLE_LIMIT - len(self._data)
                    self._data.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        raise ValueError('console byte limit exceeded')
        except Exception as error:
            self.error = f'{type(error).__name__}: {error}'

    def snapshot(self):
        with self._lock:
            return bytes(self._data)

    def check(self):
        require(self.error is None, self.error or 'console collection failed')

    def join(self, *, eof=False):
        if eof:
            self.thread.join(1)
        self._stop.set()
        self.thread.join(1)
        require(not self.thread.is_alive(), 'console collector did not stop; descriptor retained')
        if eof:
            self.check()
            require(self.eof, 'console did not reach EOF')


class StoppedRun:
    """Borrow actual shutdown evidence; no fixture/inspection/checkpoint authority."""
    def __init__(self, supervisor, token):
        require(token is supervisor._outcome_token, 'private outcome token required')
        self._supervisor = supervisor
        self.run = supervisor.run
        self.witness = supervisor.witness
        self.channel = supervisor.channel
        self.permit = supervisor.permit
        self.console = supervisor.console
        self._head_pin = self.run.head_sha256
        self._permit_data = self.permit.data
        self._console_data = self.console.snapshot()
        self._transcript = self.channel.transcript
        self._raw_status = self.witness.raw_status
        require(len(json.dumps(self.evidence, sort_keys=True, separators=(',', ':')).encode()) <=
                EVIDENCE_LIMIT, 'shutdown evidence byte limit exceeded')
        self.recheck()

    def recheck(self):
        owner = self._supervisor
        owner._owner()
        require(owner._outcome is None or owner._outcome is self, 'outcome identity differs')
        require(not owner._closed and not owner._failed and owner._stopped,
                'supervisor is not cleanly stopped')
        require(isinstance(self.run, LIFECYCLE.ManagedRun) and self.run is owner.run and
                isinstance(self.witness, PROCESS.ChildWitness) and self.witness is owner.witness and
                isinstance(self.channel, TRANSPORT.Channel) and self.channel is owner.channel and
                isinstance(self.permit, ADMISSION.Permit) and self.permit is owner.permit and
                self.console is owner.console, 'actual retained shutdown objects required')
        head = self.run.recheck()
        require(head['status'] == 'stopping' and self.run.head_sha256 == self._head_pin,
                'stopping authority differs')
        self.permit.recheck()
        owner._check_paths(terminal=True)
        require(self.permit.data == self._permit_data and self.witness.normal_zero and
                self.witness.raw_status == self._raw_status == owner._raw_status and
                self.channel.clean_shutdown and self.channel.transcript == self._transcript and
                self.console.eof and not self.console.thread.is_alive() and
                self.console.snapshot() == self._console_data,
                'shutdown evidence differs')
        self.console.check()
        return self.evidence

    @property
    def evidence(self):
        return dict(schema=1, profile='a733-supervised-shutdown-v1',
                    run_id=self.run.run_id, boot_id=self.run.boot_id,
                    permit_sha256=hashlib.sha256(self._permit_data).hexdigest(),
                    stopping_head_sha256=self._head_pin, raw_wait_status=self._raw_status,
                    qmp=list(copy.deepcopy(self._transcript)),
                    console_bytes=len(self._console_data),
                    console_sha256=hashlib.sha256(self._console_data).hexdigest(),
                    hardware_acceptance=False, checkpoint_authorized=False)


class Supervisor:
    """One root-owned fork and command sequence, borrowing persistent authority.

    start/poll/stop/close must be called serially by the creating thread. close
    invalidates any StoppedRun, closes only owned resources and never removes a
    permit, socket or partial directory. Failures quarantine before forced cleanup.
    """
    def __init__(self, run):
        require(isinstance(run, LIFECYCLE.ManagedRun), 'actual ManagedRun required; use LIFECYCLE')
        require(os.getuid() == os.geteuid() == 0, 'root supervisor required')
        require(run.recheck()['status'] == 'admitted', 'durable admitted run required')
        self.run = run
        self.admission = run.lease.admission
        self._parent = os.getpid()
        self._thread = threading.get_ident()
        self._stack = ExitStack()
        self._paths = []
        self._edges = []
        self._owned_fds = set()
        self._started = self._stopped = self._closed = self._failed = False
        self._wait_lost = False
        self._raw_status = None
        self.pid = self.start_ticks = None
        self.witness = self.channel = self.permit = self.console = self.connection = None
        self._socket_pin = None
        self._connection_pin = self._channel_binding = self._qmp_directory_pin = None
        self._terminal_ready = self._terminal_leaf_absent = False
        self._outcome = None
        self._outcome_token = object()
        self.receipt = dict(stage='new', run_id=run.run_id, errors=[], publications=[],
                            raw_wait_status=None, forced=False, hardware_acceptance=False)

    def _owner(self):
        require(os.getpid() == self._parent and threading.get_ident() == self._thread,
                'supervisor process/thread changed')
        require(not self._closed, 'supervisor closed')

    def _own_fd(self, fd):
        self._owned_fds.add(fd)
        return fd

    def _close_fd(self, fd):
        if fd in self._owned_fds:
            self._owned_fds.remove(fd)
            os.close(fd)

    def _root_file(self, path, limit, executable=False):
        held = ENROLL.HeldPath(str(Path(path).resolve(strict=True)))
        self._stack.callback(held.close)
        ENROLL.regular(held.info)
        require(0 < held.info.st_size <= limit and
                (not executable or held.info.st_mode & 0o111), 'root input size/mode differs')
        digest = hashlib.sha256()
        remaining = held.info.st_size
        while remaining:
            data = os.pread(held.fd, min(1024 * 1024, remaining), held.info.st_size - remaining)
            require(bool(data), 'root input truncated')
            remaining -= len(data)
            digest.update(data)
        held.recheck()
        self._paths.append((path, held))
        return dict(path=path, bytes=held.info.st_size, sha256=digest.hexdigest())

    def _check_terminal_binding(self):
        """Authenticate retained EOF evidence without consulting a reaped PID."""
        self._owner()
        require(self._terminal_ready and not self._failed and not self._wait_lost and
                not self.receipt['forced'] and self._raw_status == 0 and
                isinstance(self.witness, PROCESS.ChildWitness) and
                self.witness.normal_zero and self.witness.raw_status == self._raw_status,
                'owned normal-zero terminal child required')
        require(self._socket_pin is not None and self._channel_binding is not None and
                all(actual is pinned for actual, pinned in zip(
                    (self.channel, self.witness, self.connection), self._channel_binding)) and
                isinstance(self.channel, TRANSPORT.Channel) and
                self.channel._socket is self.connection and self.channel._witness is self.witness and
                self.channel.clean_shutdown and self.console.eof and
                not self.console.thread.is_alive(), 'retained terminal channel required')
        self.console.check()
        directory_fd, directory_inode = self._qmp_directory_pin
        require(self.qmp_directory == directory_fd and
                ENROLL.inode(os.fstat(directory_fd)) == directory_inode,
                'retained QMP directory replaced')
        connection, fd, info, peer = self._connection_pin
        require(self.connection is connection and connection.fileno() == fd and
                self._connection_identity(connection) == (info, peer),
                'retained QMP connection replaced')

    @staticmethod
    def _connection_identity(connection):
        info = os.fstat(connection.fileno())
        require(stat.S_ISSOCK(info.st_mode), 'connected QMP descriptor is not a socket')
        return ((info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode),
                connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))

    def _check_paths(self, *, terminal=False):
        if terminal:
            self._check_terminal_binding()
        self.admission.recheck()
        for path, held in self._paths:
            held.recheck()
            require(ENROLL.inode(os.stat(path)) == ENROLL.inode(held.info), 'root input replaced')
        for parent, name, fd, pin, gid, mode in self._edges:
            require(self._directory(fd, gid, mode) == pin ==
                    ENROLL.inode(os.stat(name, dir_fd=parent, follow_symlinks=False)),
                    'QMP directory replaced')

        if self._socket_pin is not None:
            try:
                info = os.stat('qmp.sock', dir_fd=self.qmp_directory, follow_symlinks=False)
            except FileNotFoundError:
                if not terminal:
                    raise
                self._terminal_leaf_absent = True
                return
            require(not self._terminal_leaf_absent, 'terminal QMP socket reappeared')
            require((info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode) ==
                    self._socket_pin, 'bound QMP socket replaced')

    @staticmethod
    def _directory(fd, gid, mode):
        info = os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) and
                (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, gid, mode),
                'unsafe QMP directory')
        ADMISSION._tmpfs(fd)
        return ENROLL.inode(info)

    def _prepare_directory(self, parent, name, gid, mode, *, fresh=False):
        self.run.recheck()
        created = False
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
            created = True
            self.receipt['publications'].append(dict(kind='qmp-directory', name=name))
        except FileExistsError:
            require(not fresh, 'run QMP directory already exists')
        fd = self._own_fd(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                                  os.O_CLOEXEC, dir_fd=parent))
        if created:
            self.run.recheck()
            os.fchown(fd, 0, gid)
            os.fchmod(fd, mode)
            os.fsync(fd)
        pin = self._directory(fd, gid, mode)
        self._edges.append((parent, name, fd, pin, gid, mode))
        self._check_paths()
        return fd

    def _prepare(self):
        self.run.recheck()
        self.payload_pin = self._root_file(PAYLOAD, 1024 * 1024)['sha256']
        self.qemu_pin = self._root_file(QEMU, 1024 * 1024 * 1024, executable=True)
        self.receipt['host_payload_sha256'] = self.payload_pin
        self.receipt['qemu'] = copy.deepcopy(self.qemu_pin)
        require(os.pread(self._paths[-1][1].fd, 4, 0) == b'\x7fELF', 'QEMU ELF required')
        for path in (SETPRIV, PYTHON):
            self._root_file(path, 1024 * 1024 * 1024, executable=True)
        self._root_file(LAUNCHER, 1024 * 1024)
        root = self._prepare_directory(self.admission.directory, 'qmp', self.admission.gid, 0o710)
        self.qmp_directory = self._prepare_directory(root, self.run.run_id,
                                                    self.run.config['gid'], 0o730, fresh=True)
        try:
            os.stat('qmp.sock', dir_fd=self.qmp_directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError('QMP socket already exists')

    def _permit_value(self):
        record = self.run._record  # Retained validated admission, never caller arguments.
        parent = PROCESS._identity(self._parent)
        child = PROCESS._identity(self.pid)
        require(child['ppid'] == self._parent and child['start_ticks'] == self.start_ticks,
                'barrier child identity differs')
        locks = {}
        for key, capability in (('lease', self.run.lease), ('guest', self.run.guest_lock)):
            fd = capability.recheck()
            info = os.fstat(fd)
            locks[key] = dict(fd=fd, dev=info.st_dev, ino=info.st_ino)
        namespaces = {}
        for name in ('pid', 'user', 'mnt'):
            info = os.stat('/proc/self/ns/' + name)
            namespaces[name] = dict(dev=info.st_dev, ino=info.st_ino)
        return dict(schema=1, profile=PERMIT.PROFILE, run_id=self.run.run_id,
                    boot_id=self.run.boot_id, storage=copy.deepcopy(record['storage']),
                    config=self.run.config, manifest_sha256=record['manifest_sha256'],
                    head_sha256=self.run.head_sha256, host_payload_sha256=self.payload_pin,
                    qemu=self.qemu_pin, kvm_gid=self.admission.gid,
                    parent={key: parent[key] for key in ('pid', 'start_ticks')},
                    child=dict(pid=self.pid, start_ticks=self.start_ticks),
                    locks=locks, namespaces=namespaces, automatic=record['automatic'])

    def _child_exec(self):
        config = self.run.config
        args = [SETPRIV, '--reuid=' + str(config['uid']), '--regid=' + str(config['gid']),
                '--groups=' + str(self.admission.gid), '--inh-caps=-all',
                '--ambient-caps=-all', '--bounding-set=-all', '--no-new-privs',
                PYTHON, '-I', '-B', LAUNCHER, '--run-id', self.run.run_id]
        os.execve(SETPRIV, args, {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'TZ': 'UTC'})

    def _fork(self):
        require(threading.active_count() == 1, 'fork requires no other Python threads')
        for fd in (0, 1, 2):
            os.fstat(fd)
        barrier_read, barrier_write = os.pipe2(os.O_CLOEXEC)
        self._own_fd(barrier_read)
        self._own_fd(barrier_write)
        console_read, console_write = os.pipe2(os.O_CLOEXEC)
        self._own_fd(console_read)
        self._own_fd(console_write)
        self._check_paths()
        self.run.recheck()
        parent_ticks = PROCESS._identity(self._parent)['start_ticks']
        self.pid = os.fork()
        if self.pid == 0:
            try:
                os.close(barrier_write)
                os.close(console_read)
                os.dup2(console_write, 1)
                os.dup2(console_write, 2)
                null = os.open('/dev/null', os.O_RDONLY | os.O_CLOEXEC)
                os.dup2(null, 0)
                keep = {0, 1, 2, barrier_read, self.run.lease.fd, self.run.guest_lock.fd}
                for entry in os.listdir('/proc/self/fd'):
                    fd = int(entry)
                    if fd not in keep:
                        try:
                            os.close(fd)
                        except OSError as error:
                            if error.errno != errno.EBADF:
                                raise
                if not select.select([barrier_read], [], [], BARRIER_TIMEOUT)[0]:
                    os._exit(125)
                if os.read(barrier_read, 2) != b'G':
                    os._exit(125)
                parent = PROCESS._identity(self._parent)
                if (os.getppid() != self._parent or parent['start_ticks'] != parent_ticks or
                        parent['state'] in (b'Z', b'X', b'x')):
                    os._exit(125)
                os.close(barrier_read)
                for fd in (self.run.lease.fd, self.run.guest_lock.fd):
                    os.set_inheritable(fd, True)
                self._child_exec()
            except BaseException:
                os._exit(126)
            os._exit(127)
        deadline = time.monotonic() + PERMIT_PUBLICATION_TIMEOUT
        def check_deadline():
            if time.monotonic() >= deadline:
                raise TimeoutError('permit publication/release deadline exceeded')

        def guard():
            check_deadline()
            result = self.run.recheck()
            check_deadline()
            return result

        # Retain the exact unreaped child identity before any further operation.
        self.start_ticks = PROCESS._identity(self.pid)['start_ticks']
        self.receipt['child'] = dict(pid=self.pid, start_ticks=self.start_ticks)
        self._close_fd(barrier_read)
        self._close_fd(console_write)
        self.console = Console(console_read)
        self.console.start()  # Only after fork, before any blocking protocol wait.
        self.receipt['stage'] = 'permit-publication'
        check_deadline()
        value = self._permit_value()
        check_deadline()
        self.receipt['permit'] = copy.deepcopy(value)
        self.permit = self.admission.publish_permit(value, self.run.lease,
                                                   self.run.guest_lock, guard=guard,
                                                   deadline=deadline)
        self._stack.callback(self.permit.close)
        check_deadline()
        self.receipt['permit_sha256'] = hashlib.sha256(self.permit.data).hexdigest()
        self.witness = PROCESS.ChildWitness(self.pid, self.start_ticks, value)
        check_deadline()
        self._check_paths()
        check_deadline()
        guard()
        require(PROCESS._identity(self.pid)['state'] not in (b'Z', b'X', b'x'),
                'barrier child exited before release')
        check_deadline()
        require(os.write(barrier_write, b'G') == 1, 'barrier release failed')
        self._close_fd(barrier_write)

    def _connect(self):
        deadline = time.monotonic() + CONNECT_TIMEOUT
        address = '/proc/self/fd/' + str(self.qmp_directory) + '/qmp.sock'
        while time.monotonic() < deadline:
            self.console.check()
            self.run.recheck()
            self._check_paths()
            require(self._reap() is None, 'child exited before QMP binding')
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.setblocking(False)
            try:
                info = os.stat('qmp.sock', dir_fd=self.qmp_directory, follow_symlinks=False)
                require(stat.S_ISSOCK(info.st_mode) and
                        (info.st_uid, info.st_gid) ==
                        (self.run.config['uid'], self.run.config['gid']), 'unsafe QMP socket')
                code = connection.connect_ex(address)
                if code in (errno.EINPROGRESS, errno.EALREADY):
                    writable = select.select([], [connection], [], min(0.05, max(0, deadline - time.monotonic())))[1]
                    code = connection.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) if writable else errno.EAGAIN
                if code:
                    raise OSError(code, os.strerror(code))
                require(ENROLL.inode(info) == ENROLL.inode(os.stat(
                    'qmp.sock', dir_fd=self.qmp_directory, follow_symlinks=False)),
                    'QMP socket replaced')
                self._socket_pin = (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode)
                self._check_paths()
                self.witness.verify_qmp_peer(connection)
                self.connection = connection
                self._qmp_directory_pin = (self.qmp_directory, ENROLL.inode(os.fstat(self.qmp_directory)))
                self._connection_pin = (connection, connection.fileno(),
                                        *self._connection_identity(connection))
                self._stack.callback(connection.close)
                return
            except OSError as error:
                connection.close()
                if error.errno not in (errno.ENOENT, errno.ECONNREFUSED, errno.EAGAIN):
                    raise
            except BaseException:
                connection.close()
                raise
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        raise TimeoutError('QMP connection deadline exceeded')

    def start(self):
        self._owner()
        require(not self._started and not self._failed, 'supervisor start is one-shot')
        self._started = True
        try:
            self.receipt['stage'] = 'prepare'
            self._prepare()
            self.receipt['stage'] = 'fork'
            self._fork()
            self.receipt['stage'] = 'qmp-connect'
            self._connect()
            self.channel = TRANSPORT.Channel(self.connection, self.witness, self.run.run_id)
            self._channel_binding = (self.channel, self.witness, self.connection)
            self.receipt['stage'] = 'qmp-paused-handshake'
            self.channel.greeting()
            self.channel.command('qmp_capabilities')
            self.console.check()
            self.witness.validate_child()
            self.permit.recheck()
            self._check_paths()
            self.receipt['stage'] = 'mark-running'
            self.receipt['running'] = self.run.mark_running()
            self.receipt['stage'] = 'cont'
            self.channel.command('cont')
            self.console.check()
            self.receipt['stage'] = 'running'
            return self
        except BaseException as error:
            self._failure(error)

    def _reap(self):
        if self._raw_status is not None:
            return self._raw_status
        require(not self._wait_lost, 'raw wait ownership lost')
        if self.pid is None:
            return None
        try:
            if self.witness is not None:
                status = self.witness.reap()
            else:
                pid, status = os.waitpid(self.pid, os.WNOHANG)
                if pid == 0:
                    status = None
        except (ChildProcessError, PROCESS.WitnessError):
            self._wait_lost = True
            raise
        if status is not None:
            self._raw_status = status
            self.receipt['raw_wait_status'] = status
        return status

    def poll(self):
        """Collect available asynchronous evidence without issuing a command."""
        self._owner()
        require(self.channel is not None and not self._failed, 'active channel required')
        try:
            self.console.check()
            if self.channel.shutdown_message is None:
                # receive processes one frame; cap work per poll. stop drains fully.
                for _ in range(64):
                    if not self.channel._queue and not select.select([self.connection], [], [], 0)[0]:
                        break
                    if self.channel.receive(timeout=0.1) is None:
                        break
                    if self.channel.shutdown_message is not None:
                        break
            return self._reap()
        except BaseException as error:
            self._failure(error)

    def console_snapshot(self):
        self._owner()
        return b'' if self.console is None else self.console.snapshot()

    def _wait_exit(self, timeout):
        deadline = time.monotonic() + timeout
        while self._reap() is None:
            if time.monotonic() >= deadline:
                raise TimeoutError('raw child exit deadline exceeded')
            if self.console is not None:
                self.console.check()
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        return self._raw_status

    def _finish_shutdown(self):
        self.receipt['stage'] = 'raw-exit'
        self._wait_exit(EXIT_TIMEOUT)
        self.receipt['stage'] = 'qmp-eof'
        self.channel.finish()
        self.console.join(eof=True)
        require(self.channel.clean_shutdown and self.witness.normal_zero,
                'guest-owned QMP shutdown and normal zero raw exit required')
        self._terminal_ready = True
        self._check_terminal_binding()

    def stop(self):
        self._owner()
        if self._outcome is not None:
            self._outcome.recheck()
            return self._outcome
        require(self.channel is not None and not self._failed, 'running supervisor required')
        try:
            self.receipt['stage'] = 'stop-observation'
            self.poll()  # Consume already readable self-shutdown before stop issuance.
            observed_shutdown = self.channel.shutdown_message is not None
            if observed_shutdown:
                # QEMU may unlink its listening pathname before we observe exit.
                # Collect every terminal proof before relaxing that leaf alone.
                self._finish_shutdown()
            self.permit.recheck()
            if observed_shutdown:
                self._check_paths(terminal=True)
            else:
                self._check_paths()
            require(self.run.recheck()['status'] == 'running', 'running authority required')
            self.receipt['stage'] = 'mark-stopping'
            self.receipt['stopping'] = self.run.mark_stopping()
            if not observed_shutdown:
                # Durable stopping publication precedes the fresh shared budget.
                deadline = time.monotonic() + GRACEFUL_TIMEOUT
                self.receipt['stage'] = 'system-powerdown'
                self.channel.command('system_powerdown', timeout=min(10, max(0.001, deadline - time.monotonic())))
                self.receipt['stage'] = 'shutdown-observation'
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'graceful shutdown deadline exceeded')
                self.channel.pump(timeout=min(60, remaining))
                self.console.check()
                self._finish_shutdown()
            self._stopped = True
            self._outcome = StoppedRun(self, self._outcome_token)
            self.receipt['stage'] = 'stopped'
            return self._outcome
        except BaseException as error:
            self._failure(error)

    def _signal(self, number):
        if self.pid is None or self._raw_status is not None or self._wait_lost:
            return
        self._reap()  # ECHILD prohibits all later signalling.
        if self._raw_status is not None:
            return
        identity = PROCESS._identity(self.pid)
        require(self.start_ticks is not None and identity['start_ticks'] == self.start_ticks and
                identity['ppid'] == self._parent, 'refuse signal: child identity ambiguous')
        if identity['state'] not in (b'Z', b'X', b'x'):
            try:
                os.kill(self.pid, number)
            except ProcessLookupError:
                self._reap()

    def _cleanup_child(self):
        if self.pid is None or self._raw_status is not None or self._wait_lost:
            return
        for number, timeout in ((signal.SIGTERM, TERM_TIMEOUT), (signal.SIGKILL, KILL_TIMEOUT)):
            try:
                self._signal(number)
                self.receipt['forced'] = True
                # Collection failure must not prevent bounded child cleanup.
                deadline = time.monotonic() + timeout
                while self._reap() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                if self._raw_status is not None:
                    return
            except BaseException as error:
                self.receipt['errors'].append('child-cleanup: ' + repr(error))
                # Identity ambiguity forbids further signals, but exclusive raw
                # wait ownership still permits a bounded reap attempt.
                deadline = time.monotonic() + timeout
                try:
                    while not self._wait_lost and self._reap() is None and time.monotonic() < deadline:
                        time.sleep(0.02)
                except BaseException as wait_error:
                    self.receipt['errors'].append('cleanup-wait: ' + repr(wait_error))
                return
        self.receipt['errors'].append('child remains unreaped after forced cleanup')

    def _close_owned(self):
        if self.console is not None:
            try:
                self.console.join()
            except BaseException as error:
                self.receipt['errors'].append('console-join: ' + repr(error))
        protected = self.console.fd if self.console is not None and self.console.thread.is_alive() else None
        for fd in list(self._owned_fds):
            if fd != protected:
                try:
                    self._close_fd(fd)
                except BaseException as error:
                    self.receipt['errors'].append('fd-close: ' + repr(error))
        try:
            self._stack.close()
        except BaseException as error:
            self.receipt['errors'].append('owned-close: ' + repr(error))

    def _failure(self, error):
        if self._closed and isinstance(error, SupervisionError):
            raise error
        self._failed = True
        self.receipt['errors'].append(repr(error))
        if hasattr(error, 'receipt'):
            self.receipt['failure_receipt'] = copy.deepcopy(error.receipt)
        if isinstance(error, ADMISSION.PublicationError):
            self.receipt['permit_error'] = dict(stage=error.stage, uncertain=error.uncertain)
            if error.__cause__ is not None:
                self.receipt['permit_error']['cause'] = dict(
                    type=type(error.__cause__).__name__[:128],
                    message=str(error.__cause__)[:2048])
        try:
            self.run.recheck()
            self.receipt['quarantine'] = self.run.quarantine('supervision-failed')
        except BaseException as quarantine_error:
            self.receipt['errors'].append('quarantine: ' + repr(quarantine_error))
        self._cleanup_child()
        self._close_owned()
        self.receipt['console'] = self.console_snapshot() if self.console is not None else b''
        if self.console is not None:
            self.receipt['console_error'] = self.console.error
            self.receipt['console_eof'] = self.console.eof
        if self.channel is not None:
            self.receipt['qmp'] = self.channel.transcript
        self._closed = True
        raise SupervisionError(self.receipt, error) from error

    def close(self):
        if self._closed:
            return
        self._owner()
        if self._started and not self._stopped:
            self._failure(ValueError('supervisor closed without clean stop'))
        self._close_owned()
        self._closed = True

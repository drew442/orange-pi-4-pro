"""Nonroot enrolled manual-launch admission; no CLI or storage authority.

Root bootstrap must call prepare_runtime with its retained closed owner before
opening admission. Nothing here grants managed launch or bypasses input checks.
"""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid

_SPEC = importlib.util.spec_from_file_location(
    'manual_launch_operation', Path(__file__).with_name('guest-operation-admission.py'))
NORMAL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(NORMAL)
CONTEXT, ADMISSION = NORMAL.CONTEXT, NORMAL.ADMISSION
require = ADMISSION.require
PROFILE = 'a733-manual-launch-v1'
RECORD = 'manual-launch.json'
GUESTS = '/run/a733-guests'
FILES = {'kernel': 'Image', 'initrd': 'initramfs.cpio.gz', 'disk': 'disk.raw'}


def canonical(value):
    require(type(value) is dict and set(value) == {
        'schema', 'profile', 'boot_id', 'kvm_gid', 'namespaces'}, 'invalid manual attestation fields')
    require(type(value['schema']) is int and value['schema'] == 1 and
            value['profile'] == PROFILE, 'invalid manual attestation profile')
    ADMISSION._uuid(value['boot_id'])
    require(type(value['kvm_gid']) is int and value['kvm_gid'] > 0,
            'invalid manual KVM group')
    pins = value['namespaces']
    require(type(pins) is dict and set(pins) == {'pid', 'user', 'mnt'},
            'invalid manual namespace fields')
    for pin in pins.values():
        require(type(pin) is dict and set(pin) == {'dev', 'ino'} and
                all(type(number) is int and number > 0 for number in pin.values()),
                'invalid manual namespace identity')
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


class Attestation:
    def __init__(self, admission):
        self.admission, self.fd = admission, None
        try:
            self.fd = admission._open(RECORD, 0o440)
            self.data = os.pread(self.fd, ADMISSION.MAX_RECORD + 1, 0)
            self.value = json.loads(self.data, object_pairs_hook=ADMISSION._pairs)
            require(canonical(self.value) == self.data, 'noncanonical manual attestation')
            self.recheck()
        except BaseException:
            self.close()
            raise

    def recheck(self):
        require(self.fd is not None, 'manual attestation closed')
        self.admission.recheck()
        ADMISSION._readonly(self.fd)
        self.admission._named(self.fd, RECORD, 0o440)
        require(os.pread(self.fd, ADMISSION.MAX_RECORD + 1, 0) == self.data,
                'manual attestation bytes changed')
        require(self.value['boot_id'] == self.admission.boot_id == ADMISSION._boot_id()
                and self.value['kvm_gid'] == self.admission.gid,
                'manual attested host differs')
        for name, pin in self.value['namespaces'].items():
            own = os.stat('/proc/self/ns/' + name)
            require((own.st_dev, own.st_ino) == (pin['dev'], pin['ino']),
                    'manual attested namespace differs')
            if name in ADMISSION.INITIAL_NAMESPACES:
                require(own.st_ino == ADMISSION.INITIAL_NAMESPACES[name],
                        'initial PID and user namespaces required')
        self.admission._named(self.fd, RECORD, 0o440)
        return self.value

    def close(self):
        fd, self.fd = self.fd, None
        if fd is not None:
            os.close(fd)


def prepare_runtime(owner):
    """Publish once under the canonical closed bootstrap owner; borrow its lease."""
    require(type(owner) is ADMISSION.ClosureOwner, 'canonical bootstrap owner required')
    admission = owner.admission
    require(type(admission) is ADMISSION.Admission, 'canonical admission required')

    def check():
        require(os.getresuid() == (0, 0, 0), 'actual root required')
        require(admission.bootstrap_owner is owner and owner.reason == 'bootstrap',
                'closed bootstrap owner required')
        owner._matching()
        lease = owner.lease
        require(type(lease) is ADMISSION.Lease and lease.admission is admission
                and lease.exclusive, 'bootstrap exclusive lease required')
        lease.recheck()
        ADMISSION._held_lock(lease.fd, 'WRITE', os.getpid())
        CONTEXT.host_identity(admission.boot_id)

    check()
    pins = {}
    for name in ('pid', 'user', 'mnt'):
        own, init = os.stat('/proc/self/ns/' + name), os.stat('/proc/1/ns/' + name)
        require(ADMISSION._same(own, init), 'native host namespace required')
        if name in ADMISSION.INITIAL_NAMESPACES:
            require(own.st_ino == ADMISSION.INITIAL_NAMESPACES[name],
                    'initial PID and user namespaces required')
        pins[name] = {'dev': own.st_dev, 'ino': own.st_ino}
    data = canonical(dict(schema=1, profile=PROFILE, boot_id=admission.boot_id,
                          kvm_gid=admission.gid, namespaces=pins))
    temporary, fd, rename_attempted = '.manual-launch-' + uuid.uuid4().hex, None, False
    stage = 'existing'
    try:
        try:
            existing = Attestation(admission)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            try:
                require(existing.data == data, 'existing manual attestation differs')
                check()
                os.fsync(existing.fd)
                ADMISSION._sync_directory(admission.directory, admission.gid)
                check()
                existing.recheck()
                return
            finally:
                existing.close()
        stage = 'create'
        check()
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                     | os.O_CLOEXEC, 0o600, dir_fd=admission.directory)
        os.fchown(fd, 0, admission.gid)
        stage = 'write'
        CONTEXT.ENROLL.write_all(fd, data)
        check()
        os.fchmod(fd, 0o440)
        admission._named(fd, temporary, 0o440)
        stage = 'file-fsync'
        os.fsync(fd)
        check()
        stage = 'rename'
        rename_attempted = True
        CONTEXT.ENROLL.rename_new(admission.directory, temporary, admission.directory, RECORD)
        stage = 'directory-fsync'
        ADMISSION._sync_directory(admission.directory, admission.gid)
        check()
        existing = Attestation(admission)
        try:
            require(existing.data == data, 'published manual attestation differs')
        finally:
            existing.close()
    except BaseException as error:
        raise ADMISSION.PublicationError(stage, rename_attempted) from error
    finally:
        # Always attempt all cleanup, and never retry a failed descriptor close.
        cleanup_error = None
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as error:
                cleanup_error = error
        try:
            os.unlink(temporary, dir_fd=admission.directory)
        except FileNotFoundError:
            pass
        except BaseException as error:
            cleanup_error = cleanup_error or error
        if cleanup_error is not None:
            raise ADMISSION.PublicationError('cleanup', rename_attempted) from cleanup_error


def enrolled_name(args):
    path = str(args.kernel)
    prefix = GUESTS + '/'
    require(path.startswith(prefix), 'fixed enrolled input paths required')
    parts = path[len(prefix):].split('/')
    require(len(parts) == 2, 'fixed enrolled input paths required')
    name = parts[0]
    ADMISSION._guest_name(name)
    for key, leaf in FILES.items():
        require(str(getattr(args, key)) == prefix + name + '/' + leaf,
                'fixed enrolled input paths required')
    return name


class ManualLaunch:
    """Own SH lease and stable guest EX through input verification and exec."""
    def __init__(self, args):
        self.stack = contextlib.ExitStack()
        self.closed = False
        self.pid, self.thread = os.getpid(), threading.get_ident()
        self.edges = []
        try:
            self.name = enrolled_name(args)
            require(os.getresuid() != (0, 0, 0), 'nonroot manual launch required')
            boot = ADMISSION._uuid(ADMISSION._boot_id())
            # The gid is untrusted until open_existing validates the entire
            # protected tmpfs chain and the attestation binds this exact gid.
            gid = os.lstat(ADMISSION.RUNTIME).st_gid
            require(gid > 0, 'KVM group required')
            self.admission = ADMISSION.Admission.open_existing(boot, gid, NORMAL.manager_state)
            self.stack.callback(self.admission.close)
            self.attestation = Attestation(self.admission)
            self.stack.callback(self.attestation.close)
            self.lease = self.admission.admit()
            self.stack.callback(self.lease.close)
            self.guest_lock = self.admission.guest_lock(self.name, self.lease)
            self.stack.callback(self.guest_lock.close)
            self._directories()
            self.recheck()
        except BaseException:
            self.close()
            raise

    def _directories(self):
        parent = os.open('/', os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        self.stack.callback(os.close, parent)
        ADMISSION._ancestor(os.fstat(parent))
        for path, gid, mode in (('/run', 0, 0o755), (GUESTS, 0, 0o711),
                                (GUESTS + '/' + self.name, None, 0o710)):
            name = Path(path).name
            fd = os.open(name, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=parent)
            self.stack.callback(os.close, fd)
            info = os.fstat(fd)
            if gid is None:
                gid = info.st_gid
                require(60000 <= gid <= 60999, 'enrolled guest GID required')
                self.gid = gid
            self.edges.append((parent, name, fd, gid, mode))
            parent = fd

    def _credentials(self):
        status = ADMISSION._status(self.pid)
        own = [str(self.gid)] * 4
        require(status.get('Uid') == own and status.get('Gid') == own,
                'enrolled guest credentials differ')
        groups = status.get('Groups', [])
        require(groups in ([str(self.admission.gid)],
                           sorted({str(self.admission.gid), str(self.gid)}, key=int)),
                'manual supplementary groups differ')
        require(status.get('NoNewPrivs') == ['1'] and
                status.get('NSpid') == [str(self.pid)], 'native unprivileged process required')
        for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
            value = status.get(key, [])
            require(len(value) == 1 and re.fullmatch(r'[0-9a-fA-F]+', value[0]) is not None
                    and int(value[0], 16) == 0, 'manual capabilities remain')

    def recheck(self):
        require(not self.closed and (os.getpid(), threading.get_ident()) ==
                (self.pid, self.thread), 'manual launch belongs to another process or thread')
        self.attestation.recheck()
        self._credentials()
        for parent, name, fd, gid, mode in self.edges:
            info = os.fstat(fd)
            require(stat.S_ISDIR(info.st_mode) and
                    (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, gid, mode)
                    and ADMISSION._same(info, os.stat(name, dir_fd=parent, follow_symlinks=False)),
                    'enrolled input directory changed or unsafe')
        self.lease.recheck()
        self.guest_lock.recheck()
        require(type(self.lease) is ADMISSION.Lease and not self.lease.exclusive,
                'normal shared launch lease required')
        ADMISSION._held_lock(self.lease.fd, 'READ', self.pid)
        ADMISSION._held_lock(self.guest_lock.fd, 'WRITE', self.pid)
        require(not self.admission.is_managed(self.name, self.lease, self.guest_lock),
                'managed guest requires lifecycle operation')
        return self.lease.fd, self.guest_lock.fd

    def validate_inputs(self, guest):
        self.recheck()
        guest.validate_inputs()
        for name, fd in guest.fds.items():
            info = os.fstat(fd)
            expected = (self.gid, self.gid, 0o600) if name == 'disk' else (0, 0, 0o444)
            require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == expected,
                    'enrolled input ownership or mode differs')
            named = os.stat(FILES[name], dir_fd=self.edges[-1][2], follow_symlinks=False)
            require(ADMISSION._same(info, named), 'enrolled input named inode changed')
        require(len({(os.fstat(fd).st_dev, os.fstat(fd).st_ino)
                     for fd in guest.fds.values()}) == 3, 'distinct enrolled inputs required')

    def close(self):
        require((os.getpid(), threading.get_ident()) == (self.pid, self.thread),
                'manual launch belongs to another process or thread')
        if not self.closed:
            self.closed = True
            self.stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

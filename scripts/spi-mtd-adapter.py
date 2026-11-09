"""Borrowed-FD Linux MTD callbacks for the reviewed SPI transaction engine.

No CLI, opens, closes, source pin overrides or device operations on import.
The trusted caller must hold exclusive use of the descriptor (and its aliases),
keep it in normal MTD file mode, and prevent concurrent close/reuse or flag changes.
The guard must raise unless identity/quiescence, registered ROM recovery,
authenticated sources and operator authority still hold. identity_check(fd) must
raise unless the bound A733 SPI/sysfs/JEDEC/namespace identity still holds.
These callbacks are trusted prerequisites, not proofs supplied by this backend.
emit(event) must return only after durable off-board capture; its return value is
ignored, as in the engine. Every exception propagates. No fsync is attempted.

After full engine verification the caller invalidates this adapter and closes its
own descriptor; close errors make the outcome uncertain. Readback and successful
callbacks alone do not establish physical persistence or hardware acceptance.
Descriptor stamps cannot defend against a malicious privileged caller or detect
close/reopen of the identical inode: lifetime/exclusive ownership is mandatory.

ABI: linux-orangepi 2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f,
include/uapi/mtd/mtd-abi.h and drivers/mtd/mtdchar.c. MEMERASE64 calls mtd_erase;
normal writes can fail after mutation; RW close invokes mtd_sync, without fsync.
Mutation uses single libc calls to avoid Python's automatic EINTR syscall retries.
Only Linux 64-bit arm64/x86_64 ABI is supported. Tests patch syscalls in process.
"""
import ctypes
import fcntl
import os
import platform
import stat
import struct
import sys

SIZE = 16777216
ERASE = 65536
CHUNK = 4096
MUTABLE_END = 26 * ERASE
MTD_INFO = struct.Struct('=B3xIIIIIQ')
ERASE_INFO = struct.Struct('=QQ')
MEMGETINFO = 0x80204D01
MEMERASE64 = 0x40104D14
WRITEABLE = 0x400


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def _erase_ioctl(fd, payload):
    """One libc ioctl, including on EINTR: never retry an uncertain erase."""
    function = _libc().ioctl
    function.argtypes = (ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p)
    function.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(payload)
    result = function(fd, MEMERASE64, buffer)
    if result == -1:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    require(type(result) is int and result == 0, 'invalid MEMERASE64 result')


def _pwrite(fd, data, offset):
    """One libc pwrite, with no automatic restart or partial-write loop."""
    function = _libc().pwrite
    function.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t,
                         ctypes.c_int64)
    function.restype = ctypes.c_ssize_t
    buffer = ctypes.create_string_buffer(data)
    result = function(fd, buffer, len(data), offset)
    if result == -1:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


class MtdAdapter:
    """Bind an already-open FD; no method owns or closes that descriptor.

    An operation failure permanently invalidates this instance. A new reviewed
    attempt needs a fresh adapter and fresh engine plan, never an automatic retry.
    Trusted callbacks are single-threaded and must not reenter the adapter.
    """
    def __init__(self, fd, *, guard, identity_check, emit):
        require(type(fd) is int and 0 <= fd <= 0x7fffffff, 'invalid held descriptor')
        require(all(callable(callback) for callback in (guard, identity_check, emit)),
                'explicit trusted callbacks required')
        require(sys.platform == 'linux' and ctypes.sizeof(ctypes.c_void_p) == 8
                and platform.machine() in ('aarch64', 'x86_64'),
                'unsupported Linux MTD ABI')
        self._fd = fd
        self._guard = guard
        self._identity_check = identity_check
        self._emit = emit
        self._valid = True
        self._stamp, self._flags = self._descriptor()
        self.guard()
        self._geometry = self._info()
        self._stable()

    def invalidate(self):
        """End this borrowed binding without closing the caller-owned FD."""
        self._valid = False

    def _descriptor(self):
        info = os.fstat(self._fd)
        require(stat.S_ISCHR(info.st_mode) and
                (os.major(info.st_rdev), os.minor(info.st_rdev)) == (90, 0),
                'held descriptor is not the fixed 90:0 MTD character device')
        flags = fcntl.fcntl(self._fd, fcntl.F_GETFL)
        require(type(flags) is int and flags & os.O_ACCMODE == os.O_RDWR and
                not flags & (os.O_APPEND | os.O_PATH),
                'held descriptor must be O_RDWR without O_APPEND/O_PATH')
        stamp = tuple(getattr(info, field) for field in
                      ('st_dev', 'st_ino', 'st_rdev', 'st_mode', 'st_uid',
                       'st_gid', 'st_nlink', 'st_ctime_ns'))
        return stamp, flags

    def _stable(self):
        require(self._descriptor() == (self._stamp, self._flags),
                'held descriptor identity or flags changed')

    def _info(self):
        data = bytearray(MTD_INFO.size)
        result = fcntl.ioctl(self._fd, MEMGETINFO, data, True)
        require(type(result) is int and result == 0, 'invalid MEMGETINFO result')
        kind, flags, size, erase, write, oob, padding = MTD_INFO.unpack(data)
        require((kind, size, erase, write, oob, padding) ==
                (3, SIZE, ERASE, 1, 0, 0) and flags & WRITEABLE,
                'held descriptor has unexpected or nonwriteable NOR geometry')
        return bytes(data)

    def guard(self):
        try:
            require(self._valid, 'adapter binding invalidated')
            self._stable()
            self._guard()
            self._identity_check(self._fd)
            self._stable()
        except BaseException:
            self.invalidate()
            raise

    @staticmethod
    def _range(offset, size, end):
        require(type(offset) is int and type(size) is int and
                0 <= offset < end and 0 < size <= end - offset,
                'invalid SPI byte range')

    def _before_mutation(self):
        self.guard()
        require(self._info() == self._geometry, 'held descriptor geometry changed')
        self._stable()

    def read(self, offset, size):
        try:
            self._range(offset, size, SIZE)
            self.guard()
            data = os.pread(self._fd, size, offset)
            require(type(data) is bytes and len(data) == size,
                    'short or invalid SPI read')
            return data
        except BaseException:
            self.invalidate()
            raise

    def erase(self, offset, size):
        try:
            self._range(offset, size, MUTABLE_END)
            require(size == ERASE and offset % ERASE == 0,
                    'erase requires one aligned unit 0..25')
            self._before_mutation()
            _erase_ioctl(self._fd, ERASE_INFO.pack(offset, size))
        except BaseException:
            self.invalidate()
            raise

    def write(self, offset, data):
        try:
            require(type(data) is bytes and 0 < len(data) <= CHUNK,
                    'write requires immutable nonempty bytes <=4096')
            self._range(offset, len(data), MUTABLE_END)
            require(offset // CHUNK == (offset + len(data) - 1) // CHUNK,
                    'write crosses 4096-byte boundary')
            self._before_mutation()
            count = _pwrite(self._fd, data, offset)
            require(type(count) is int and 0 < count <= len(data),
                    'invalid SPI write count')
            return count
        except BaseException:
            self.invalidate()
            raise

    def emit(self, event):
        try:
            require(self._valid, 'adapter binding invalidated')
            return self._emit(event)
        except BaseException:
            self.invalidate()
            raise

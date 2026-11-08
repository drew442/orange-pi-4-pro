"""Host regressions only: these tests never open physical storage."""
import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'known_sd_writer', Path(__file__).resolve().parents[1] / 'scripts/write-known-bootstrap-sd.py')
w = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(w)


def info(**changes):
    fields = dict(st_dev=os.makedev(0, 1), st_ino=20, st_rdev=os.makedev(179, 0),
                  st_mode=stat.S_IFBLK | 0o600, st_uid=0, st_gid=0,
                  st_nlink=1, st_size=12, st_mtime_ns=1, st_ctime_ns=1)
    fields.update(changes)
    return types.SimpleNamespace(**fields)


class ParsingTests(unittest.TestCase):
    def test_mountinfo_requires_complete_probe(self):
        for raw in ('', '1 2 179:0 / /run rw - tmpfs tmpfs',
                    '1 2 bad / /run rw - tmpfs tmpfs rw'):
            with self.subTest(raw=raw), mock.patch.object(w, 'read_text', return_value=raw):
                with self.assertRaises(w.Refusal):
                    w.mountinfo()
        with mock.patch.object(w, 'read_text', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                w.mountinfo()

    def test_mount_decoding_and_longest_match(self):
        raw = ('1 0 0:1 / / rw - rootfs rootfs rw\n'
               '2 1 0:2 / /run rw - tmpfs tmpfs rw\n'
               '3 2 8:1 / /run/disk rw - ext4 /dev/sda1 rw')
        with mock.patch.object(w, 'read_text', return_value=raw):
            mounts = w.mountinfo()
        self.assertEqual(w.containing_mount('/run/a/image', mounts)['fstype'], 'tmpfs')
        self.assertEqual(w.containing_mount('/run/disk/image', mounts)['fstype'], 'ext4')
        with self.assertRaises(w.Refusal):
            w.containing_mount('/run/a', mounts + [mounts[1]])
        with self.assertRaises(w.Refusal):
            w.unescape_mount('bad\\escape')

    def test_caller_boot_pin_and_root_required(self):
        boot = '11111111-1111-4111-8111-111111111111'
        with mock.patch.object(w.os, 'geteuid', return_value=1000):
            with self.assertRaises(w.Refusal):
                w.host_context(boot)
        with mock.patch.object(w.os, 'geteuid', return_value=0), \
                mock.patch.object(w, 'read_text', return_value='22222222-2222-4222-8222-222222222222'):
            with self.assertRaises(w.Refusal):
                w.host_context(boot)

    def test_namespace_and_hidden_proc_refused(self):
        boot = '11111111-1111-4111-8111-111111111111'
        def read(path):
            return boot if path.endswith('boot_id') else 'NSpid:\t12'
        with mock.patch.object(w.os, 'geteuid', return_value=0), \
                mock.patch.object(w, 'read_text', side_effect=read), \
                mock.patch.object(w.os, 'stat', side_effect=[info(st_ino=1), info(st_ino=2)]):
            with self.assertRaises(w.Refusal):
                w.host_context(boot)
        mounts = [dict(path='/proc', fstype='proc', dev=(0, 1), options=['hidepid=2'])]
        with mock.patch.object(w.os, 'geteuid', return_value=0), \
                mock.patch.object(w, 'read_text', side_effect=read), \
                mock.patch.object(w.os, 'stat', return_value=info()), \
                mock.patch.object(w, 'mountinfo', return_value=mounts):
            with self.assertRaises(w.Refusal):
                w.host_context(boot)


@contextlib.contextmanager
def fixture_root_ownership():
    """Keep real inode/timestamps and simulate only ownership for nonroot tests."""
    original_fstat, original_lstat = os.fstat, os.lstat
    def root(result):
        fields = {name: getattr(result, name) for name in dir(result) if name.startswith('st_')}
        fields['st_uid'] = 0
        return types.SimpleNamespace(**fields)
    with mock.patch.object(w.os, 'fstat', side_effect=lambda fd: root(original_fstat(fd))), \
            mock.patch.object(w.os, 'lstat', side_effect=lambda path: root(original_lstat(path))):
        yield


class SourceTests(unittest.TestCase):
    def test_held_source_hash_identity_and_path(self):
        payload = b'\0tiny fixture\0'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'image'
            path.write_bytes(payload)
            path.chmod(0o600)
            with fixture_root_ownership(), mock.patch.object(w, 'protected_ram_path'), \
                    mock.patch.object(w, 'SOURCE_BYTES', len(payload)), \
                    mock.patch.object(w, 'SOURCE_SHA256', hashlib.sha256(payload).hexdigest()):
                fd, original = w.open_source(str(path), [])
                try:
                    self.assertEqual(os.read(fd, len(payload)), payload)
                    path.write_bytes(payload[:-1] + b'!')
                    with self.assertRaises(w.Refusal):
                        w.validate_source(fd, str(path), original)
                finally:
                    os.close(fd)

    def test_hash_mismatch_and_symlink_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'image'
            path.write_bytes(b'fixture')
            path.chmod(0o600)
            with fixture_root_ownership(), mock.patch.object(w, 'protected_ram_path'), \
                    mock.patch.object(w, 'SOURCE_BYTES', 7):
                with self.assertRaises(w.Refusal):
                    w.open_source(str(path), [])
                link = Path(folder) / 'link'
                link.symlink_to(path)
                with self.assertRaises(OSError):
                    w.open_source(str(link), [])

    def test_changed_path_mode_links_and_size_refused(self):
        regular = info(st_mode=stat.S_IFREG | 0o600, st_size=w.SOURCE_BYTES)
        for change in ({'st_nlink': 2}, {'st_uid': 1000}, {'st_size': 1},
                       {'st_mode': stat.S_IFREG | 0o644}):
            with self.subTest(change=change), \
                    mock.patch.object(w.os, 'fstat', return_value=info(**{
                        'st_mode': regular.st_mode, 'st_size': regular.st_size, **change})), \
                    mock.patch.object(w.os, 'lstat', return_value=regular):
                with self.assertRaises(w.Refusal):
                    w.validate_source(123, '/run/private/image')
        with mock.patch.object(w.os, 'fstat', return_value=regular), \
                mock.patch.object(w.os, 'lstat', return_value=info(st_ino=21)):
            with self.assertRaises(w.Refusal):
                w.validate_source(123, '/run/private/image')

    def test_ram_protection_refuses_noncanonical_and_disk_mounts(self):
        for path in ('relative', '/run/a/../image', '/dev/shm/image', '/proc/image'):
            with self.subTest(path=path), self.assertRaises(w.Refusal):
                w.protected_ram_path(path, [])
        parent = info(st_mode=stat.S_IFDIR | 0o700)
        with mock.patch.object(w.os, 'lstat', return_value=parent):
            with self.assertRaises(w.Refusal):
                w.protected_ram_path('/run/private/image', [
                    dict(path='/run', fstype='ext4', dev=(0, 1))])
        with mock.patch.object(w.os, 'lstat', return_value=info(st_mode=stat.S_IFDIR | 0o777)):
            with self.assertRaises(w.Refusal):
                w.protected_ram_path('/run/private/image', [])


class TargetTests(unittest.TestCase):
    def test_target_exact_identity_and_failures(self):
        values = {'dev': '179:0', 'device/cid': w.CID, 'device/name': 'SU02G',
                  'ro': '0', 'size': str(w.TARGET_BYTES // 512),
                  'queue/logical_block_size': '512'}
        base = '/sys/class/block/mmcblk1/'
        def read(path):
            return values[str(path).removeprefix(base)]
        with mock.patch.object(w.os, 'lstat', return_value=info()), \
                mock.patch.object(w, 'read_text', side_effect=read), \
                mock.patch.object(w.Path, 'iterdir', return_value=iter([])):
            self.assertEqual(w.target_identity()[1], {(179, 0)})
        for key, bad in [('device/cid', 'bad'), ('device/name', 'other'), ('dev', '179:1'),
                         ('ro', ''), ('size', '1'), ('queue/logical_block_size', '4096')]:
            old = values[key]
            values[key] = bad
            with self.subTest(key=key), mock.patch.object(w.os, 'lstat', return_value=info()), \
                    mock.patch.object(w, 'read_text', side_effect=read):
                with self.assertRaises(w.Refusal):
                    w.target_identity()
            values[key] = old
        with mock.patch.object(w.os, 'lstat', return_value=info(st_mode=stat.S_IFLNK | 0o777)):
            with self.assertRaises(w.Refusal):
                w.target_identity()

    def test_partition_numbers_come_from_sysfs_and_failed_probe_is_fatal(self):
        base = '/sys/class/block/mmcblk1/'
        values = {'dev': '179:0', 'device/cid': w.CID, 'device/name': 'SU02G',
                  'ro': '0', 'size': str(w.TARGET_BYTES // 512),
                  'queue/logical_block_size': '512', 'mmcblk1p1/partition': '1',
                  'mmcblk1p1/dev': '179:7'}
        def read(path):
            value = values[str(path).removeprefix(base)]
            if isinstance(value, Exception):
                raise value
            return value
        with mock.patch.object(w.os, 'lstat', return_value=info()), \
                mock.patch.object(w, 'read_text', side_effect=read), \
                mock.patch.object(w.Path, 'iterdir', side_effect=lambda *a: iter([Path(base + 'mmcblk1p1')])), \
                mock.patch.object(w.os, 'stat', return_value=info()):
            self.assertEqual(w.target_identity()[1], {(179, 0), (179, 7)})
            values['mmcblk1p1/dev'] = PermissionError('sysfs unavailable')
            with self.assertRaises(PermissionError):
                w.target_identity()

    def test_ordinary_sysfs_attribute_files_are_not_partition_directories(self):
        base = '/sys/class/block/mmcblk1/'
        values = {'dev': '179:0', 'device/cid': w.CID, 'device/name': 'SU02G',
                  'ro': '0', 'size': str(w.TARGET_BYTES // 512),
                  'queue/logical_block_size': '512', 'mmcblk1p1/partition': '1',
                  'mmcblk1p1/dev': '179:1'}
        def probe(path):
            if str(path).endswith(('uevent/partition', 'dev/partition')):
                raise NotADirectoryError(str(path))
            return info()
        with mock.patch.object(w.os, 'lstat', return_value=info()), \
                mock.patch.object(w, 'read_text', side_effect=lambda p: values[str(p).removeprefix(base)]), \
                mock.patch.object(w.Path, 'iterdir', side_effect=lambda *a: iter(
                    [Path(base + n) for n in ('uevent', 'dev', 'mmcblk1p1')])), \
                mock.patch.object(w.os, 'stat', side_effect=probe):
            self.assertEqual(w.target_identity()[1], {(179, 0), (179, 1)})

    def test_partition_named_nondirectory_is_refused(self):
        base = '/sys/class/block/mmcblk1/'
        values = {'dev': '179:0', 'device/cid': w.CID, 'device/name': 'SU02G',
                  'ro': '0', 'size': str(w.TARGET_BYTES // 512),
                  'queue/logical_block_size': '512'}
        with mock.patch.object(w.os, 'lstat', return_value=info()), \
                mock.patch.object(w, 'read_text', side_effect=lambda p: values[str(p).removeprefix(base)]), \
                mock.patch.object(w.Path, 'iterdir', side_effect=lambda *a: iter([Path(base + 'mmcblk1p1')])), \
                mock.patch.object(w.os, 'stat', side_effect=NotADirectoryError):
            with self.assertRaises(w.Refusal):
                w.target_identity()

    def test_idle_fails_on_mount_swap_holder_and_probe_errors(self):
        with mock.patch.object(w, 'mountinfo', return_value=[{'dev': (179, 1)}]):
            with self.assertRaises(w.Refusal):
                w.idle_target({(179, 0), (179, 1)})
        header = 'Filename Type Size Used Priority'
        for swaps in ('', 'invalid', header + '\n/dev/mmcblk1p1 partition 1 0 -2'):
            with self.subTest(swaps=swaps), \
                    mock.patch.object(w, 'mountinfo', return_value=[]), \
                    mock.patch.object(w, 'read_text', return_value=swaps), \
                    mock.patch.object(w.os, 'stat', return_value=info(st_rdev=os.makedev(179, 1))):
                with self.assertRaises(w.Refusal):
                    w.idle_target({(179, 0), (179, 1)})
        with mock.patch.object(w, 'mountinfo', return_value=[]), \
                mock.patch.object(w, 'read_text', return_value=header), \
                mock.patch.object(w.Path, 'iterdir', return_value=iter([Path('holder')])):
            with self.assertRaises(w.Refusal):
                w.idle_target({(179, 0)})
        with mock.patch.object(w, 'mountinfo', return_value=[]), \
                mock.patch.object(w, 'read_text', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                w.idle_target({(179, 0)})

    def test_process_descriptor_census_including_readonly(self):
        def listing(path):
            if str(path) == '/proc':
                return iter([Path('/proc/1'), Path('/proc/42')])
            return iter([path / '3'])
        with mock.patch.object(w.Path, 'iterdir', listing), \
                mock.patch.object(w.os, 'stat', return_value=info()):
            with self.assertRaises(w.Refusal):
                w.process_consumers({(179, 0)})
        for error in (PermissionError('hidden'), ValueError('malformed')):
            with mock.patch.object(w.Path, 'iterdir', listing), \
                    mock.patch.object(w.os, 'stat', side_effect=error):
                with self.assertRaises(type(error)):
                    w.process_consumers({(179, 0)})
        with mock.patch.object(w.Path, 'iterdir', listing), \
                mock.patch.object(w.os, 'stat', side_effect=FileNotFoundError):
            w.process_consumers({(179, 0)})

    def test_census_excludes_only_own_target_fd_and_process_exit(self):
        mine = str(os.getpid())
        def listing(path):
            if str(path) == '/proc':
                return iter([Path('/proc/1'), Path('/proc/' + mine), Path('/proc/999999999')])
            if str(path) == '/proc/999999999/fd':
                raise FileNotFoundError('exited')
            if str(path) == '/proc/1/fd':
                return iter([])
            return iter([path / '8'])
        with mock.patch.object(w.Path, 'iterdir', listing), \
                mock.patch.object(w.os, 'stat', return_value=info()):
            w.process_consumers({(179, 0)}, own_fd=8)
            with self.assertRaises(w.Refusal):
                w.process_consumers({(179, 0)}, own_fd=9)

    def test_recheck_prevents_opening_changed_target(self):
        before = (1, 2, 3)
        with mock.patch.object(w, 'host_context'), mock.patch.object(w, 'validate_source'), \
                mock.patch.object(w, 'target_identity', return_value=((1, 2, 4), {(179, 0)})), \
                mock.patch.object(w.os, 'open') as opened:
            with self.assertRaises(w.Refusal):
                w.write_payload(10, '/run/private/image', (), 'boot', before)
            opened.assert_not_called()

    def test_opened_block_ioctls_fail_closed(self):
        with mock.patch.object(w.os, 'fstat', return_value=info()), \
                mock.patch.object(w, 'block_integer', side_effect=[w.TARGET_BYTES, 512, 0]):
            w.validate_open_target(3)
        with mock.patch.object(w.os, 'fstat', return_value=info()), \
                mock.patch.object(w, 'block_integer', side_effect=OSError('ioctl failed')):
            with self.assertRaises(OSError):
                w.validate_open_target(3)
        with mock.patch.object(w.os, 'fstat', return_value=info()), \
                mock.patch.object(w, 'block_integer', side_effect=[w.TARGET_BYTES, 512, 1]):
            with self.assertRaises(w.Refusal):
                w.validate_open_target(3)

    def test_write_claim_recheck_and_flush_sequence(self):
        before = (os.makedev(0, 1), 20, os.makedev(179, 0))
        events = []
        def opened(path, flags):
            self.assertEqual(path, w.TARGET)
            for flag in (os.O_RDWR, os.O_NOFOLLOW, os.O_CLOEXEC, os.O_EXCL):
                self.assertTrue(flags & flag)
            events.append('open')
            return 99
        with contextlib.ExitStack() as stack:
            patches = {'host_context': mock.DEFAULT, 'validate_source': mock.DEFAULT,
                       'target_identity': mock.DEFAULT, 'idle_target': mock.DEFAULT,
                       'validate_open_target': mock.DEFAULT, 'stream_write': mock.DEFAULT}
            mocks = stack.enter_context(mock.patch.multiple(w, **patches))
            mocks['target_identity'].return_value = (before, {(179, 0)})
            mocks['stream_write'].side_effect = lambda *a: events.append('write') or w.SOURCE_BYTES
            stack.enter_context(mock.patch.object(w.os, 'open', side_effect=opened))
            stack.enter_context(mock.patch.object(w.os, 'close'))
            stack.enter_context(mock.patch.object(w.os, 'lseek'))
            stack.enter_context(mock.patch.object(w.os, 'fstat', return_value=info()))
            stack.enter_context(mock.patch.object(w.fcntl, 'flock', side_effect=lambda *a: events.append('claim')))
            flush = stack.enter_context(mock.patch.object(w.os, 'fsync', side_effect=lambda *a: events.append('fsync')))
            ioctl = stack.enter_context(mock.patch.object(w.fcntl, 'ioctl', side_effect=lambda *a: events.append('flush')))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            self.assertEqual(w.write_payload(10, '/run/private/image', (), 'boot', before), w.SOURCE_BYTES)
            self.assertEqual(events, ['open', 'claim', 'write', 'fsync', 'flush'])
            mocks['idle_target'].assert_any_call({(179, 0)}, own_fd=99)
            ioctl.assert_called_once_with(99, w.BLKFLSBUF)
            flush.assert_called_once_with(99)


class StreamTests(unittest.TestCase):
    def test_real_regular_files_zeros_shortwrites_and_bounds(self):
        payload = b'abc' + b'\0' * 41 + b'end'
        actual_write = os.write
        def short_write(fd, data):
            return actual_write(fd, data[:3])
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as target:
            source.write(payload + b'NEVER-WRITE-THIS')
            source.seek(0)
            target.write(b'X' * (len(payload) + 10))
            target.seek(0)
            progress = []
            with mock.patch.object(w.os, 'write', side_effect=short_write), \
                    mock.patch.object(w, 'CHUNK', 7):
                self.assertEqual(w.stream_write(source.fileno(), target.fileno(), len(payload),
                                                lambda n, total: progress.append((n, total))), len(payload))
            target.seek(0)
            self.assertEqual(target.read(), payload + b'X' * 10)
            self.assertEqual(source.tell(), len(payload))
            self.assertEqual(progress[-1], (len(payload), len(payload)))

    def test_eof_and_zero_write_refused(self):
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as target:
            with self.assertRaises(w.Refusal):
                w.stream_write(source.fileno(), target.fileno(), 1)
            source.write(b'x')
            source.seek(0)
            with mock.patch.object(w.os, 'write', return_value=0):
                with self.assertRaises(w.Refusal):
                    w.stream_write(source.fileno(), target.fileno(), 1)


if __name__ == '__main__':
    unittest.main()

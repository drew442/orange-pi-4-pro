"""Host regressions only: these tests never open physical storage."""
import contextlib
import hashlib
import importlib.util
import io
import mmap
import os
from pathlib import Path
import stat
import tempfile
import threading
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

    def host_fixture(self, mounts=None, namespace_change=None):
        boot = '11111111-1111-4111-8111-111111111111'
        if mounts is None:
            mounts = [dict(path='/proc', root='/', fstype='proc', dev=(0, 1), options=['rw'])]
        def probe(path):
            ns = str(path).rsplit('/', 1)[-1]
            inode = w.INITIAL_NAMESPACES.get(ns, 99)
            if namespace_change and ns in namespace_change:
                inode = namespace_change[ns]
            return info(st_ino=inode)
        def read(path):
            return boot if str(path).endswith('boot_id') else 'NSpid:\t' + str(os.getpid())
        with mock.patch.object(w.os, 'geteuid', return_value=0), \
                mock.patch.object(w.os, 'stat', side_effect=probe), \
                mock.patch.object(w, 'read_text', side_effect=read), \
                mock.patch.object(w, 'mountinfo', return_value=mounts), \
                mock.patch.object(w, 'require_single_thread'):
            return w.host_context(boot)

    def test_initial_namespace_pins_reject_private_proc_simulation(self):
        self.host_fixture()
        # Both self and the visible PID 1 match, with a single NSpid value:
        # a privately remounted proc in nested PID/user namespaces still fails.
        for namespace in ('pid', 'user'):
            with self.subTest(namespace=namespace), self.assertRaises(w.Refusal):
                self.host_fixture(namespace_change={namespace: 12345})

    def test_full_proc_root_device_and_submount_policy(self):
        base = dict(path='/proc', root='/', fstype='proc', dev=(0, 1), options=['rw'])
        for changes in ({'root': '/123'}, {'dev': (0, 2)}, {'fstype': 'tmpfs'},
                        {'options': ['subset=pid']}, {'options': ['hidepid=2']}):
            with self.subTest(changes=changes), self.assertRaises(w.Refusal):
                self.host_fixture([{**base, **changes}])
        hidden = dict(path='/proc/42/task', root='/', fstype='tmpfs', dev=(0, 2), options=[])
        with self.assertRaises(w.Refusal):
            self.host_fixture([base, hidden])
        allowed = {**hidden, 'path': '/proc/sys/fs/binfmt_misc', 'fstype': 'binfmt_misc'}
        self.host_fixture([base, allowed])


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
    def test_protected_bootstrap_pin(self):
        self.assertEqual(w.SOURCE_BYTES, 268435456)
        self.assertEqual(w.SOURCE_SHA256,
                         "ab5e8c2c846cbbd51b61bef6831d7e4d1498c29fefbf47d74301b1f3bb0ad207")

    def test_held_source_hash_identity_and_path(self):
        payload = b'\0tiny fixture\0'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'image'
            path.write_bytes(payload)
            path.chmod(0o600)
            with fixture_root_ownership(), mock.patch.object(w, 'protected_ram_path'), \
                    mock.patch.object(w, 'PROFILES', {w.DEFAULT_PROFILE:
                        (len(payload), hashlib.sha256(payload).hexdigest())}):
                fd, original = w.open_source(str(path), [])
                try:
                    self.assertEqual(os.read(fd, len(payload)), payload)
                    path.write_bytes(payload[:-1] + b'!')
                    # Filesystem timestamps can share one coarse clock tick.
                    # This check exercises identity change, not adversarial
                    # same-size mutation with deliberately unchanged metadata.
                    os.utime(path, ns=(path.stat().st_atime_ns, original[7] + 1_000_000_000))
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
                    mock.patch.object(w, 'PROFILES', {w.DEFAULT_PROFILE: (7, w.SOURCE_SHA256)}):
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


class ProfileTests(unittest.TestCase):
    def test_finite_profile_pins(self):
        self.assertEqual(dict(w.PROFILES), {
            'protected-state-20261109': (268435456,
                'ab5e8c2c846cbbd51b61bef6831d7e4d1498c29fefbf47d74301b1f3bb0ad207'),
            'nvme-first-trial-20261109': (268435456,
                '8470ff59290016bb2f99c051f371ab20ac431e75fd17dec4e0e0de5c665613d9')})

    def invoke(self, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            status = w.main(args)
        return status, __import__('json').loads(output.getvalue())

    def test_repeated_main_profiles_and_default_do_not_leak(self):
        boot = '11111111-1111-4111-8111-111111111111'
        protected, trial = tuple(w.PROFILES)
        payloads = {protected: b'protected', trial: b'trial----'}
        pins = {name: (len(data), hashlib.sha256(data).hexdigest())
                for name, data in payloads.items()}
        with tempfile.TemporaryDirectory() as folder, fixture_root_ownership(), \
                mock.patch.object(w, 'protected_ram_path'), \
                mock.patch.object(w, 'host_context', return_value=[]), \
                mock.patch.object(w, 'target_identity', return_value=('identity', {(179, 0)})), \
                mock.patch.object(w, 'idle_target'), mock.patch.object(w, 'PROFILES', pins), \
                mock.patch.object(w, 'write_payload', return_value=9) as write:
            for selected in (trial, protected, trial, None):
                profile = selected or protected
                path = Path(folder) / 'image'
                path.write_bytes(payloads[profile])
                path.chmod(0o600)
                args = [str(path), '--expected-boot-id', boot]
                if selected:
                    args += ['--profile', selected]
                status, report = self.invoke(args)
                self.assertEqual(status, 0)
                self.assertEqual(report['status'], 'plan')
                self.assertEqual(report['profile'], profile)
                self.assertEqual(report['source']['sha256'], pins[profile][1])
                status, report = self.invoke(args + ['--write'])
                self.assertEqual(status, 0)
                self.assertEqual(report['status'], 'write-complete')
                self.assertEqual(write.call_args.kwargs['profile'], profile)
                other = trial if profile == protected else protected
                status, report = self.invoke([str(path), '--expected-boot-id', boot,
                                            '--profile', other, '--write'])
                self.assertEqual(status, 1)
                self.assertEqual(report['profile'], other)
                self.assertEqual(report['source']['sha256'], pins[other][1])
                self.assertIn('SHA-256 mismatch', report['error'])
            self.assertEqual(w.SOURCE_SHA256,
                'ab5e8c2c846cbbd51b61bef6831d7e4d1498c29fefbf47d74301b1f3bb0ad207')

    def test_wrong_and_unknown_profile_before_rw_target_open(self):
        with mock.patch.object(w.os, 'open') as opened, \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                w.main(['/run/private/image', '--expected-boot-id', 'boot', '--profile', 'arbitrary'])
            opened.assert_not_called()
        with mock.patch.object(w, 'host_context', return_value=[]), \
                mock.patch.object(w, 'open_source', side_effect=w.Refusal('source SHA-256 mismatch')), \
                mock.patch.object(w.os, 'open') as opened:
            status, report = self.invoke(['/run/private/image', '--expected-boot-id', 'boot',
                                          '--profile', 'nvme-first-trial-20261109', '--write'])
            self.assertEqual(status, 1)
            self.assertEqual(report['profile'], 'nvme-first-trial-20261109')
            opened.assert_not_called()


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

    def census(self, *, thread_fd=False, mapping=False, own_fd=None, error=None,
               table_error=None, maps_error=None, maps_text=None,
               map_range='1000-2000', map_missing=False):
        mine = str(os.getpid())
        def listing(path):
            path = Path(path)
            if str(path) == '/proc/self/task':
                return iter([path / mine])
            if str(path) == '/proc':
                return iter([path / '1', path / mine])
            if path.name == 'task':
                if table_error and path.parent.name == '1':
                    raise table_error
                ids = ['1', '2'] if path.parent.name == '1' else [mine]
                return iter([path / n for n in ids])
            if path.name == 'fd':
                if (thread_fd and path.parent.name == '2') or path.parent.name == mine:
                    return iter([path / '8'])
                return iter([])
            raise AssertionError(str(path))
        def probe(path):
            if 'map_files' in Path(path).parts:
                start, end = (int(value, 16) for value in map_range.split('-'))
                self.assertEqual(str(path), f'/proc/2/map_files/{start:x}-{end:x}')
                if map_missing:
                    raise FileNotFoundError(2, 'missing map_files entry')
            if error:
                raise error
            return info()
        def maps(path):
            if maps_error:
                raise maps_error
            if maps_text is not None:
                return maps_text
            if mapping and str(path) == '/proc/1/task/2/maps':
                # 00:01 is the filesystem holding the node, not 179:0.
                return map_range + ' rw-s 00000000 00:01 20 /dev/renamed-node (deleted)'
            return ''
        with mock.patch.object(w.Path, 'iterdir', autospec=True, side_effect=listing), \
                mock.patch.object(w.os, 'stat', side_effect=probe), \
                mock.patch.object(w, 'read_text', side_effect=maps):
            w.process_consumers({(179, 0)}, own_fd=own_fd)

    def test_thread_only_descriptor_and_closed_fd_mapping_refused(self):
        with self.assertRaisesRegex(w.Refusal, 'descriptor'):
            self.census(thread_fd=True, own_fd=8)
        with self.assertRaisesRegex(w.Refusal, 'mapping'):
            self.census(mapping=True, own_fd=8)

    def test_padded_low_mapping_range_normalized_and_disappearance_checked(self):
        with self.assertRaisesRegex(w.Refusal, 'open process mapping'):
            self.census(mapping=True, own_fd=8, map_range='00400000-00420000')
        # ENOENT cannot silently skip a surviving padded mapping by comparing
        # its normalized basename with the original padded maps range.
        with self.assertRaisesRegex(w.Refusal, 'mapping is unscannable'):
            self.census(mapping=True, own_fd=8, map_range='00400000-00420000',
                        map_missing=True)

    def test_census_canonical_lookup_for_own_real_file_mappings(self):
        mine = str(os.getpid())
        task = Path('/proc') / mine / 'task' / mine
        rows = w.read_text(task / 'maps')
        actual_lstat = os.lstat
        lookups = []
        def listing(directory):
            directory = Path(directory)
            if str(directory) == '/proc':
                return iter([Path('/proc/1'), Path('/proc') / mine])
            if str(directory) == '/proc/1/task':
                return iter([directory / '1'])
            if str(directory) == '/proc/' + mine + '/task':
                return iter([task])
            if directory.name == 'fd':
                return iter([])
            raise AssertionError(str(directory))
        def probe(entry):
            entry = Path(entry)
            self.assertEqual(entry.parent, Path('/proc') / mine / 'map_files')
            self.assertTrue(stat.S_ISLNK(actual_lstat(entry).st_mode))
            lookups.append(entry)
            # The real symlink shape suffices; following these special links
            # may require capabilities unavailable to an ordinary test user.
            return info(st_mode=stat.S_IFREG | 0o600)
        def maps(entry):
            return rows if Path(entry) == task / 'maps' else ''
        with mock.patch.object(w, 'require_single_thread'), \
                mock.patch.object(w.Path, 'iterdir', autospec=True, side_effect=listing), \
                mock.patch.object(w, 'read_text', side_effect=maps), \
                mock.patch.object(w.os, 'stat', side_effect=probe):
            w.process_consumers({(179, 0)})
        self.assertGreater(len(lookups), 0)
        self.assertEqual(len(lookups), sum(int(row.split()[4]) != 0 for row in rows.splitlines()))

    def test_real_proc_map_files_shape_with_closed_file_and_nonleader_thread(self):
        # Use a real harmless mapping and actual proc symlink/directory shape.
        # Only the mapped inode's metadata is simulated as the protected block
        # device; no device is opened, and no privileged map_files stat is needed.
        mine = str(os.getpid())
        ready, stop = threading.Event(), threading.Event()
        tids = []
        def worker():
            tids.append(str(threading.get_native_id()))
            ready.set()
            stop.wait(10)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(ready.wait(5))
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'ordinary-mapped-file'
                path.write_bytes(b'x' * mmap.PAGESIZE)
                with path.open('rb') as source:
                    mapped = mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ)
                try:
                    actual_stat, actual_lstat = os.stat, os.lstat
                    actual_read = w.read_text
                    for tid in (mine, tids[0]):
                        with self.subTest(tid=tid):
                            task = Path('/proc') / mine / 'task' / tid
                            direct = Path('/proc') / tid / 'map_files'
                            self.assertTrue(stat.S_ISDIR(actual_stat(direct).st_mode))
                            with self.assertRaises(FileNotFoundError):
                                actual_stat(task / 'map_files')
                            rows = [row for row in actual_read(task / 'maps').splitlines()
                                    if row.split(maxsplit=5)[-1] == str(path)]
                            self.assertEqual(len(rows), 1)
                            start, end = (int(value, 16) for value in rows[0].split()[0].split('-'))
                            map_range = f'{start:x}-{end:x}'
                            self.assertTrue(stat.S_ISLNK(actual_lstat(direct / map_range).st_mode))
                            def listing(directory):
                                directory = Path(directory)
                                if str(directory) == '/proc':
                                    return iter([Path('/proc/1'), Path('/proc') / mine])
                                if str(directory) == '/proc/1/task':
                                    return iter([directory / '1'])
                                if str(directory) == '/proc/' + mine + '/task':
                                    return iter([task])
                                if directory.name == 'fd':
                                    return iter([])
                                raise AssertionError(str(directory))
                            def probe(entry):
                                self.assertEqual(Path(entry), direct / map_range)
                                self.assertTrue(stat.S_ISLNK(actual_lstat(entry).st_mode))
                                return info()
                            def maps(entry):
                                return rows[0] if Path(entry) == task / 'maps' else ''
                            with mock.patch.object(w, 'require_single_thread'), \
                                    mock.patch.object(w.Path, 'iterdir', autospec=True, side_effect=listing), \
                                    mock.patch.object(w, 'read_text', side_effect=maps), \
                                    mock.patch.object(w.os, 'stat', side_effect=probe):
                                with self.assertRaisesRegex(w.Refusal, 'mapping'):
                                    w.process_consumers({(179, 0)})
                finally:
                    mapped.close()
        finally:
            stop.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())

    def test_census_excludes_only_own_target_fd(self):
        self.census(own_fd=8)
        with self.assertRaisesRegex(w.Refusal, 'descriptor'):
            self.census(own_fd=9)

    def test_process_probe_errors_fail_closed_except_ordinary_exit(self):
        for error in (PermissionError('hidden'), ValueError('malformed'),
                      OSError(5, 'io failure')):
            with self.subTest(error=error), self.assertRaises(type(error)):
                self.census(thread_fd=True, own_fd=8, error=error)
        for error in (FileNotFoundError(2, 'exited'), ProcessLookupError(3, 'exited')):
            self.census(thread_fd=True, own_fd=8, error=error)

    def test_mapping_permission_denial_is_fatal(self):
        with self.assertRaises(PermissionError):
            self.census(mapping=True, own_fd=8, error=PermissionError('map_files denied'))

    def test_surviving_process_with_missing_or_hidden_task_table_refused(self):
        for error in (FileNotFoundError(2, 'leader exited'), PermissionError('hidden')):
            with self.subTest(error=error), self.assertRaises((w.Refusal, PermissionError)):
                self.census(own_fd=8, table_error=error)
        with self.assertRaisesRegex(w.Refusal, 'task is unscannable'):
            self.census(own_fd=8, maps_error=FileNotFoundError(2, 'missing maps'))

    def test_malformed_maps_refused(self):
        for text in ('bad', '2000-1000 rw-s 0 00:01 20',
                     '1000-2000 rw-s invalid 00:01 20'):
            with self.subTest(text=text), self.assertRaises(w.Refusal):
                self.census(own_fd=8, maps_text=text)

    def test_writer_multiple_threads_refused(self):
        with mock.patch.object(w.Path, 'iterdir', return_value=iter([
                Path('/proc/self/task') / str(os.getpid()), Path('/proc/self/task/987')])):
            with self.assertRaisesRegex(w.Refusal, 'single-threaded'):
                w.process_consumers({(179, 0)}, own_fd=8)

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
            profile = 'nvme-first-trial-20261109'
            self.assertEqual(w.write_payload(10, '/run/private/image', (), 'boot', before,
                                             profile=profile), w.PROFILES[profile][0])
            self.assertEqual(mocks['validate_source'].call_count, 3)
            for call in mocks['validate_source'].call_args_list:
                self.assertEqual(call.kwargs['profile'], profile)
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

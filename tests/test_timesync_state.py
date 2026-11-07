"""Clock-state compatibility behavior on ordinary, private host fixtures.

Ownership alone is mocked for unprivileged execution; real inode/type/mode,
links, timestamps, path traversal and descriptor reads remain exercised.
"""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'build/timesync-state.py'
spec = importlib.util.spec_from_file_location('timesync_state', SCRIPT)
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)
UUID = '64504c34-85b4-4035-9618-d86b298f8349'
MARKER = f'a733-state-v1\n{UUID}\n'.encode()


class Ownership:
    def __init__(self, original, owner):
        self.original = original
        self.st_uid, self.st_gid = owner

    def __getattr__(self, name):
        return getattr(self.original, name)


class TimesyncStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'state'
        self.systemd = self.root / 'var/lib/systemd'
        self.systemd.mkdir(parents=True)
        self.root.chmod(0o700)
        self.marker = self.root / '.a733-state'
        self.marker.write_bytes(MARKER)
        self.marker.chmod(0o600)
        self.timesync = self.systemd / 'timesync'
        self.clock = self.timesync / 'clock'
        self.owners = {}
        for path in (self.root, self.root / 'var', self.root / 'var/lib',
                     self.systemd, self.marker):
            if path.is_dir() and path != self.root:
                path.chmod(0o755)
            self.set_owner(path, 0)

    def set_owner(self, path, uid, gid=None):
        info = path.lstat()
        self.owners[(info.st_dev, info.st_ino)] = (uid, uid if gid is None else gid)

    def initialized(self, clock=True):
        self.timesync.mkdir(mode=0o755)
        self.timesync.chmod(0o755)
        self.set_owner(self.timesync, 996)
        if clock:
            self.clock.touch()
            self.clock.chmod(0o644)
            self.set_owner(self.clock, 996)

    def ownership(self):
        original_stat, original_fstat = os.stat, os.fstat

        def wrap(info):
            owner = self.owners.get((info.st_dev, info.st_ino))
            return info if owner is None else Ownership(info, owner)

        stack = ExitStack()
        stack.enter_context(mock.patch.object(state.os, 'stat',
                                             side_effect=lambda *a, **k: wrap(original_stat(*a, **k))))
        stack.enter_context(mock.patch.object(state.os, 'fstat',
                                             side_effect=lambda *a, **k: wrap(original_fstat(*a, **k))))
        return stack

    def audit(self, require=False, root=None):
        with self.ownership():
            return state.audit(self.root if root is None else root, require)

    def reject(self, require=False, root=None):
        with self.assertRaises((OSError, ValueError)):
            self.audit(require, root)

    def initialize(self, expected=UUID):
        # Record ownership changes without requiring chown privileges. All real
        # directory operations and metadata checks still run on the fixture.
        def chown(fd, uid, gid):
            info = os.fstat(fd)
            self.owners[(info.st_dev, info.st_ino)] = (uid, gid)

        with self.ownership(), mock.patch.object(state.os, 'fchown', side_effect=chown):
            return state.initialize(self.root, expected)

    def ready(self, expected=UUID, root=None):
        with self.ownership():
            return state.directory_ready(self.root if root is None else root, expected)

    def test_directory_ready_is_stable_while_clock_changes(self):
        self.initialized(clock=False)
        before = self.ready()
        self.assertEqual(set(before), {'state_uuid', 'directory'})
        self.assertEqual(set(before['directory']), {'uid', 'gid', 'mode', 'device', 'inode', 'nlink'})
        self.clock.touch()
        os.utime(self.clock, ns=(0, 123456789))
        self.assertEqual(self.ready(), before)
        # The live guard never opens or enumerates clock; even an unexpected
        # child is outside its deliberately limited directory proof.
        (self.timesync / 'unexpected').touch()
        with mock.patch.object(state.os, 'scandir', side_effect=AssertionError('enumeration')):
            self.assertEqual(self.ready(), before)

    def test_directory_ready_allows_concurrent_clock_creation_and_ancestor_entries(self):
        self.initialized(clock=False)
        original_fstat = os.fstat
        inode = self.timesync.stat().st_ino
        seen = 0

        def create(fd):
            nonlocal seen
            info = original_fstat(fd)
            if info.st_ino == inode:
                seen += 1
                if seen == 2:
                    self.clock.touch()
                    (self.base / 'other-runtime-file').touch()
                    (self.base / 'other-runtime-directory').mkdir()
                    info = original_fstat(fd)
            return info

        before = self.ready()
        with mock.patch.object(state.os, 'fstat', side_effect=create):
            self.assertEqual(self.ready(), before)
        self.assertTrue(self.clock.exists())

    def test_directory_ready_refuses_missing_unsafe_or_aliased_state(self):
        with self.assertRaises(OSError):
            self.ready()
        self.initialized()
        for expected in ('bad', UUID.upper(), '00000000-0000-0000-0000-000000000000',
                         '14504c34-85b4-4035-9618-d86b298f8349'):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                self.ready(expected)
        for path in (self.root / 'var', self.systemd, self.timesync):
            path.chmod(0o700)
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.ready()
            path.chmod(0o755)
        self.set_owner(self.timesync, 0)
        with self.assertRaises(ValueError):
            self.ready()
        self.set_owner(self.timesync, 996)
        os.link(self.marker, self.root / 'alias')
        with self.assertRaises(ValueError):
            self.ready()

    def test_directory_ready_rejects_symlink_parent_and_leaf(self):
        self.initialized()
        alias = self.base / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            self.ready(root=alias)
        self.timesync.rename(self.systemd / 'original')
        self.timesync.symlink_to('original', target_is_directory=True)
        with self.assertRaises(OSError):
            self.ready()

    def test_directory_ready_rejects_cross_filesystem_and_noncanonical_marker(self):
        self.initialized()
        original = state._owned

        def cross(info, *args, **kwargs):
            original(info, *args, **kwargs)
            if info.st_ino == self.systemd.stat().st_ino:
                info.st_dev = info.st_dev + 1

        # Ownership wrappers allow a changed device without requiring a mount.
        with mock.patch.object(state, '_owned', side_effect=cross), self.assertRaises(ValueError):
            self.ready()
        self.marker.write_bytes(MARKER.replace(UUID.encode(), UUID.upper().encode()))
        with self.assertRaises(ValueError):
            self.ready()

    def test_directory_ready_detects_marker_and_directory_replacement(self):
        self.initialized()
        original = state._readable_pin

        def replace_marker(fd, info):
            readable = original(fd, info)
            self.marker.rename(self.root / 'old-marker')
            self.marker.write_bytes(MARKER)
            return readable

        with mock.patch.object(state, '_readable_pin', side_effect=replace_marker), self.assertRaises(ValueError):
            self.ready()

    def test_directory_ready_detects_concurrent_ownership_and_marker_changes(self):
        self.initialized()
        original = os.fstat
        inode = self.timesync.stat().st_ino

        def change_owner(fd):
            info = original(fd)
            if info.st_ino == inode:
                self.set_owner(self.timesync, 0)
            return info

        with mock.patch.object(state.os, 'fstat', side_effect=change_owner), self.assertRaises(ValueError):
            self.ready()
        self.set_owner(self.timesync, 996)
        original_read = os.read
        count = 0

        def change_marker(fd, limit):
            nonlocal count
            data = original_read(fd, limit)
            count += 1
            if count == 1:
                self.marker.write_bytes(MARKER.replace(b'a733', b'b733'))
            return data

        with mock.patch.object(state.os, 'read', side_effect=change_marker), self.assertRaises(ValueError):
            self.ready()

    def test_initialize_missing_directory_under_restrictive_umask(self):
        previous = os.umask(0o077)
        try:
            report = self.initialize()
        finally:
            os.umask(previous)
        self.assertEqual(report, self.audit())
        self.assertEqual(report['status'], 'uninitialized')
        self.assertEqual(report['metadata']['timesync']['uid'], 996)
        self.assertEqual(report['metadata']['timesync']['gid'], 996)
        self.assertEqual(report['metadata']['timesync']['mode'], '0755')
        self.assertEqual(list(self.timesync.iterdir()), [])
        self.assertFalse(self.clock.exists())

    def test_initialize_accepts_btrfs_style_directory_links(self):
        # Model Btrfs's stable directory nlink=1 convention without requiring
        # a privileged mount. This is not evidence from an actual Btrfs image.
        original_stat, original_fstat = os.stat, os.fstat

        def btrfs_links(info):
            if stat.S_ISDIR(info.st_mode):
                changed = Ownership(info, (info.st_uid, info.st_gid))
                changed.st_nlink = 1
                return changed
            return info

        with mock.patch.object(state.os, 'stat',
                               side_effect=lambda *a, **k: btrfs_links(original_stat(*a, **k))), \
                mock.patch.object(state.os, 'fstat',
                                  side_effect=lambda *a, **k: btrfs_links(original_fstat(*a, **k))):
            report = self.initialize()
            self.assertEqual(report['metadata']['timesync']['nlink'], 1)
            self.assertEqual(report['metadata']['timesync']['mode'], '0755')
            self.assertEqual(list(self.timesync.iterdir()), [])
            self.assertFalse(self.clock.exists())
            self.assertEqual(self.initialize(), report)
            self.clock.touch()
            self.clock.chmod(0o644)
            self.set_owner(self.clock, 996)
            os.utime(self.clock, ns=(2147483640123450000, 2147483640123456789))
            report = self.audit()
            self.assertEqual(self.initialize(), report)
            self.assertEqual(report['clock_mtime_ns'], 2147483640123456789)

    def test_initialize_existing_state_preserves_every_inode_and_timestamp(self):
        for clock in (False, True):
            with self.subTest(clock=clock):
                if not self.timesync.exists():
                    self.initialized(clock=clock)
                elif clock:
                    self.clock.touch()
                    self.clock.chmod(0o644)
                    self.set_owner(self.clock, 996)
                if clock:
                    os.utime(self.clock, ns=(2147483640123450000, 2147483640123456789))
                paths = [self.root, self.marker, self.root / 'var', self.root / 'var/lib',
                         self.systemd, self.timesync] + ([self.clock] if clock else [])
                before = [state._signature(path.lstat()) for path in paths]
                report = self.audit()
                with mock.patch.object(state.os, 'mkdir', side_effect=AssertionError('mkdir')), \
                        mock.patch.object(state.os, 'fchmod', side_effect=AssertionError('chmod')):
                    self.assertEqual(self.initialize(), report)
                    self.assertEqual(self.initialize(), report)
                self.assertEqual(before, [state._signature(path.lstat()) for path in paths])

    def test_initialize_rejects_expected_uuid_before_any_mkdir(self):
        for identity in ('74504c34-85b4-4035-9618-d86b298f8349', UUID.upper(),
                         '00000000-0000-0000-0000-000000000000', '', None):
            with self.subTest(identity=identity), \
                    mock.patch.object(state.os, 'mkdir', side_effect=AssertionError('mkdir')):
                with self.assertRaises(ValueError):
                    self.initialize(identity)
        self.assertFalse(self.timesync.exists())

    def test_initialize_rejects_symlink_parent_and_leaf(self):
        real = self.systemd.with_name('systemd-real')
        self.systemd.rename(real)
        self.systemd.symlink_to(real)
        with self.assertRaises((OSError, ValueError)):
            self.initialize()
        self.systemd.unlink()
        real.rename(self.systemd)
        self.timesync.symlink_to(self.root)
        with self.assertRaises((OSError, ValueError)):
            self.initialize()
        self.assertTrue(self.timesync.is_symlink())

    def test_initialize_rejects_unsafe_existing_state_without_repairs(self):
        self.initialized()
        for path in (self.marker, self.systemd, self.timesync, self.clock):
            with self.subTest(path=path):
                good = path.lstat()
                self.set_owner(path, 997)
                with self.assertRaises(ValueError):
                    self.initialize()
                self.set_owner(path, 996 if path in (self.timesync, self.clock) else 0)
                path.chmod(0o777)
                with self.assertRaises(ValueError):
                    self.initialize()
                self.assertEqual(stat.S_IMODE(path.lstat().st_mode), 0o777)
                path.chmod(stat.S_IMODE(good.st_mode))
        alias = self.root / 'clock-alias'
        os.link(self.clock, alias)
        with self.assertRaises(ValueError):
            self.initialize()
        self.assertEqual(self.clock.lstat().st_nlink, 2)

    def test_initialize_rejects_inaccessible_parent_before_mkdir(self):
        for path in (self.root / 'var', self.root / 'var/lib', self.systemd):
            with self.subTest(parent=path):
                path.chmod(0o700)
                self.assertEqual(self.audit()['status'], 'uninitialized')
                with mock.patch.object(state.os, 'mkdir', side_effect=AssertionError('mkdir')):
                    with self.assertRaises(ValueError):
                        self.initialize()
                self.assertEqual(stat.S_IMODE(path.lstat().st_mode), 0o700)
                path.chmod(0o755)

    def test_initialize_rejects_cross_filesystem_and_invalid_directory_links(self):
        self.initialized(clock=False)
        for path, attribute in ((self.marker, 'st_dev'), (self.systemd, 'st_dev'),
                                (self.timesync, 'st_dev'), (self.timesync, 'st_nlink')):
            inode = path.lstat().st_ino
            with self.subTest(path=path, attribute=attribute), self.ownership():
                original_fstat, original_stat = os.fstat, os.stat

                def altered(info):
                    if info.st_ino == inode:
                        changed = Ownership(info, (info.st_uid, info.st_gid))
                        setattr(changed, attribute, 3 if attribute == 'st_nlink'
                                else getattr(info, attribute) + 1)
                        return changed
                    return info

                with mock.patch.object(state.os, 'fstat',
                                       side_effect=lambda *a, **k: altered(original_fstat(*a, **k))), \
                        mock.patch.object(state.os, 'stat',
                                          side_effect=lambda *a, **k: altered(original_stat(*a, **k))), \
                        mock.patch.object(state.os, 'mkdir', side_effect=AssertionError('mkdir')), \
                        mock.patch.object(state.os, 'fchown', side_effect=AssertionError('chown')):
                    with self.assertRaises(ValueError):
                        state.initialize(self.root, UUID)

    def test_initialize_rejects_parent_binding_replaced_before_write(self):
        original_open = os.open

        def change(name, flags, *args, **kwargs):
            fd = original_open(name, flags, *args, **kwargs)
            if name == '.':
                self.systemd.rename(self.systemd.with_name('systemd-old'))
                self.systemd.mkdir(mode=0o755)
                self.set_owner(self.systemd, 0)
            return fd

        with mock.patch.object(state.os, 'open', side_effect=change):
            with self.assertRaises(ValueError):
                self.initialize()
        self.assertFalse(self.timesync.exists())
        self.assertFalse((self.systemd.with_name('systemd-old') / 'timesync').exists())

    def test_initialize_marker_mutation_before_write_fails_closed(self):
        original_open = os.open
        changed = False

        def change(name, flags, *args, **kwargs):
            nonlocal changed
            fd = original_open(name, flags, *args, **kwargs)
            if name == '.' and not changed:
                changed = True
                self.marker.write_bytes(MARKER.replace(b'64504c34', b'74504c34'))
            return fd

        with mock.patch.object(state.os, 'open', side_effect=change), \
                mock.patch.object(state.os, 'mkdir', side_effect=AssertionError('mkdir')):
            with self.assertRaises(ValueError):
                self.initialize()
        self.assertFalse(self.timesync.exists())

    def test_optional_absent_and_required_absent(self):
        report = self.audit()
        self.assertEqual(report['state_uuid'], UUID)
        self.assertEqual(report['status'], 'uninitialized')
        self.assertIsNone(report['clock_mtime_ns'])
        self.reject(require=True)
        self.assertFalse(self.timesync.exists())

    def test_empty_existing_directory_is_optional(self):
        self.initialized(clock=False)
        self.assertEqual(self.audit()['status'], 'uninitialized')
        self.reject(require=True)
        self.assertFalse(self.clock.exists())

    def test_future_timestamp_and_all_metadata_unchanged(self):
        self.initialized()
        future = 2147483640123456789
        os.utime(self.clock, ns=(future - 12345, future))
        paths = (self.root, self.marker, self.root / 'var', self.root / 'var/lib',
                 self.systemd, self.timesync, self.clock)
        before = [tuple(p.lstat()) + (p.lstat().st_atime_ns, p.lstat().st_mtime_ns,
                                     p.lstat().st_ctime_ns) for p in paths]
        for _ in range(2):
            report = self.audit(require=True)
            self.assertEqual(report['clock_mtime_ns'], future)
            self.assertEqual(report['status'], 'initialized')
            self.assertEqual(report['metadata']['clock']['mode'], '0644')
        after = [tuple(p.lstat()) + (p.lstat().st_atime_ns, p.lstat().st_mtime_ns,
                                    p.lstat().st_ctime_ns) for p in paths]
        self.assertEqual(before, after)
        self.assertEqual(self.clock.read_bytes(), b'')

    def test_marker_framing_and_canonical_nonzero_uuid(self):
        for bad in (MARKER[:-1], MARKER + b'\n', b'x' * 51,
                    MARKER.replace(b'a733-state-v1', b'a733-state-v2'),
                    MARKER.upper(), b'a733-state-v1\n00000000-0000-0000-0000-000000000000\n',
                    MARKER.replace(b'64504c34', b'64504C34'), b'\x00' * 51):
            with self.subTest(marker=bad):
                self.marker.write_bytes(bad)
                self.reject()

    def test_marker_symlink_hardlink_and_unsafe_mode(self):
        self.marker.chmod(0o666)
        self.reject()
        self.marker.chmod(0o600)
        alias = self.root / 'alias'
        os.link(self.marker, alias)
        self.reject()
        alias.unlink()
        self.marker.rename(alias)
        self.marker.symlink_to(alias)
        self.reject()

    def test_symlink_each_parent_is_rejected(self):
        for path in (self.root / 'var', self.root / 'var/lib', self.systemd):
            with self.subTest(parent=path):
                target = path.with_name(path.name + '-real')
                path.rename(target)
                path.symlink_to(target)
                self.reject()
                path.unlink()
                target.rename(path)
        alias = self.base / 'state-link'
        alias.symlink_to(self.root)
        self.reject(root=alias)
        outside = self.base / 'base-link'
        outside.symlink_to(self.base)
        self.reject(root=outside / 'state')

    def test_parent_traversal_rejected(self):
        self.reject(root=self.root / 'var/..')

    def test_parent_ownership_group_and_unsafe_modes(self):
        for path in (self.root, self.root / 'var', self.root / 'var/lib', self.systemd):
            for owner in ((996, 996), (0, 996)):
                with self.subTest(path=path, owner=owner):
                    self.set_owner(path, *owner)
                    self.reject()
                    self.set_owner(path, 0)
            original = stat.S_IMODE(path.stat().st_mode)
            for mode in (0o777, 0o2775, 0o1755):
                with self.subTest(path=path, mode=mode):
                    path.chmod(mode)
                    self.reject()
            path.chmod(original)

    def test_reserved_ownership_is_exact_for_directory_and_clock(self):
        self.initialized()
        for path in (self.timesync, self.clock):
            for owner in ((0, 0), (996, 0), (997, 996)):
                with self.subTest(path=path, owner=owner):
                    self.set_owner(path, *owner)
                    self.reject()
            self.set_owner(path, 996)

    def test_exact_modes(self):
        self.initialized()
        for path, correct, invalid in ((self.timesync, 0o755, (0o700, 0o775, 0o2755)),
                                      (self.clock, 0o644, (0o600, 0o664, 0o4644, 0o755))):
            for mode in invalid:
                with self.subTest(path=path, mode=mode):
                    path.chmod(mode)
                    self.reject()
            path.chmod(correct)

    def test_symlink_directory_and_clock_rejected(self):
        self.initialized()
        self.clock.unlink()
        self.clock.symlink_to(self.marker)
        self.reject()
        self.clock.unlink()
        real = self.systemd / 'timesync-real'
        self.timesync.rename(real)
        self.timesync.symlink_to(real)
        self.reject()

    def test_nonregular_and_hardlinked_clock_rejected(self):
        self.initialized()
        os.link(self.clock, self.root / 'clock-alias')
        self.reject()
        (self.root / 'clock-alias').unlink()
        self.clock.unlink()
        self.clock.mkdir()
        self.reject()
        self.clock.rmdir()
        os.mkfifo(self.clock)
        self.reject()

    def test_nonempty_or_negative_timestamp_clock_rejected(self):
        self.initialized()
        self.clock.write_bytes(b'not a timestamp')
        self.reject()
        self.clock.write_bytes(b'')
        os.utime(self.clock, ns=(-1000000000, -1000000000))
        self.reject()

    def test_extraneous_children_are_rejected(self):
        self.initialized()
        for name in ('private-secret', 'clock.bak'):
            extra = self.timesync / name
            extra.write_bytes(b'secret')
            self.reject()
            extra.unlink()

    def test_concurrent_marker_change_is_rejected(self):
        original_read = os.read

        def changed(fd, size):
            result = original_read(fd, size)
            previous = self.marker.stat()
            self.marker.write_bytes(MARKER.replace(b'64504c34', b'74504c34'))
            # Make the mutation observable on coarse filesystem clocks.
            os.utime(self.marker, ns=(previous.st_atime_ns, previous.st_mtime_ns + 10**9))
            return result

        with mock.patch.object(state.os, 'read', side_effect=changed):
            self.reject()

    def test_concurrent_directory_replacement_is_rejected(self):
        self.initialized()
        original_scandir = os.scandir

        def replace(fd):
            result = original_scandir(fd)
            self.timesync.rename(self.systemd / 'old')
            self.timesync.mkdir(mode=0o755)
            self.set_owner(self.timesync, 996)
            return result

        with mock.patch.object(state.os, 'scandir', side_effect=replace):
            self.reject()

    def test_clock_mutation_during_audit_is_rejected(self):
        self.initialized()
        original_fstat = os.fstat
        seen = 0
        clock_inode = self.clock.stat().st_ino

        def mutate(fd):
            nonlocal seen
            info = original_fstat(fd)
            if info.st_ino == clock_inode:
                seen += 1
                if seen == 2:
                    self.clock.write_bytes(b'concurrent change')
                    info = original_fstat(fd)
            return info

        with mock.patch.object(state.os, 'fstat', side_effect=mutate):
            self.reject()

    def test_absent_clock_created_during_audit_is_rejected(self):
        self.initialized(clock=False)
        original_stat = os.stat

        def appear(name, *args, **kwargs):
            if name == 'clock':
                self.clock.touch()
                self.clock.chmod(0o644)
            return original_stat(name, *args, **kwargs)

        with mock.patch.object(state.os, 'stat', side_effect=appear):
            self.reject()

    def test_marker_reserved_owner_is_rejected(self):
        self.set_owner(self.marker, 996)
        self.reject()

    def test_zero_timestamp_is_valid(self):
        self.initialized()
        os.utime(self.clock, ns=(0, 0))
        self.assertEqual(self.audit(require=True)['clock_mtime_ns'], 0)

    def test_nonregular_marker_never_receives_readable_open(self):
        self.marker.unlink()
        os.mkfifo(self.marker)
        self.marker.chmod(0o600)
        self.set_owner(self.marker, 0)
        original_open = os.open
        marker_flags = []

        def record(name, flags, *args, **kwargs):
            if name == '.a733-state':
                marker_flags.append(flags)
                self.assertTrue(flags & os.O_PATH)
            return original_open(name, flags, *args, **kwargs)

        with mock.patch.object(state.os, 'open', side_effect=record):
            self.reject()
        self.assertEqual(len(marker_flags), 1)

    def test_marker_name_replacement_cannot_redirect_readable_open(self):
        original_open = os.open
        read_results = []
        original_read = os.read
        replaced = False

        def replace(name, flags, *args, **kwargs):
            nonlocal replaced
            if name == '.a733-state':
                self.assertTrue(flags & os.O_PATH)
                descriptor = original_open(name, flags, *args, **kwargs)
                if not replaced:
                    replaced = True
                    self.marker.rename(self.root / 'original-marker')
                    os.mkfifo(self.marker)
                return descriptor
            return original_open(name, flags, *args, **kwargs)

        def record(fd, size):
            result = original_read(fd, size)
            read_results.append(result)
            return result

        with mock.patch.object(state.os, 'open', side_effect=replace), \
                mock.patch.object(state.os, 'read', side_effect=record):
            self.reject()
        self.assertEqual(read_results, [MARKER])

    def test_unavailable_procfs_verification_fails_closed(self):
        with mock.patch.object(state.ctypes, 'CDLL', return_value=object()):
            self.reject()

    def test_procfs_pid_namespace_mismatch_fails_closed(self):
        with mock.patch.object(state.os, 'readlink', return_value='different-pid'):
            self.reject()

    def test_cli_reports_only_evidence_or_generic_failure(self):
        self.initialized()
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.ownership(), redirect_stdout(stdout), redirect_stderr(stderr), \
                mock.patch.object(sys, 'argv', [str(SCRIPT), str(self.root), '--require-clock']):
            self.assertEqual(state.main(), 0)
        self.assertEqual(json.loads(stdout.getvalue())['state_uuid'], UUID)
        self.assertNotIn(str(self.root), stdout.getvalue())
        self.clock.write_bytes(b'private-secret')
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.ownership(), redirect_stdout(stdout), redirect_stderr(stderr), \
                mock.patch.object(sys, 'argv', [str(SCRIPT), str(self.root)]):
            self.assertEqual(state.main(), 1)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stderr.getvalue(), 'timesync state audit failed\n')


if __name__ == '__main__':
    unittest.main()

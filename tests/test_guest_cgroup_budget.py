"""Private ordinary-filesystem probes; cgroup filesystem/membership substituted.

No cgroup creation, migration, controller writes, installed units or KVM occur.
Actual retained FD metadata, names, read bounds and cleanup are exercised.
"""
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

BASE = Path(__file__).resolve().parents[1]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

M = load('cgroup_budget_test', 'scripts/guest-cgroup-budget.py')
F = load('cgroup_budget_permit', 'tests/test_guest_run_permit.py')


class ParsingTests(unittest.TestCase):
    def test_fstatfs_observes_real_filesystem(self):
        fd = os.open('/proc/self', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            self.assertEqual(M.filesystem(fd), 0x9fa0)
        finally:
            os.close(fd)

    def test_mount_rejects_legacy_alias_private_and_nested_mounts(self):
        good = b'20 1 0:30 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n'
        self.assertEqual(M.mount_identity(good)[2], '0:30')
        bad = [b'', good + good, good.replace(b' / /sys', b' /private /sys'),
               good.replace(b'/sys/fs/cgroup', b'/private'),
               good + b'21 20 0:1 / /other rw - cgroup cpu rw\n',
               good + b'21 20 0:1 / /sys/fs/cgroup/child rw - tmpfs tmpfs rw\n',
               good + b'21 20 0:1 / /sys/fs/cgroup\\057child rw - tmpfs tmpfs rw\n']
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                M.mount_identity(data)


@unittest.skipUnless(os.geteuid() == 0, 'root-owned private cgroup surrogate')
class RetainedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cgroup-budget-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = F.permit()['config']
        self.path = '/a733.slice/a733-guests.slice/a733-managed-' + self.config['name'] + '.service'
        self.mount = self.root / 'sys/fs/cgroup'
        self.unit = Path(str(self.mount) + self.path)
        self.unit.mkdir(parents=True)
        self.slice = self.unit.parent
        for directory in (self.slice, self.unit):
            memory = 10240 * M.UNITS.MIB if directory == self.slice else self.config['memory_max_mib'] * M.UNITS.MIB
            for name, value in (('memory.max', str(memory)), ('memory.swap.max', '0'),
                                ('pids.max', '4096' if directory == self.slice else '256'),
                                ('cgroup.type', 'domain')):
                (directory / name).write_text(value + '\n')
        dev = self.mount.stat().st_dev
        self.mountinfo = f'20 1 {os.major(dev)}:{os.minor(dev)} / /sys/fs/cgroup rw - cgroup2 cgroup rw\n'.encode()
        self.membership = ('0::' + self.path + '\n').encode()
        self.meminfo = b'MemTotal:       12582912 kB\n'
        def native(path, limit):
            return {'/proc/self/cgroup': self.membership,
                    '/proc/self/mountinfo': self.mountinfo,
                    '/proc/meminfo': self.meminfo}[path]
        for patch in (mock.patch.object(M, 'ROOT', str(self.root)),
                      mock.patch.object(M, 'read_native', side_effect=native),
                      mock.patch.object(M, 'filesystem', return_value=M.MAGIC)):
            patch.start()
            self.addCleanup(patch.stop)

    def guard(self):
        value = M.CgroupBudget(self.config)
        self.addCleanup(value.close)
        return value

    def test_retained_readonly_cloexec_context_and_closed_checks(self):
        with self.guard() as value:
            self.assertEqual(len(value._fds), 15)
            value.check()
            self.assertTrue(all(not os.get_inheritable(fd) for fd in value._fds))
        value.close()
        with self.assertRaisesRegex(ValueError, 'closed'):
            value.check()

    def test_limit_and_type_changes_refuse(self):
        for name, content in (('memory.max', 'max\n'), ('memory.swap.max', '1\n'),
                              ('pids.max', '257\n'), ('cgroup.type', 'threaded\n'),
                              ('memory.max', '0' * 130)):
            with self.subTest(name=name, content=content):
                path = self.unit / name
                previous = path.read_bytes()
                value = self.guard()
                path.write_text(content)
                with self.assertRaises(ValueError):
                    value.check()
                value.close()
                path.write_bytes(previous)

    def test_parent_limits_exact_not_merely_tighter(self):
        for content in ('max\n', '1\n', str(10241 * M.UNITS.MIB) + '\n'):
            (self.slice / 'memory.max').write_text(content)
            with self.assertRaisesRegex(ValueError, 'limit/type'):
                M.CgroupBudget(self.config)

    def test_membership_refuses_mixed_deleted_escaped_private_otherunit(self):
        for membership in (b'1:memory:/x\n' + self.membership, self.membership + self.membership,
                           self.membership.replace(b'\n', b' (deleted)\n'),
                           self.membership.replace(b'a733.slice', b'a733\\056slice'),
                           b'0::/private\n', self.membership.replace(b'.service', b'.scope')):
            with self.subTest(membership=membership):
                self.membership = membership
                with self.assertRaisesRegex(ValueError, 'membership'):
                    M.CgroupBudget(self.config)

    def test_memtotal_duplicate_invalid_or_budget_exhausted_refuses(self):
        for data in (self.meminfo * 2, b'MemTotal: max kB\n', b'MemTotal: 1 MB\n',
                     b'MemTotal: 2097152 kB\n', b'MemTotal: 0 kB\n'):
            self.meminfo = data
            with self.subTest(data=data), self.assertRaises(ValueError):
                M.CgroupBudget(self.config)

    def test_named_inode_replacement_symlink_and_metadata_refuse(self):
        value = self.guard()
        path = self.unit / 'pids.max'
        path.rename(self.unit / 'old')
        path.write_text('256\n')
        with self.assertRaisesRegex(ValueError, 'metadata/name'):
            value.check()
        value.close()
        path.unlink()
        path.symlink_to(self.unit / 'old')
        with self.assertRaises(OSError):
            M.CgroupBudget(self.config)
        path.unlink()
        (self.unit / 'old').rename(path)
        path.chmod(0o666)
        with self.assertRaisesRegex(ValueError, 'metadata/name'):
            M.CgroupBudget(self.config)

    def test_descriptor_substitution_access_and_inheritability_refuse(self):
        for flags in (os.O_WRONLY, os.O_PATH):
            value = self.guard()
            target = value._limits[0][0]
            replacement = os.open(self.slice / 'memory.max', flags | os.O_CLOEXEC)
            try:
                os.dup2(replacement, target, inheritable=False)
            finally:
                os.close(replacement)
            with self.assertRaisesRegex(ValueError, 'descriptor access'):
                value.check()
            value.close()
        value = self.guard()
        os.set_inheritable(value._limits[0][0], True)
        with self.assertRaisesRegex(ValueError, 'descriptor access'):
            value.check()

    def test_filesystem_and_mount_change_refuse(self):
        value = self.guard()
        with mock.patch.object(M, 'filesystem', return_value=0x1021994):
            with self.assertRaisesRegex(ValueError, 'filesystem'):
                value.check()
        self.mountinfo = self.mountinfo.replace(b'20 1', b'21 1')
        with self.assertRaisesRegex(ValueError, 'mount changed'):
            value.check()

    def test_creator_required_even_after_close_and_uid_drop_cleanup_allowed(self):
        value = self.guard()
        value.close()
        for method in (value.check, value.close, value.__enter__):
            with mock.patch.object(M.os, 'getpid', return_value=os.getpid() + 1):
                with self.assertRaisesRegex(ValueError, 'creator'):
                    method()
        errors = []
        def other_thread():
            try:
                value.close()
            except ValueError as error:
                errors.append(error)
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        value = self.guard()
        with mock.patch.object(M.os, 'geteuid', return_value=60002):
            value.close()

    def test_constructor_attempts_all_cleanup_and_retains_primary(self):
        real_close = os.close
        closed = []
        def close(fd):
            closed.append(fd)
            real_close(fd)
            raise OSError('reported cleanup failure')
        with mock.patch.object(M, 'read_fd', side_effect=ValueError('primary limit read')), \
                mock.patch.object(M.os, 'close', side_effect=close):
            with self.assertRaises(M.CleanupError) as caught:
                M.CgroupBudget(self.config)
        self.assertIsInstance(caught.exception.primary, ValueError)
        self.assertEqual(str(caught.exception.primary), 'primary limit read')
        self.assertEqual(len(closed), 15)
        self.assertEqual(len(caught.exception.errors), 15)

    def test_pseudofile_zero_size_uses_actual_contents(self):
        real_fstat = os.fstat
        def pseudo_info(fd):
            info = real_fstat(fd)
            fields = list(info)
            fields[6] = 0
            return os.stat_result(fields)
        with mock.patch.object(M.os, 'fstat', side_effect=pseudo_info):
            value = self.guard()
            value.check()
            (self.unit / 'pids.max').write_text('max\n')
            with self.assertRaisesRegex(ValueError, 'limit/type'):
                value.check()

    def test_directory_replacement_and_group_write_refuse(self):
        value = self.guard()
        self.unit.rename(self.unit.with_name('old.service'))
        self.unit.mkdir()
        with self.assertRaisesRegex(ValueError, 'metadata/name'):
            value.check()
        value.close()
        self.unit.rmdir()
        self.unit.with_name('old.service').rename(self.unit)
        self.slice.chmod(0o775)
        with self.assertRaisesRegex(ValueError, 'metadata/name'):
            M.CgroupBudget(self.config)


if __name__ == '__main__':
    unittest.main()

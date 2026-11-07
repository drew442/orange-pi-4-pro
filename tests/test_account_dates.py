"""Build-only account-date policy regressions; no hardware acceptance."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('account_dates', Path(__file__).resolve().parents[1] / 'build/account-dates.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def fixture(profile='baseline', name='shadow', day=b'20765'):
    return b''.join(user + b':!:' + day + b':0:99999:7:::\n'
                    for user in m._policy(profile, name))


class ParserTests(unittest.TestCase):
    def test_closed_profiles_and_days(self):
        self.assertEqual(len(m._policy('baseline', 'shadow')), 23)
        self.assertEqual(len(m._policy('baseline', 'shadow-')), 22)
        self.assertEqual(len(m._policy('time', 'shadow')), 24)
        self.assertEqual(len(m._policy('time', 'shadow-')), 23)
        for profile in ('baseline', 'time'):
            for name in ('shadow', 'shadow-'):
                self.assertEqual(m._normalize_bytes(fixture(profile, name, b'20763'), profile, name),
                                 m._normalize_bytes(fixture(profile, name, b'30000'), profile, name))
        self.assertIn(b'systemd-timesync:!:20764:', m._normalize_bytes(fixture('time'), 'time', 'shadow'))

    def test_preserve_every_other_byte_and_order(self):
        data = b''.join(reversed(fixture().replace(b':0:99999:7:::', b':000:123:08:90:12:1').splitlines(True)))
        output = m._normalize_bytes(data, 'baseline', 'shadow')
        for before, after in zip(data.splitlines(), output.splitlines()):
            a, b = before.split(b':'), after.split(b':')
            self.assertEqual(a[:2] + a[3:], b[:2] + b[3:])
            self.assertEqual(b[2], b'20763')

    def test_reject_malformed_unknown_duplicate_missing_dates_and_passwords(self):
        base = fixture()
        variants = [base.replace(b'root:', b'unknown:', 1), base + base.splitlines(True)[0],
                    b''.join(base.splitlines(True)[1:]), base[:-1], base + b'\n',
                    base.replace(b':20765:', b'::', 1), base.replace(b':20765:', b':-1:', 1),
                    base.replace(b':20765:', b':01:', 1), base.replace(b':20765:', b':x:', 1),
                    base.replace(b':0:', b':x:', 1), base.replace(b':0:', b':0\r:', 1),
                    base.replace(b':!:', b'::', 1), base.replace(b':!:', b':unlocked:', 1),
                    base.replace(b':!:', b':!opaque-hash:', 1), base.replace(b':!:', b':*opaque:', 1),
                    base.replace(b'root:', b'root', 1), b'x' * (m.LIMIT + 1)]
        for data in variants:
            with self.subTest(case=variants.index(data)), self.assertRaises(ValueError):
                m._normalize_bytes(data, 'baseline', 'shadow')

    def test_only_literal_public_markers(self):
        for marker in m.MARKERS:
            data = fixture().replace(b':!:', b':' + marker + b':')
            self.assertEqual(m._normalize_bytes(data, 'baseline', 'shadow'), data.replace(b'20765', b'20763'))

    def test_unknown_policy_rejected(self):
        for profile, name in [('other', 'shadow'), ('baseline', 'passwd')]:
            with self.assertRaises(ValueError):
                m._normalize_bytes(fixture(), profile, name)


@unittest.skipUnless(os.geteuid() == 0, 'requires namespace root for real 0:42 metadata')
class FilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='a733-account-dates-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.etc = self.root / 'etc'
        self.etc.mkdir(mode=0o755)
        self.populate()

    def put(self, name, data, mode=0o640, gid=42):
        path = self.etc / name
        path.write_bytes(data)
        os.chown(path, 0, gid)
        path.chmod(mode)
        os.utime(path, ns=(123456789012, 987654321098))
        return path

    def populate(self, profile='baseline', day=b'20765'):
        for name in ('shadow', 'shadow-'):
            self.put(name, fixture(profile, name, day))
        if profile == 'time':
            self.put('passwd', b'root:x:0:0:root:/root:/bin/bash\n' + m.TIME_PASSWD + b'\n', 0o644, 0)

    def snapshot(self):
        return {n: ((self.etc / n).read_bytes(), (self.etc / n).stat()) for n in ('shadow', 'shadow-')}

    def assert_unchanged(self, before):
        for n, (data, info) in before.items():
            self.assertEqual((self.etc / n).read_bytes(), data)
            self.assertEqual(m._identity((self.etc / n).stat()), m._identity(info))

    def test_convergence_metadata_xattrs_and_noop_ctime(self):
        for profile in ('baseline', 'time'):
            self.populate(profile, b'20765')
            path = self.etc / 'shadow'
            os.setxattr(path, 'user.account-dates-test', b'preserve')
            before = self.snapshot()
            self.assertEqual(m.normalize(self.root, profile), ('shadow', 'shadow-'))
            expected = self.snapshot()
            for n, (_, info) in before.items():
                after = (self.etc / n).stat()
                self.assertEqual((after.st_uid, after.st_gid, after.st_mode, after.st_mtime_ns, after.st_ino),
                                 (info.st_uid, info.st_gid, info.st_mode, info.st_mtime_ns, info.st_ino))
            self.assertEqual(os.getxattr(path, 'user.account-dates-test'), b'preserve')
            self.assertEqual(m.normalize(self.root, profile), ())
            self.assert_unchanged(expected)
            self.populate(profile, b'30000')
            m.normalize(self.root, profile)
            for n, (data, _) in expected.items():
                self.assertEqual((self.etc / n).read_bytes(), data)

    def test_both_prevalidated_before_mutation(self):
        self.put('shadow-', fixture(name='shadow-').replace(b'root:', b'unknown:', 1))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            m.normalize(self.root, 'baseline')
        self.assert_unchanged(before)

    def test_time_identity_and_baseline_sets_required(self):
        self.populate('time')
        self.put('passwd', m.TIME_PASSWD.replace(b':996:996:', b':995:996:') + b'\n', 0o644, 0)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            m.normalize(self.root, 'time')
        self.assert_unchanged(before)
        with self.assertRaises(ValueError):
            m.normalize(self.root, 'baseline')
        self.assert_unchanged(before)

    def test_symlink_file_and_hardlink_rejected(self):
        path = self.etc / 'shadow-'
        original = self.etc / 'saved'
        path.rename(original)
        path.symlink_to(original.name)
        before = (self.etc / 'shadow').read_bytes()
        with self.assertRaises(OSError):
            m.normalize(self.root, 'baseline')
        path.unlink()
        os.link(original, path)
        with self.assertRaises(ValueError):
            m.normalize(self.root, 'baseline')
        self.assertEqual((self.etc / 'shadow').read_bytes(), before)

    def test_unsafe_directories_and_live_root_rejected(self):
        with self.assertRaises(ValueError):
            m.normalize('/', 'baseline')
        self.root.chmod(0o777)
        with self.assertRaises(ValueError):
            m.normalize(self.root, 'baseline')
        self.root.chmod(0o700)
        old = self.root / 'saved-etc'
        self.etc.rename(old)
        self.etc.symlink_to(old.name)
        with self.assertRaises(OSError):
            m.normalize(self.root, 'baseline')

    def test_file_metadata_and_fifo_rejected(self):
        for mode, uid, gid in ((0o600, 0, 42), (0o640, 0, 0), (0o640, 1, 42)):
            path = self.etc / 'shadow-'
            path.chmod(mode)
            os.chown(path, uid, gid)
            with self.assertRaises(ValueError):
                m.normalize(self.root, 'baseline')
        path.unlink()
        os.mkfifo(path, 0o640)
        with self.assertRaises(ValueError):
            m.normalize(self.root, 'baseline')

    def test_time_passwd_symlink_and_duplicate_rejected(self):
        self.populate('time')
        self.put('passwd', m.TIME_PASSWD + b'\n' + m.TIME_PASSWD + b'\n', 0o644, 0)
        with self.assertRaises(ValueError):
            m.normalize(self.root, 'time')
        path = self.etc / 'passwd'
        path.rename(self.etc / 'saved-passwd')
        path.symlink_to('saved-passwd')
        with self.assertRaises(OSError):
            m.normalize(self.root, 'time')

    def test_changed_directory_permissions_rejected_before_write(self):
        original = m._normalize_bytes
        def racing(data, profile, name):
            result = original(data, profile, name)
            if name == 'shadow-':
                self.etc.chmod(0o777)
            return result
        before = self.snapshot()
        with mock.patch.object(m, '_normalize_bytes', side_effect=racing), self.assertRaises(ValueError):
            m.normalize(self.root, 'baseline')
        self.assert_unchanged(before)

    def test_changed_time_identity_rejected_before_write(self):
        self.populate('time')
        original = m._read
        def racing(fd, info):
            data = original(fd, info)
            if data.startswith(b'root:x:'):
                path = self.etc / 'passwd'
                path.write_bytes(data.replace(b':996:996:', b':995:996:'))
            return data
        before = self.snapshot()
        with mock.patch.object(m, '_read', side_effect=racing), self.assertRaises(ValueError):
            m.normalize(self.root, 'time')
        self.assert_unchanged(before)

    def test_write_failure_is_reported_and_can_leave_partial_clone(self):
        original = os.pwrite
        count = 0
        before = self.snapshot()
        def failing(fd, data, offset):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('injected build storage failure')
            return original(fd, data, offset)
        with mock.patch.object(m.os, 'pwrite', side_effect=failing), self.assertRaises(OSError):
            m.normalize(self.root, 'baseline')
        self.assertEqual((self.etc / 'shadow').read_bytes(),
                         m._normalize_bytes(before['shadow'][0], 'baseline', 'shadow'))
        self.assertEqual((self.etc / 'shadow-').read_bytes(), before['shadow-'][0])

    def test_changed_contents_rejected_before_any_write(self):
        original = m._normalize_bytes
        count = 0
        def racing(data, profile, name):
            nonlocal count
            result = original(data, profile, name)
            count += 1
            if count == 2:
                (self.etc / 'shadow').write_bytes(data)
            return result
        before_backup = (self.etc / 'shadow-').read_bytes()
        with mock.patch.object(m, '_normalize_bytes', side_effect=racing), self.assertRaises(ValueError):
            m.normalize(self.root, 'baseline')
        self.assertEqual((self.etc / 'shadow-').read_bytes(), before_backup)

    def test_changed_path_rejected_before_any_write(self):
        original = m._normalize_bytes
        def racing(data, profile, name):
            result = original(data, profile, name)
            if name == 'shadow-':
                path = self.etc / 'shadow'
                path.rename(self.etc / 'original-shadow')
                self.put('shadow', fixture(day=b'30000'))
            return result
        before = (self.etc / 'shadow-').read_bytes()
        with mock.patch.object(m, '_normalize_bytes', side_effect=racing), self.assertRaises(ValueError):
            m.normalize(self.root, 'baseline')
        self.assertEqual((self.etc / 'shadow-').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()

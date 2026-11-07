"""Offline identity invariants; no package installation or daemon execution."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'build/timesync-identity.py'
spec = importlib.util.spec_from_file_location('timesync_identity', SCRIPT)
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)

PASSWD = ('root:x:0:0:root:/root:/bin/bash\n'
          'systemd-network:x:998:998:Network Management:/:/usr/sbin/nologin\n'
          'systemd-resolve:x:997:997:Name Resolution:/:/usr/sbin/nologin\n')
GROUP = ('root:x:0:\n'
         'systemd-journal:x:999:\n'
         'systemd-network:x:998:\n'
         'systemd-resolve:x:997:\n')
SERVICE_USER = 'systemd-timesync:x:996:996:systemd Time Synchronization:/:/usr/sbin/nologin\n'
SERVICE_GROUP = 'systemd-timesync:x:996:\n'


class TimesyncIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.make_root('before')

    def make_root(self, name, after=False):
        root = self.base / name
        (root / 'etc').mkdir(parents=True)
        (root / 'etc/passwd').write_text(PASSWD + (SERVICE_USER if after else ''))
        (root / 'etc/group').write_text(GROUP + (SERVICE_GROUP if after else ''))
        return root

    def audit(self, phase='before', root=None):
        return identity.audit(root or self.root, phase)

    def replace(self, file, data):
        (self.root / 'etc' / file).write_text(data)

    def assert_invalid(self, phase='before'):
        with self.assertRaises((ValueError, OSError)):
            self.audit(phase)

    def test_before_reserves_unused_name_and_identity_with_hashes(self):
        result = self.audit()
        self.assertEqual(result['reservation'], 'unused')
        self.assertEqual((result['uid'], result['gid']), (996, 996))
        self.assertEqual(result['sha256']['passwd'], hashlib.sha256(PASSWD.encode()).hexdigest())
        self.assertNotIn('root', json.dumps(result))

    def test_after_accepts_exact_identity_and_before_refuses_it(self):
        self.root = self.make_root('after', True)
        self.assertEqual(self.audit('after')['reservation'], 'exact')
        self.assert_invalid()

    def test_after_requires_user_and_group(self):
        self.assert_invalid('after')
        self.replace('passwd', PASSWD + SERVICE_USER)
        self.assert_invalid('after')

    def test_reserved_numeric_uid_and_primary_gid(self):
        for uid, gid in ((996, 40), (40, 996)):
            with self.subTest(uid=uid, gid=gid):
                self.replace('passwd', PASSWD + f'other:x:{uid}:{gid}::/:/bin/false\n')
                self.assert_invalid()

    def test_reserved_group_or_names(self):
        for file, data in (
            ('group', GROUP + 'other:x:996:\n'),
            ('group', GROUP + 'systemd-timesync:x:40:\n'),
            ('passwd', PASSWD + 'systemd-timesync:x:40:40::/:/bin/false\n'),
            ('group', GROUP + 'other:x:40:systemd-timesync\n'),
        ):
            with self.subTest(file=file, data=data):
                self.replace('passwd', PASSWD)
                self.replace('group', GROUP)
                self.replace(file, data)
                self.assert_invalid()

    def test_after_rejects_other_primary_gid_alias(self):
        self.root = self.make_root('after', True)
        self.replace('passwd', PASSWD + SERVICE_USER + 'alias:x:50:996::/:/bin/false\n')
        self.assert_invalid('after')

    def test_after_rejects_inexact_service_fields(self):
        self.root = self.make_root('after', True)
        for changed in (
            SERVICE_USER.replace(':996:996:', ':995:996:'),
            SERVICE_USER.replace(':996:996:', ':996:995:'),
            SERVICE_USER.replace(':/:', ':/var/lib/systemd/timesync:'),
            SERVICE_USER.replace('/usr/sbin/nologin', '/bin/bash'),
            SERVICE_USER.replace(':x:', ':secret:'),
        ):
            with self.subTest(changed=changed):
                self.replace('passwd', PASSWD + changed)
                self.assert_invalid('after')
        self.replace('passwd', PASSWD + SERVICE_USER)
        self.replace('group', GROUP + SERVICE_GROUP.rstrip('\n') + 'root\n')
        self.assert_invalid('after')

    def test_duplicate_names_and_uid_gid_aliases_fail(self):
        for file, addition in (
            ('passwd', 'root:x:40:40::/:/bin/false\n'),
            ('passwd', 'alias:x:998:998::/:/bin/false\n'),
            ('group', 'root:x:40:\n'),
            ('group', 'alias:x:998:\n'),
        ):
            with self.subTest(file=file, addition=addition):
                self.replace('passwd', PASSWD)
                self.replace('group', GROUP)
                original = PASSWD if file == 'passwd' else GROUP
                self.replace(file, original + addition)
                self.assert_invalid()

    def test_invalid_numeric_fields_anywhere_fail(self):
        for malformed in ('-1', '+40', ' 40', '040', '1.0', '', '4294967295', '65535', 'nobody'):
            for field in (2, 3):
                with self.subTest(malformed=malformed, field=field):
                    fields = ['other', 'x', '40', '40', '', '/', '/bin/false']
                    fields[field] = malformed
                    self.replace('passwd', PASSWD + ':'.join(fields) + '\n')
                    self.assert_invalid()
        self.replace('passwd', PASSWD)
        self.replace('group', GROUP + 'other:x:bad:\n')
        self.assert_invalid()

    def test_unrelated_locked_passwords_and_members_are_accepted(self):
        self.replace('passwd', PASSWD + 'other:!:40:0::/:/bin/false\n')
        self.replace('group', GROUP + 'other:*:40:root,systemd-network\n')
        self.audit()

    def test_malformed_records_and_controls_fail(self):
        for bad in ('garbage\n', '\n', 'other:x:40:40::/:/bin/false',
                    'other:x:40:40::/:/bin/false\r\n',
                    'other:x:40:40::/:/bin/false\vsecond:x:41:41::/:/bin/false\n'):
            with self.subTest(bad=bad):
                self.replace('passwd', PASSWD + bad)
                self.assert_invalid()

    def test_bounded_file_line_and_nonregular_input(self):
        self.replace('passwd', 'x' * (identity.MAX_BYTES + 1))
        self.assert_invalid()
        self.replace('passwd', 'other:x:40:40:' + 'x' * identity.MAX_LINE_BYTES + ':/:/bin/false\n')
        self.assert_invalid()
        path = self.root / 'etc/passwd'
        path.unlink()
        os.mkfifo(path)
        self.assert_invalid()

    def test_account_and_etc_symlinks_fail(self):
        passwd = self.root / 'etc/passwd'
        target = self.base / 'passwd'
        passwd.rename(target)
        passwd.symlink_to(target)
        self.assert_invalid()
        passwd.unlink()
        target.rename(passwd)
        etc = self.root / 'etc'
        physical = self.base / 'physical-etc'
        etc.rename(physical)
        etc.symlink_to(physical, target_is_directory=True)
        self.assert_invalid()

    def test_root_parent_symlink_and_traversal_fail(self):
        linked = self.base / 'linked'
        linked.symlink_to(self.base, target_is_directory=True)
        for root in (linked / 'before', self.root / '..' / 'before'):
            with self.subTest(root=root):
                with self.assertRaises((ValueError, OSError)):
                    self.audit(root=root)

    def test_policy_tampering_and_symlink_fail(self):
        policy = self.base / 'policy'
        policy.write_bytes(identity.POLICY.read_bytes() + b'# changed\n')
        with self.assertRaises(ValueError):
            identity.audit(self.root, 'before', policy)
        policy.unlink()
        policy.symlink_to(identity.POLICY)
        with self.assertRaises(OSError):
            identity.audit(self.root, 'before', policy)

    def test_detects_file_changed_during_read(self):
        original_read = os.read
        changed = False

        def mutate_after_read(descriptor, count):
            nonlocal changed
            data = original_read(descriptor, count)
            if not changed:
                changed = True
                passwd = self.root / 'etc/passwd'
                previous = passwd.stat()
                self.replace('passwd', PASSWD.replace('root:x:', 'root:!:'))
                # Make the mutation observable on coarse filesystem clocks.
                os.utime(passwd, ns=(previous.st_atime_ns, previous.st_mtime_ns + 10**9))
            return data

        with mock.patch.object(identity.os, 'read', side_effect=mutate_after_read):
            with self.assertRaises(ValueError):
                identity._snapshot(self.root)

    def test_compare_accepts_only_addition_and_preserves_all_old_tuples(self):
        after = self.make_root('after', True)
        self.assertTrue(identity.compare_accounts(self.root, after))
        (after / 'etc/passwd').write_text(PASSWD.replace('/bin/bash', '/bin/sh') + SERVICE_USER)
        with self.assertRaises(ValueError):
            identity.compare_accounts(self.root, after)
        (after / 'etc/passwd').write_text(PASSWD + SERVICE_USER)
        (after / 'etc/group').write_text(GROUP.replace('root:x:0:', 'root:x:0:systemd-network') + SERVICE_GROUP)
        with self.assertRaises(ValueError):
            identity.compare_accounts(self.root, after)

    def test_cli_failure_leaves_files_unchanged_and_redacts_data(self):
        self.replace('passwd', PASSWD + 'privateperson:secret-password:996:40::/:/bin/false\n')
        paths = [self.root / 'etc/passwd', self.root / 'etc/group']
        before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.root), '--phase', 'before'],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, 'timesync identity audit failed\n')
        self.assertEqual(before, [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths])

    def test_cli_success_is_machine_readable(self):
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.root), '--phase', 'before'],
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)['account'], 'systemd-timesync')
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()

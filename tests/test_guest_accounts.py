"""Guest account policy tests; no live account databases or NSS are modified."""
import copy
import importlib.util
import json
import io
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('guest_accounts', BASE / 'scripts/guest-accounts.py')
ACCOUNTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCOUNTS)
IDENTITY = ACCOUNTS.IDENTITY
CONFIG = {'schema': 1, 'guests': [{'name': 'one', 'uid': 60001, 'gid': 60001}]}
CONFIG_DATA = ACCOUNTS.canonical(CONFIG)
NAME = 'a733-vm-one'


def database(existing=False):
    result = {'passwd': b'root:x:0:0:root:/root:/bin/bash\n',
              'shadow': b'root:!:1:0:99999:7:::\n',
              'group': b'root:x:0:\nsudo:x:27:\n',
              'gshadow': b'root:*::\nsudo:*::\n'}
    if existing:
        for key, row in {'passwd': f'{NAME}:x:60001:60001:A733 guest one:/nonexistent:/usr/sbin/nologin\n',
                         'shadow': f'{NAME}:!:20000::::::\n',
                         'group': f'{NAME}:x:60001:\n',
                         'gshadow': f'{NAME}:!::\n'}.items():
            result[key] += row.encode()
    return result


class IdentityTests(unittest.TestCase):
    def test_fixed_render(self):
        self.assertEqual(IDENTITY.render(CONFIG), [f'g {NAME} 60001',
            f'u {NAME} 60001:60001 "A733 guest one" /nonexistent /usr/sbin/nologin'])

    def test_schema_refusals(self):
        values = []
        for name in ('a-', '0abc', 'A', 'a_b', 'a' * 17, 'a\nb', ''):
            value = copy.deepcopy(CONFIG)
            value['guests'][0]['name'] = name
            values.append(value)
        for uid, gid in ((True, True), (60000, 60001), (59999, 59999), (61000, 61000), (60001.0, 60001)):
            value = copy.deepcopy(CONFIG)
            value['guests'][0].update(uid=uid, gid=gid)
            values.append(value)
        values.extend([{'schema': True, 'guests': CONFIG['guests']},
                       {'schema': 1, 'guests': []}, {**CONFIG, 'extra': 1},
                       {'schema': 1, 'guests': CONFIG['guests'] * 2}])
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                IDENTITY.validate_config(json.dumps(value))
        with self.assertRaises(ValueError):
            IDENTITY.validate_config('{"schema":1,"schema":1,"guests":[]}')

    def test_name_limits(self):
        for name in ('a', 'a' * 16, 'a-b1'):
            value = copy.deepcopy(CONFIG)
            value['guests'][0]['name'] = name
            IDENTITY.validate_config(json.dumps(value))


class AccountPolicyTests(unittest.TestCase):
    def test_absent_and_exact_idempotent(self):
        self.assertFalse(ACCOUNTS.validate_accounts(CONFIG, database()))
        self.assertTrue(ACCOUNTS.validate_accounts(CONFIG, database(True), complete=True))
        with self.assertRaises(ValueError):
            ACCOUNTS.validate_accounts(CONFIG, database(), complete=True)

    def test_collision_matrix(self):
        mutations = [
            ('passwd', b'foreign:x:60001:1::/:/bin/sh\n'),
            ('passwd', b'foreign:x:123:60001::/:/bin/sh\n'),
            ('group', b'foreign:x:60001:\n'),
            ('passwd', f'{NAME}:x:60002:60002::/:/bin/sh\n'.encode()),
            ('shadow', f'{NAME}:!:::::::stable-secret\n'.encode()),
            ('group', f'wheel:x:10:{NAME}\n'.encode()),
            ('group', f'disk:x:6:{NAME}\n'.encode()),
            ('gshadow', f'wheel:!:{NAME}:\n'.encode()),
            ('gshadow', f'disk:!::{NAME}\n'.encode()),
        ]
        for key, row in mutations:
            for existing in (False, True):
                data = database(existing)
                data[key] += row
                with self.subTest(key=key, row=row, existing=existing), self.assertRaises(ValueError):
                    ACCOUNTS.validate_accounts(CONFIG, data)

    def test_noncanonical_foreign_primary_gid_cannot_hide_guest_access(self):
        for gid in ('060001', '+60001', '-60001', '', 'invalid', ' 60001', '4294967295', '999999999999999999999999'):
            data = database()
            data['passwd'] += f'foreign:x:123:{gid}::/:/bin/sh\n'.encode()
            with self.subTest(gid=gid), self.assertRaisesRegex(ValueError, 'account number'):
                ACCOUNTS.validate_accounts(CONFIG, data)

    def test_existing_wrong_identity_or_login(self):
        mutations = [('passwd', b':60001:60001:', b':60002:60001:'),
                     ('passwd', b'/nonexistent', b'/home/one'),
                     ('passwd', b'/usr/sbin/nologin', b'/bin/sh'),
                     ('shadow', f'{NAME}:!:'.encode(), f'{NAME}:$6$secret:'.encode()),
                     ('shadow', f'{NAME}:!:'.encode(), f'{NAME}:!$6$secret:'.encode()),
                     ('group', f'{NAME}:x:60001:'.encode(), f'{NAME}:x:60001:root'.encode()),
                     ('gshadow', f'{NAME}:!::'.encode(), f'{NAME}:!:root:'.encode())]
        for key, before, after in mutations:
            data = database(True)
            data[key] = data[key].replace(before, after)
            with self.subTest(key=key, after=after), self.assertRaises(ValueError):
                ACCOUNTS.validate_accounts(CONFIG, data)

    def test_plans_expose_only_hashes_and_requested_lines(self):
        data = database()
        data['shadow'] = b'root:$6$SUPER-SECRET:1:0:99999:7:::\n'
        plan = ACCOUNTS.make_plan(CONFIG_DATA, data)
        serialized = ACCOUNTS.canonical(plan)
        self.assertNotIn(b'SUPER-SECRET', serialized)
        self.assertEqual(plan['database_sha256']['shadow'], ACCOUNTS.digest(data['shadow']))
        altered = dict(data, shadow=data['shadow'].replace(b'SECRET', b'CHANGED'))
        self.assertNotEqual(plan, ACCOUNTS.make_plan(CONFIG_DATA, altered))


class ApplyTests(unittest.TestCase):
    def run_fixture(self, before=None, after=None, supplied=None, tool_error=None, guard_error=False):
        before = database() if before is None else before
        after = database(True) if after is None else after
        plan = ACCOUNTS.make_plan(CONFIG_DATA, before) if supplied is None else supplied
        with tempfile.TemporaryDirectory() as directory:
            lock = str(Path(directory) / 'lock')
            def input_data(path):
                return CONFIG_DATA if path == '/fixture/config' else ACCOUNTS.canonical(plan)
            # Every live dependency is replaced inside this fixture. Production
            # has no guard-bypass CLI or configurable database paths.
            with patch.object(ACCOUNTS, 'LOCK', lock), \
                 patch.object(ACCOUNTS.MANAGEMENT, 'runtime_guard', side_effect=ValueError('fixture') if guard_error else None), \
                 patch.object(ACCOUNTS, 'read_input', side_effect=input_data), \
                 patch.object(ACCOUNTS, 'validate_lock'), \
                 patch.object(ACCOUNTS.sys, 'stderr', new_callable=io.StringIO), \
                 patch.object(ACCOUNTS, 'snapshot', side_effect=[before, before, before, after]), \
                 patch.object(ACCOUNTS.subprocess, 'run', side_effect=tool_error) as tool:
                result = ACCOUNTS.apply('/fixture/config', '/fixture/plan')
                return result, tool.call_args_list, Path(lock).exists()

    def test_exact_argv_and_cleared_environment(self):
        result, calls, _ = self.run_fixture()
        self.assertTrue(result['success'])
        self.assertTrue(result['accounts_may_be_modified'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0], ['/usr/bin/systemd-sysusers', '--inline', *IDENTITY.render(CONFIG)])
        self.assertEqual(calls[0].kwargs['env'], {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
        self.assertNotIn('shell', calls[0].kwargs)

    def test_lock_close_failure_preserves_conservative_modified_receipt(self):
        close = os.close
        injected = False
        def failed_close(fd):
            nonlocal injected
            close(fd)
            if not injected:
                injected = True
                raise OSError('close acknowledgement lost')
        with patch.object(ACCOUNTS.os, 'close', side_effect=failed_close):
            result, calls, _ = self.run_fixture()
        self.assertFalse(result['success'])
        self.assertTrue(result['accounts_may_be_modified'])
        self.assertEqual(result['cleanup_error'], 'OSError')
        self.assertEqual(len(calls), 1)

    def test_stale_plan_refuses_tool(self):
        plan = ACCOUNTS.make_plan(CONFIG_DATA, database())
        plan['config_sha256'] = '0' * 64
        result, calls, _ = self.run_fixture(supplied=plan)
        self.assertFalse(result['success'])
        self.assertFalse(result['accounts_may_be_modified'])
        self.assertFalse(calls)

    def test_guard_refuses_before_lock(self):
        result, calls, lock_exists = self.run_fixture(guard_error=True)
        self.assertFalse(result['success'])
        self.assertFalse(calls)
        self.assertFalse(lock_exists)

    def test_exact_account_skips_sysusers(self):
        result, calls, _ = self.run_fixture(before=database(True))
        self.assertTrue(result['success'])
        self.assertFalse(result['accounts_may_be_modified'])
        self.assertFalse(calls)

    def test_tool_failure_is_conservative_no_retry(self):
        result, calls, _ = self.run_fixture(tool_error=subprocess.CalledProcessError(1, 'secret'))
        self.assertFalse(result['success'])
        self.assertTrue(result['accounts_may_be_modified'])
        self.assertEqual(len(calls), 1)
        self.assertNotIn('secret', json.dumps(result))

    def test_numeric_fallback_and_partial_tool_success_rejected(self):
        partial = database(True)
        partial['passwd'] = partial['passwd'].replace(b':60001:60001:', b':60002:60001:')
        for after in (database(), partial):
            result, calls, _ = self.run_fixture(after=after)
            self.assertFalse(result['success'])
            self.assertTrue(result['accounts_may_be_modified'])
            self.assertEqual(len(calls), 1)

    def test_unrelated_rows_cannot_change(self):
        for database_name in ACCOUNTS.DATABASES:
            for change in ('modify', 'drop', 'add'):
                after = database(True)
                lines = after[database_name].splitlines(keepends=True)
                if change == 'modify':
                    lines[0] = lines[0].replace(b'root', b'altered', 1)
                elif change == 'drop':
                    lines.pop(0)
                else:
                    lines.append(lines[0].replace(b'root', b'unexpected', 1))
                after[database_name] = b''.join(lines)
                with self.subTest(database=database_name, change=change):
                    result, calls, _ = self.run_fixture(after=after)
                    self.assertFalse(result['success'])
                    self.assertTrue(result['accounts_may_be_modified'])
                    self.assertEqual(len(calls), 1)

    def test_intent_is_flushed_before_invocation(self):
        observed = []
        def invoke(*args, **kwargs):
            intent = json.loads(ACCOUNTS.sys.stderr.getvalue())
            self.assertTrue(intent['accounts_may_be_modified'])
            self.assertFalse(intent['success'])
            observed.append(intent)
        result, calls, _ = self.run_fixture(tool_error=invoke)
        self.assertTrue(result['success'])
        self.assertEqual(len(observed), 1)

    def test_interruption_reports_conservative_intent(self):
        result, calls, _ = self.run_fixture(tool_error=KeyboardInterrupt())
        self.assertFalse(result['success'])
        self.assertTrue(result['accounts_may_be_modified'])
        self.assertEqual(result['error'], 'KeyboardInterrupt')
        self.assertEqual(len(calls), 1)

    def test_unsafe_lock_refuses_before_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / 'lock'
            lock.symlink_to(Path(directory) / 'destination')
            with patch.object(ACCOUNTS, 'LOCK', str(lock)), \
                 patch.object(ACCOUNTS.MANAGEMENT, 'runtime_guard'), \
                 patch.object(ACCOUNTS.subprocess, 'run') as tool:
                result = ACCOUNTS.apply('/fixture/config', '/fixture/plan')
            self.assertFalse(result['success'])
            self.assertFalse(result['accounts_may_be_modified'])
            tool.assert_not_called()


class InputAndLockTests(unittest.TestCase):
    def test_read_input_held_device_before_read(self):
        for allowed in (False, True):
            with self.subTest(allowed=allowed), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / 'config'
                source.write_bytes(CONFIG_DATA)
                source.chmod(0o400)
                info = source.stat()
                safe_device = info.st_dev if allowed else info.st_dev + 1
                original_stat = os.stat
                def fixture_stat(path, *args, **kwargs):
                    if str(path) in ('/run', str(ACCOUNTS.MANAGEMENT.STATE)):
                        return SimpleNamespace(st_dev=safe_device)
                    return original_stat(path, *args, **kwargs)
                with patch.object(ACCOUNTS.MANAGEMENT, 'secure_path', return_value=info), \
                     patch.object(ACCOUNTS.os, 'stat', side_effect=fixture_stat), \
                     patch.object(ACCOUNTS.os, 'read', wraps=os.read) as read:
                    if allowed:
                        self.assertEqual(ACCOUNTS.read_input(source), CONFIG_DATA)
                        read.assert_called_once()
                    else:
                        with self.assertRaises(ValueError):
                            ACCOUNTS.read_input(source)
                        read.assert_not_called()

    def test_input_cap_and_duplicate_keys(self):
        with self.assertRaises(ValueError):
            IDENTITY.validate_config(b'x' * (IDENTITY.LIMIT + 1))
        for data in (b'{"schema":1,"schema":1,"guests":[]}',
                     b'{"schema":1,"guests":[{"name":"one","name":"two","uid":60001,"gid":60001}]}'):
            with self.assertRaises(ValueError):
                IDENTITY.validate_config(data)

    def test_database_metadata_policy(self):
        fields = dict(st_mode=0o100640, st_uid=0, st_gid=42, st_nlink=1, st_dev=7)
        for name in ('shadow', 'gshadow'):
            ACCOUNTS.validate_database_metadata(SimpleNamespace(**fields), 7, name, 42)
            for key, value in (('st_mode', 0o100644), ('st_mode', 0o100660),
                               ('st_mode', 0o100400), ('st_uid', 1000), ('st_gid', 99),
                               ('st_nlink', 2), ('st_dev', 8), ('st_mode', 0o120640)):
                with self.subTest(name=name, key=key, value=value), self.assertRaises(ValueError):
                    ACCOUNTS.validate_database_metadata(
                        SimpleNamespace(**dict(fields, **{key: value})), 7, name, 42)
        for name in ('passwd', 'group'):
            for mode in (0o100644, 0o100600):
                ACCOUNTS.validate_database_metadata(
                    SimpleNamespace(**dict(fields, st_mode=mode, st_gid=0)), 7, name, 42)
            with self.assertRaises(ValueError):
                ACCOUNTS.validate_database_metadata(SimpleNamespace(**fields), 7, name, 42)

    def test_lock_metadata_refusal_matrix(self):
        fields = dict(st_mode=0o100600, st_uid=0, st_gid=0, st_nlink=1,
                      st_dev=7, st_ino=1, st_size=0, st_mtime_ns=1, st_ctime_ns=1)
        info = SimpleNamespace(**fields)
        ACCOUNTS.validate_lock(info, info, 7)
        for key, value in (('st_mode', 0o100644), ('st_uid', 1000), ('st_gid', 1000),
                           ('st_nlink', 2), ('st_dev', 8), ('st_mode', 0o040600)):
            changed = SimpleNamespace(**dict(fields, **{key: value}))
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                ACCOUNTS.validate_lock(changed, changed, 7)
        with self.assertRaises(ValueError):
            ACCOUNTS.validate_lock(info, SimpleNamespace(**dict(fields, st_ino=2)), 7)


if __name__ == '__main__':
    unittest.main()

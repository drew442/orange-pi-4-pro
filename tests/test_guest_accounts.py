"""Guest account policy tests; no live account databases or NSS are modified."""
import copy
import contextlib
import fcntl
import importlib.util
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import uuid
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


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.context = ACCOUNTS._bootstrap_context()
        self.admission_module = self.context.ADMISSION
        a = self.admission_module
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        # Actual typed Admission/ClosureOwner/Lease and kernel flock semantics;
        # only platform placement/ownership and host account I/O are fixtures.
        for fixture in (patch.object(a, 'RUNTIME', str(self.path / 'a733-admission')),
                        patch.object(a, 'RUNTIME_PARENT', str(self.path)),
                        patch.object(a, 'ROOT_UID', os.getuid()),
                        patch.object(a, '_tmpfs'), patch.object(a, '_ancestor'),
                        patch.object(a.os, 'geteuid', return_value=0),
                        patch.object(self.context, 'host_identity')):
            fixture.start()
            self.addCleanup(fixture.stop)
        self.admission = a.initialize(str(uuid.uuid4()), os.getgid(), lambda: 'running')
        self.addCleanup(self.admission.close)
        self.owner = self.admission.bootstrap_owner

    def apply_fixture(self, before=None, after=None, owner=None, invoke=None, plan=None,
                      config_data=CONFIG_DATA):
        before = database() if before is None else before
        after = database(True) if after is None else after
        plan = ACCOUNTS.make_plan(config_data, before) if plan is None else plan
        with patch.object(ACCOUNTS, 'LOCK', str(self.path / 'accounts.lock')), \
             patch.object(ACCOUNTS.MANAGEMENT, 'runtime_guard'), \
             patch.object(ACCOUNTS, 'validate_lock'), \
             patch.object(ACCOUNTS, 'snapshot', side_effect=[before, before, before, after]), \
             patch.object(ACCOUNTS.sys, 'stderr', new_callable=io.StringIO), \
             patch.object(ACCOUNTS.subprocess, 'run', side_effect=invoke) as tool:
            result = ACCOUNTS.apply_bootstrap(config_data, plan, self.owner if owner is None else owner)
            return result, tool.call_count

    def test_real_owner_is_borrowed_and_gate_stays_closed(self):
        fd = self.owner.lease.fd
        with patch.object(self.admission_module.ClosureOwner, 'exclusive',
                          side_effect=AssertionError('nested lease')), \
             patch.object(self.admission_module.ClosureOwner, 'open',
                          side_effect=AssertionError('opened gate')):
            result, calls = self.apply_fixture()
        self.assertTrue(result['success'])
        self.assertEqual(calls, 1)
        self.assertEqual(fd, self.owner.lease.recheck())
        self.assertEqual(self.admission._read()['state'], 'closed')
        self.context.host_identity.assert_called_with(self.admission.boot_id)

    def test_exact_existing_account_is_verified_noop(self):
        result, calls = self.apply_fixture(before=database(True))
        self.assertTrue(result['success'])
        self.assertFalse(result['accounts_may_be_modified'])
        self.assertEqual(calls, 0)

    def test_foreign_or_untyped_owner_refused(self):
        spec = importlib.util.spec_from_file_location(
            'foreign_accounts_admission', BASE / 'scripts/guest-admission.py')
        foreign = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(foreign)
        owner = foreign.ClosureOwner(self.admission, self.admission._read())
        owner.lease = self.owner.lease
        for value in (owner, SimpleNamespace(**vars(self.owner)), object()):
            with self.subTest(owner=type(value)):
                result, calls = self.apply_fixture(owner=value)
                self.assertFalse(result['success'])
                self.assertFalse(result['accounts_may_be_modified'])
                self.assertEqual(calls, 0)

    def test_invalid_capability_refuses_before_account_lock(self):
        a = self.admission_module
        for condition in ('reason', 'open', 'nonce', 'poison', 'closed', 'shared',
                          'flag', 'unlocked', 'foreign-admission', 'boot', 'nonroot'):
            with self.subTest(condition=condition), contextlib.ExitStack() as stack:
                owner = a.ClosureOwner(self.admission, self.admission._read())
                owner.lease = a.Lease(self.admission, os.dup(self.owner.lease.fd), True)
                stack.callback(owner.lease.close)
                if condition == 'reason':
                    owner.reason = 'maintenance'
                elif condition == 'open':
                    stack.enter_context(patch.object(a.Admission, '_read', return_value=
                        dict(self.admission._read(), state='open')))
                elif condition == 'nonce':
                    owner.nonce = str(uuid.uuid4())
                elif condition == 'poison':
                    owner.poisoned = True
                elif condition == 'closed':
                    owner.lease.close()
                elif condition == 'shared':
                    fcntl.flock(owner.lease.fd, fcntl.LOCK_SH)
                    stack.callback(fcntl.flock, self.owner.lease.fd, fcntl.LOCK_EX)
                elif condition == 'flag':
                    owner.lease.exclusive = False
                elif condition == 'unlocked':
                    fcntl.flock(owner.lease.fd, fcntl.LOCK_UN)
                    stack.callback(fcntl.flock, self.owner.lease.fd, fcntl.LOCK_EX)
                elif condition == 'foreign-admission':
                    owner.lease.admission = object()
                elif condition == 'boot':
                    stack.enter_context(patch.object(self.context, 'host_identity',
                                                     side_effect=ValueError('wrong boot/native namespace')))
                elif condition == 'nonroot':
                    stack.enter_context(patch.object(ACCOUNTS.os, 'geteuid', return_value=1000))
                result, calls = self.apply_fixture(owner=owner)
                self.assertFalse(result['success'])
                self.assertFalse(result['accounts_may_be_modified'])
                self.assertEqual(calls, 0)
                self.assertFalse((self.path / 'accounts.lock').exists())

    def test_actual_lock_owned_by_another_process_refused(self):
        fd = self.owner.lease.fd
        fcntl.flock(fd, fcntl.LOCK_UN)
        code = ('import fcntl,sys; fcntl.flock(int(sys.argv[1]), fcntl.LOCK_EX); '
                'print("ready", flush=True); sys.stdin.read(1)')
        child = subprocess.Popen([sys.executable, '-c', code, str(fd)], pass_fds=(fd,),
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline(), 'ready\n')
            result, calls = self.apply_fixture()
            self.assertFalse(result['success'])
            self.assertFalse(result['accounts_may_be_modified'])
            self.assertEqual(calls, 0)
        finally:
            child.communicate('x', timeout=5)
            fcntl.flock(fd, fcntl.LOCK_UN)
            fcntl.flock(fd, fcntl.LOCK_EX)

    def test_capability_loss_during_tool_fails_postcheck_conservatively(self):
        result, calls = self.apply_fixture(invoke=lambda *a, **k: self.owner.lease.close())
        self.assertFalse(result['success'])
        self.assertTrue(result['accounts_may_be_modified'])
        self.assertEqual(calls, 1)

    def test_closed_admission_refuses_before_mutation(self):
        self.admission.close()
        result, calls = self.apply_fixture()
        self.assertFalse(result['success'])
        self.assertFalse(result['accounts_may_be_modified'])
        self.assertEqual(calls, 0)

    def test_loss_after_snapshot_refuses_before_sysusers(self):
        original = ACCOUNTS.validate_accounts
        validations = 0
        def invalidate(*args, **kwargs):
            nonlocal validations
            result = original(*args, **kwargs)
            validations += 1
            # make_plan validates three snapshots, then the mutation decision.
            if validations == 4:
                self.owner.poisoned = True
            return result
        with patch.object(ACCOUNTS, 'validate_accounts', side_effect=invalidate):
            result, calls = self.apply_fixture()
        self.assertFalse(result['success'])
        self.assertEqual(calls, 0)

    def test_stale_plan_refuses_mutation(self):
        plan = ACCOUNTS.make_plan(CONFIG_DATA, database())
        plan['database_sha256']['shadow'] = '0' * 64
        result, calls = self.apply_fixture(plan=plan)
        self.assertFalse(result['success'])
        self.assertFalse(result['accounts_may_be_modified'])
        self.assertEqual(calls, 0)

    def test_two_fresh_batches_prepare_32_accounts(self):
        before = database()
        for offset in (0, 16):
            guests = [{'name': 'vm' + str(index), 'uid': 60000 + index,
                       'gid': 60000 + index} for index in range(offset, offset + 16)]
            config_data = ACCOUNTS.canonical({'schema': 1, 'guests': guests})
            after = dict(before)
            for guest in guests:
                name, number = 'a733-vm-' + guest['name'], guest['uid']
                for key, row in {
                    'passwd': f'{name}:x:{number}:{number}:A733 guest {guest["name"]}:/nonexistent:/usr/sbin/nologin\n',
                    'shadow': f'{name}:!:20000::::::\n',
                    'group': f'{name}:x:{number}:\n', 'gshadow': f'{name}:!::\n'
                }.items():
                    after[key] += row.encode()
            result, calls = self.apply_fixture(before=before, after=after, config_data=config_data)
            self.assertTrue(result['success'])
            self.assertEqual(calls, 1)
            before = after
        self.assertEqual(len(ACCOUNTS.parse_database(before['passwd'], 7)), 33)

    def test_capability_rechecked_at_final_postcheck(self):
        original = ACCOUNTS.validate_preserved
        def invalidate(*args):
            original(*args)
            self.owner.poisoned = True
        with patch.object(ACCOUNTS, 'validate_preserved', side_effect=invalidate):
            result, calls = self.apply_fixture()
        self.assertFalse(result['success'])
        self.assertTrue(result['accounts_may_be_modified'])
        self.assertEqual(calls, 1)

    def test_bootstrap_preserves_other_batch_rows(self):
        before = database()
        after = database(True)
        other = database(True)
        for key in ACCOUNTS.DATABASES:
            row = other[key].splitlines(keepends=True)[-1]
            row = row.replace(b'a733-vm-one', b'a733-vm-two').replace(b'60001', b'60002')
            before[key] += row
            after[key] += row
        result, calls = self.apply_fixture(before=before, after=after)
        self.assertTrue(result['success'])
        self.assertEqual(calls, 1)
        after['shadow'] = after['shadow'].replace(b'a733-vm-two:!', b'a733-vm-two:*')
        result, calls = self.apply_fixture(before=before, after=after)
        self.assertFalse(result['success'])
        self.assertTrue(result['accounts_may_be_modified'])


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
            ACCOUNTS.validate_database_metadata(SimpleNamespace(**fields), name, 42)
            for key, value in (('st_mode', 0o100644), ('st_mode', 0o100660),
                               ('st_mode', 0o100400), ('st_uid', 1000), ('st_gid', 99),
                               ('st_nlink', 2), ('st_mode', 0o120640)):
                with self.subTest(name=name, key=key, value=value), self.assertRaises(ValueError):
                    ACCOUNTS.validate_database_metadata(
                        SimpleNamespace(**dict(fields, **{key: value})), name, 42)
        for name in ('passwd', 'group'):
            for mode in (0o100644, 0o100600):
                ACCOUNTS.validate_database_metadata(
                    SimpleNamespace(**dict(fields, st_mode=mode, st_gid=0)), name, 42)
            with self.assertRaises(ValueError):
                ACCOUNTS.validate_database_metadata(SimpleNamespace(**fields), name, 42)

    def test_strict_fd_mount_identity(self):
        self.assertEqual(ACCOUNTS.fd_mount_id('pos:\t0\nmnt_id:\t43\nino:\t123\n'), 43)
        for data in ('', 'pos: 0\n', 'mnt_id: 43\nmnt_id: 43\n',
                     'mnt_id: 043\n', 'mnt_id: +43\n', 'mnt_id: -1\n',
                     'mnt_id: 0\n', 'mnt_id: abc\n', 'malformed\nmnt_id: 43\n',
                     'mnt_id: ' + '1' * 21, 'x' * (ACCOUNTS.FDINFO_LIMIT + 1)):
            with self.subTest(data=data), self.assertRaises(ValueError):
                ACCOUNTS.fd_mount_id(data)

    def test_overlay_mount_identity_uses_management_validation(self):
        row = '43 1 0:26 / /etc rw - overlay overlay rw\n'
        with patch.object(ACCOUNTS.MANAGEMENT, 'validate_mounts') as validate:
            self.assertEqual(ACCOUNTS.etc_mount_id(row), 43)
            validate.assert_called_once()
            for value in ('', row + row, row.replace('43 ', '043 ', 1),
                          row.replace('43 ', '+43 ', 1), 'malformed\n'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    ACCOUNTS.etc_mount_id(value)
        with patch.object(ACCOUNTS.MANAGEMENT, 'validate_mounts', side_effect=ValueError('guard')):
            with self.assertRaisesRegex(ValueError, 'guard'):
                ACCOUNTS.etc_mount_id(row)

    def test_database_mount_boundary_before_read(self):
        for file_mount in (43, 44):
            with self.subTest(file_mount=file_mount), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'passwd'
                payload = database()['passwd']
                path.write_bytes(payload)
                path.chmod(0o644)
                values = {key: getattr(path.stat(), key) for key in (
                    'st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink',
                    'st_size', 'st_mtime_ns', 'st_ctime_ns')}
                info = SimpleNamespace(**dict(values, st_uid=0, st_gid=0))
                etc_info = SimpleNamespace(**dict(values, st_dev=info.st_dev + 1))
                fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    with patch.object(ACCOUNTS, 'validate_etc') as parent, \
                         patch.object(ACCOUNTS, 'held_mount_id', return_value=file_mount), \
                         patch.object(ACCOUNTS.os, 'stat', return_value=info), \
                         patch.object(ACCOUNTS.os, 'fstat', return_value=info), \
                         patch.object(ACCOUNTS.os, 'open', wraps=os.open) as opened, \
                         patch.object(ACCOUNTS.os, 'read', wraps=os.read) as read:
                        if file_mount == 43:
                            self.assertEqual(ACCOUNTS.read_database('passwd', fd, etc_info, 43), payload)
                            read.assert_called_once()
                            self.assertEqual(parent.call_count, 3)
                        else:
                            with self.assertRaisesRegex(ValueError, 'foreign account database mount'):
                                ACCOUNTS.read_database('passwd', fd, etc_info, 43)
                            read.assert_not_called()
                        self.assertEqual(opened.call_args.args[0], 'passwd')
                        self.assertEqual(opened.call_args.kwargs, {'dir_fd': fd})
                        self.assertTrue(opened.call_args.args[1] & os.O_NOFOLLOW)
                        self.assertTrue(opened.call_args.args[1] & os.O_NONBLOCK)
                finally:
                    os.close(fd)

    def test_parent_identity_and_mount_fences(self):
        fields = dict(st_dev=26, st_ino=1, st_mode=0o40755, st_uid=0, st_gid=0,
                      st_nlink=1, st_size=0, st_mtime_ns=1, st_ctime_ns=1)
        info = SimpleNamespace(**fields)
        for failure in (None, 'held', 'path', 'fd_mount', 'path_mount'):
            with self.subTest(failure=failure), \
                 patch.object(ACCOUNTS.os, 'fstat', return_value=SimpleNamespace(
                     **dict(fields, st_ino=2 if failure == 'held' else 1))), \
                 patch.object(ACCOUNTS.MANAGEMENT, 'secure_path', return_value=SimpleNamespace(
                     **dict(fields, st_ino=2 if failure == 'path' else 1))), \
                 patch.object(ACCOUNTS, 'held_mount_id', return_value=44 if failure == 'fd_mount' else 43), \
                 patch.object(ACCOUNTS, 'etc_mount_id', return_value=44 if failure == 'path_mount' else 43), \
                 patch.object(Path, 'read_text', return_value='fixture'):
                if failure is None:
                    ACCOUNTS.validate_etc(10, info, 43)
                else:
                    with self.assertRaises(ValueError):
                        ACCOUNTS.validate_etc(10, info, 43)

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

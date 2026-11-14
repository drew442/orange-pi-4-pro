"""Host-only refusal/order checks; these do not establish hardware acceptance."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/request-spi-power.py'
SPEC = importlib.util.spec_from_file_location('spi_power_request_tests', SCRIPT)
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)
BOOT = 'a0140bd2-8acd-4f97-8175-34136a5bb3a5'


def simulated_flags(**overrides):
    # Preserve every actual interpreter flag used by argparse/subprocess.
    values = {name: getattr(sys.flags, name) for name in dir(sys.flags) if not name.startswith("_")}
    return NS(**(values | overrides))


def arguments():
    return NS(expected_boot_id=BOOT, request_id=BOOT, action='reboot',
              expected_head_sha256='a' * 64, expected_registry_sha256='b' * 64)


class Runtime:
    """Explicit fake installed API, exercising orchestration rather than hardware."""
    def __init__(self):
        self.events, self.record, self.held = [], {'state': 'open'}, False
        self.fail_close = self.fail_ex = self.fail_auth = False
        runtime = self
        class Policy:
            value = dict(storage=H.STORAGE, target='/dev/nvme0n1',
                         expected_model='SPCC M.2 PCIe SSD', expected_bytes=256060514304)
            def __init__(self, boot): pass
            def check(self): runtime.events.append('policy-check')
            def close(self): runtime.events.append('policy-close')
        class Lease:
            exclusive, fd = True, 123
            def __init__(self, admission):
                self.admission = admission
                runtime.held = True
            def recheck(self): pass
            def close(self):
                runtime.events.append('ex-close')
                if runtime.fail_close:
                    raise OSError('uncertain EX close')
                runtime.held = False
        class Admission:
            bootstrap_owner = None
            @classmethod
            def open_existing(cls, *args): return cls()
            def recheck(self): pass
            def _gate(self): return contextlib.nullcontext()
            def _read(self): return dict(runtime.record)
            def close_gate(self, reason):
                runtime.events.append('close-gate')
                runtime.record = {'state': 'closed', 'reason': reason}
            def shutdown_lease(self):
                runtime.events.append('take-ex')
                if runtime.fail_ex:
                    raise ValueError('busy EX')
                return Lease(self)
            def close(self): runtime.events.append('admission-close')
        self.A = NS(Admission=Admission, Lease=Lease, _boot_id=lambda: BOOT,
                    _held_lock=lambda *args: self.events.append('write-check'))
        self.P = NS(BOOT_POLICY=NS(Policy=Policy),
                    NORMAL=NS(manager_state=lambda: 'running', local_kvm_gid=lambda: 100),
                    installed_guard=lambda boot: self.events.append('native'),
                    failure_detail=lambda error: {'message': str(error)},
                    MAINTENANCE=NS(consumer_check=lambda: self.events.append('consumers')))
        self.M = NS(A=self.A, P=self.P)
        self.M.L = NS(ADMISSION=self.A)
        self.P.CP = NS(LIFECYCLE=self.M.L)
        self.Q = NS(load_installed=lambda: self.M, terminal=lambda m: self.events.append('terminal'))
        self.intent = b'intent'

    def auth(self, *args):
        self.events.append('authenticate')
        self.assert_held()
        if self.fail_auth:
            raise ValueError('authentication changed')
        return {'head.json': {'sha256': 'a' * 64}}

    def assert_held(self):
        if not self.held:
            raise AssertionError('EX not held')

    def publish(self, value):
        self.assert_held()
        self.events.append('publish')
        return self.intent

    def command(self, argv, **kwargs):
        if self.held:
            raise AssertionError('EX retained across systemctl')
        if argv != ['/usr/bin/systemctl', 'reboot'] or kwargs != dict(
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=H.ENVIRONMENT, timeout=10, check=False):
            raise AssertionError('unexpected command transport')
        self.events.append('command')
        return NS(returncode=0)

    @contextlib.contextmanager
    def installed(self):
        with contextlib.ExitStack() as stack:
            for name, value in (
                    ('load_qualifier', lambda: (self.Q, 'helper-pin')),
                    ('original', lambda: None), ('sd_absent', lambda: None),
                    ('storage_snapshot', lambda *args: {}), ('authenticate', self.auth),
                    ('publish_intent', self.publish), ('read_private', lambda *args: self.intent),
                    ('emit', lambda value: self.events.append('precommand'))):
                stack.enter_context(patch.object(H, name, value))
            stack.enter_context(patch.object(H.os.path, 'lexists', return_value=False))
            stack.enter_context(patch.object(H.os, 'get_inheritable', return_value=False))
            stack.enter_context(patch.object(H.subprocess, 'run', self.command))
            yield


class PowerRequestTests(unittest.TestCase):
    def test_default_is_read_free_and_no_transport(self):
        with patch.object(H, 'load_qualifier', side_effect=AssertionError('installed import')), \
                patch.object(H.subprocess, 'run', side_effect=AssertionError('transport')), \
                patch.object(H.os, 'lstat', side_effect=AssertionError('filesystem')), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(H.main([]), 0)
        self.assertFalse(json.loads(out.getvalue())['power_requested'])

    def test_cli_validation(self):
        good = ['--execute', '--action', 'reboot', '--expected-boot-id', BOOT,
                '--request-id', BOOT, '--expected-head-sha256', 'a' * 64,
                '--expected-registry-sha256', 'b' * 64]
        cases = [good[:1], good[:-2], good[:4] + [BOOT.upper()] + good[5:],
                 good[:8] + ['A' * 64] + good[9:]]
        for argv in cases:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), \
                    patch.object(H, 'execute', side_effect=AssertionError('execute')):
                with self.assertRaises(SystemExit): H.main(argv)
        with contextlib.redirect_stderr(io.StringIO()), \
                patch.object(H.sys, 'flags', simulated_flags(isolated=0, dont_write_bytecode=1)):
            with self.assertRaises(SystemExit): H.main(good)

    def test_exact_once_and_ex_release_before_command(self):
        r = Runtime()
        with r.installed(): result = H.execute(arguments())
        self.assertTrue(result['success'], result)
        self.assertTrue(result['command_attempted'])
        self.assertEqual(r.events.count('command'), 1)
        self.assertEqual(r.events.count('consumers'), 4)
        self.assertEqual(r.events.count('terminal'), 4)
        self.assertLess(r.events.index('publish'), r.events.index('ex-close'))
        self.assertLess(r.events.index('ex-close'), r.events.index('precommand'))
        self.assertLess(r.events.index('precommand'), r.events.index('command'))
        self.assertEqual(r.record, {'state': 'closed', 'reason': 'shutdown'})

    def test_busy_ex_leaves_gate_closed(self):
        r = Runtime(); r.fail_ex = True
        with r.installed(): result = H.execute(arguments())
        self.assertFalse(result['command_attempted'])
        self.assertEqual(r.record['state'], 'closed')
        self.assertTrue(result['shutdown_close_attempted'])
        self.assertTrue(result['shutdown_closed'])
        self.assertFalse(result['exclusive_acquired'])
        self.assertFalse(result['intent_publication_attempted'])
        self.assertIn('failure', result)
        self.assertNotIn('publish', r.events)

    def test_failed_authentication_leaves_closed(self):
        r = Runtime(); r.fail_auth = True
        with r.installed(): result = H.execute(arguments())
        self.assertFalse(result['command_attempted'])
        self.assertNotIn('publish', r.events)
        self.assertEqual(r.record['state'], 'closed')

    def test_cleanup_uncertainty_aborts_and_never_retries_close(self):
        r = Runtime(); r.fail_close = True
        with r.installed(): result = H.execute(arguments())
        self.assertFalse(result['command_attempted'])
        self.assertEqual(r.events.count('ex-close'), 1)
        self.assertTrue(result['intent_durable'])
        self.assertFalse(result['exclusive_released'])
        self.assertEqual(r.record['state'], 'closed')

    def test_existing_partial_intent_refuses_before_closure(self):
        r = Runtime()
        with r.installed(), patch.object(H.os.path, 'lexists', return_value=True):
            result = H.execute(arguments())
        self.assertFalse(result['command_attempted'])
        self.assertNotIn('close-gate', r.events)

    def test_closed_admission_refuses(self):
        r = Runtime(); r.record = {'state': 'closed', 'reason': 'shutdown'}
        with r.installed(): result = H.execute(arguments())
        self.assertFalse(result['command_attempted'])
        self.assertNotIn('take-ex', r.events)

    def test_pid1_sd_native_and_changed_boot_refuse_before_closure(self):
        for failure in ('pid1', 'sd', 'native', 'boot', 'storage'):
            r = Runtime()
            with self.subTest(failure=failure), r.installed(), contextlib.ExitStack() as stack:
                if failure == 'pid1': r.P.NORMAL.manager_state = lambda: 'starting'
                if failure == 'sd': stack.enter_context(patch.object(H, 'sd_absent', side_effect=ValueError('SD')))
                if failure == 'native': r.P.installed_guard = lambda b: H.require(False, 'native')
                if failure == 'boot': r.A._boot_id = lambda: 'stale'
                if failure == 'storage': stack.enter_context(patch.object(H, 'storage_snapshot', side_effect=ValueError('storage')))
                result = H.execute(arguments())
            self.assertFalse(result['command_attempted'])
            self.assertNotIn('close-gate', r.events)

    def test_command_timeout_and_nonzero_are_uncertain_no_retry(self):
        for response in (subprocess.TimeoutExpired('systemctl', 10), NS(returncode=1), OSError('ack lost')):
            r = Runtime()
            with self.subTest(response=response), r.installed(), \
                    patch.object(H.subprocess, 'run', side_effect=[response] if isinstance(response, BaseException)
                                 else None, return_value=response) as command:
                result = H.execute(arguments())
            self.assertTrue(result['command_attempted'])
            self.assertFalse(result['success'])
            self.assertFalse(result['command_acknowledged'])
            self.assertEqual(command.call_count, 1)
            self.assertEqual(r.record['state'], 'closed')

    def test_source_path_and_cache_and_hash_refusal(self):
        with patch.object(H, 'original'), patch.object(H, '__file__', '/tmp/helper.py'):
            with self.assertRaisesRegex(ValueError, 'fixed external'): H.load_qualifier()
        with patch.object(H, 'original'), patch.object(H, '__file__', str(H.SOURCE)), \
                patch.object(H.os, 'listdir', return_value=[H.SOURCE.name, H.QUALIFIER.name, '__pycache__']):
            with self.assertRaisesRegex(ValueError, 'bytecode'): H.load_qualifier()
        with patch.object(H, 'original'), patch.object(H, '__file__', str(H.SOURCE)), \
                patch.object(H.os, 'listdir', return_value=[H.SOURCE.name, H.QUALIFIER.name]), \
                patch.object(H, 'read_private', return_value=b'wrong'):
            with self.assertRaisesRegex(ValueError, 'pin'): H.load_qualifier()


    def test_full_authentication_rejects_wrong_head_run_or_cp_authority(self):
        def canonical(value): return json.dumps(value, sort_keys=True).encode()
        for failure in ('head', 'run', 'success', 'publication', 'cleanup', 'registry'):
            with self.subTest(failure=failure):
                head = dict(status='ready', sequence=1, name='autokvm', last_boot_id=BOOT, run_id=BOOT)
                run = dict(name='autokvm', run_id=BOOT, boot_id=BOOT, automatic=True,
                           fixture_profile='managed-power-key-v1', shutdown_profile='shutdown')
                args = arguments()
                args.expected_head_sha256 = H.digest(canonical(head))
                if failure == 'head': args.expected_head_sha256 = '0' * 64
                if failure == 'run': run['automatic'] = False
                auth = dict(success=True, stage='complete', publications=[],
                            registry_sha256=args.expected_registry_sha256)
                if failure == 'success': auth['success'] = False
                if failure == 'publication': auth['publications'] = ['unexpected']
                if failure == 'cleanup': auth['cleanup_errors'] = ['uncertain']
                if failure == 'registry': auth['registry_sha256'] = '0' * 64
                class Store:
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def child(self, name): return self
                    def read(self, name, limit):
                        return canonical(head if name == 'head.json' else run)
                store = Store()
                class Controller:
                    context = NS(guard=lambda: None)
                    def __init__(self, *args, **kwargs): pass
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def _head_store(self, *args): return store
                lock = NS(recheck=lambda: None)
                admission = NS(guest_lock=lambda *a, **k: contextlib.nullcontext(lock))
                cp = NS(authenticate_ready_cohort=lambda *a, **k: auth)
                p = NS(Resources=contextlib.ExitStack, CP=cp,
                       HEAD=NS(LIMIT=65536, validate_head=json.loads, canonical=canonical),
                       REGISTRY=NS(SHUTDOWN_PROFILE='shutdown'))
                m = NS(P=p, L=NS(StateController=Controller, validate_run=json.loads, canonical_run=canonical))
                with self.assertRaises(ValueError):
                    H.authenticate(m, NS(), NS(value={'storage': H.STORAGE}), admission,
                                   object(), args, lambda: None)

    def test_sd_scan_rejects_presence_unknown_and_read_errors(self):
        for names, value in ((['mmcblk1'], b'MMC\n'), (['mmcblk2'], b'SD\n'),
                             (['mmcblk2'], b'unknown\n'), (['mmcblk2'], OSError('read failed'))):
            with self.subTest(names=names, value=value), \
                    patch.object(H.os, 'scandir', return_value=contextlib.nullcontext([NS(name=n) for n in names])), \
                    patch.object(H.Path, 'read_bytes', side_effect=value if isinstance(value, Exception) else None,
                                 return_value=value):
                with self.assertRaises((ValueError, OSError)): H.sd_absent()
        with patch.object(H.os, 'scandir', return_value=contextlib.nullcontext([NS(name='mmcblk0')])), \
                patch.object(H.Path, 'read_bytes', return_value=b'MMC\n'):
            H.sd_absent()

    def test_unsafe_ram_path_refusal(self):
        with self.assertRaisesRegex(ValueError, 'fixed RAM'): H.ram_directory(Path('/tmp/helper'))

    def test_policy_and_pending_selection_refusal(self):
        policy = NS(value=Runtime().P.BOOT_POLICY.Policy.value)
        snap = dict(active=dict(slot='B', marker_sha256=H.MARKER_SHA256),
                    state=dict(selector=dict(SLOT='A'), selector_sha256=H.SELECTOR_SHA256,
                               uuid=H.STORAGE['state_uuid']), guest=dict(uuid=H.STORAGE['guest_uuid']))
        m = NS(P=NS(ACTIVATE=NS(snapshot=lambda args, mounted: snap)))
        with self.assertRaisesRegex(ValueError, 'running/selected'): H.storage_snapshot(m, policy)
        snap['state']['selector']['SLOT'] = 'B'
        self.assertEqual(H.storage_snapshot(m, policy), snap)
        policy.value = dict(policy.value, expected_bytes=1)
        with self.assertRaisesRegex(ValueError, 'storage policy'): H.storage_snapshot(m, policy)


if __name__ == '__main__':
    unittest.main()

"""Real root RAM records/flocks; explicit physical/PID1 fixtures, no hardware proof."""
import copy
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
M = load('dispatch_test', 'scripts/dispatch-guest-boot.py')
F = load('dispatch_fixture', 'tests/test_prepare_guest_boot.py')
F.M, F.A = M.PREP, M.A
P = M.PREP


class Records(unittest.TestCase):
    def test_only_empty_exec_array_display_omissions_are_optional(self):
        keys = ('LoadState', 'ExecStartPre', 'ExecStartPost')
        with patch.object(M, 'command', return_value='LoadState=loaded\n'):
            self.assertEqual(M.properties('one.service', keys),
                             dict(LoadState='loaded', ExecStartPre='', ExecStartPost=''))
        with patch.object(M, 'command', return_value='ExecStartPre=command\n'):
            with self.assertRaises(Exception):
                M.properties('one.service', keys)
        with patch.object(M, 'command', return_value='LoadState=loaded\nExecStartPre=command\n'):
            self.assertEqual(M.properties('one.service', keys)['ExecStartPre'], 'command')

    def test_native_252_dependency_display_preserves_unit_name_escapes(self):
        # Debian 252.39 show output: quoted C-escaped names, other units bare.
        display = (r'systemd-journald.socket "run-a733\\x2dstate.mount" '
                   r'sysinit.target -.mount "run-a733\\x2dguests.mount"')
        expected = {'systemd-journald.socket', r'run-a733\x2dstate.mount',
                    'sysinit.target', '-.mount', r'run-a733\x2dguests.mount'}
        self.assertEqual(M.unit_dependencies(display), expected)
        self.assertEqual(M.unit_dependencies('  ' + ' '.join(sorted(expected)) + ' '), expected)
        self.assertEqual(M.unit_dependencies(''), set())
        self.assertEqual(M.unit_dependencies('x' * 247 + '.service'),
                         {'x' * 247 + '.service'})
        self.assertEqual(len(M.unit_dependencies(
            ' '.join(str(i) + '.service' for i in range(4096)))), 4096)
        self.assertEqual(M.unit_dependencies(r'"literal\\x5c.mount"'),
                         {r'literal\x5c.mount'})

    def test_dependency_display_rejects_malformed_or_unbounded_names(self):
        invalid = [None, b'one.service', 'é.service', 'one.service\n',
                   'one.service\ttwo.service', '"one.service', '""',
                   '"one.service"two.service', r'"run-a733\x2dstate.mount"',
                   r'"one\n.service"', r'"one\".service"', r'one\q.service',
                   r'one\x2.mount', r'one\xZZ.mount', "'one.service'",
                   '/one.service', 'one.unknown', 'one.service one.service',
                   'x' * 248 + '.service', ' ' * 65537,
                   ' '.join(str(i) + '.service' for i in range(4097))]
        for value in invalid:
            with self.subTest(value=repr(value)[:120]), self.assertRaises(M.A.AdmissionError):
                M.unit_dependencies(value)

    def test_closed_and_canonical_intent(self):
        value = dict(schema=1, profile=M.PROFILE, boot_id='11111111-1111-4111-8111-111111111111',
            policy_sha256='a'*64, registry_sha256='b'*64, selected=['one'],
            units={M.U.SLICE_NAME:'c'*64, 'a733-managed-one.service':'d'*64})
        self.assertEqual(M.intent_record(M.encoded(value)), value)
        for invalid in ({**value, 'extra': True}, {**value, 'selected':['one','one']},
                        {**value, 'schema': True}):
            with self.assertRaises(Exception):
                M.intent_record(M.encoded(invalid))
        with self.assertRaises(Exception):
            M.intent_record(M.encoded(value).replace(b':1', b': 1'))


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'root RAM fixture required')
class Native(unittest.TestCase):
    # Reuse only the existing preparation fixture's setup/helpers, not its tests.
    for _name in ('entry', 'plan', 'authority', 'activate', 'state', 'accounts', 'read_registry',
                  'observe', 'ready', 'configs', 'reconcile', 'census', 'restore_census', 'gate'):
        locals()[_name] = getattr(F.Native, _name)

    def setUp(self):
        F.Native.setUp(self)
        self.entry('one')
        prepared = P.prepare()
        self.assertTrue(prepared['success'], prepared)
        policy_class, controller_class = P.BOOT_POLICY.Policy, P.LIFECYCLE.StateController
        class Policy(policy_class):
            def __enter__(self): return self
            def __exit__(self, *unused): self.close()
        class Controller(controller_class):
            def __enter__(self): return self
            def __exit__(self, *unused): self.close()
        self.stack.enter_context(patch.object(P.BOOT_POLICY, 'Policy', Policy))
        self.stack.enter_context(patch.object(P.LIFECYCLE, 'StateController', Controller))
        self.commands = []
        self.stack.enter_context(patch.object(M, 'command', self.command))
        self.stack.enter_context(patch.object(M, 'properties', self.properties))
        self.bad = None

    def command(self, argv, **kwargs):
        self.commands.append(argv)
        if argv[0].endswith('systemd-escape'):
            return {'/run/a733-state':'run-a733\\x2dstate.mount\n',
                    '/run/a733-guests':'run-a733\\x2dguests.mount\n'}[argv[-1]]
        if 'start' in argv:
            self.assertIn('--no-block', argv)
            self.assertTrue((self.prepared_dir / M.RECORD).exists())
            self.assertLess(self.events.index('policy-close', self.events.index('open')),
                            len(self.events))
            if self.bad == 'lost-ack':
                raise TimeoutError('lost acknowledgement')
        return ''

    def properties(self, unit, keys):
        value = {k:'' for k in keys}
        value.update(LoadState='loaded', FragmentPath=str(self.unit_dir / unit), DropInPaths='',
            MemoryMax=str((F.RAM//M.U.MIB)*M.U.MIB-M.U.HOST_RESERVE_BYTES),
            MemorySwapMax='0', TasksMax='4096')
        if unit != M.U.SLICE_NAME:
            value.update(MemoryMax=str(256*M.U.MIB), TasksMax='256', User='root', Group='root',
                Type='exec', Restart='no', Slice=M.U.SLICE_NAME, KillMode='mixed', KillSignal='15',
                TimeoutStopUSec='7min', SendSIGKILL='yes', NoNewPrivileges='yes',
                CapabilityBoundingSet=' '.join(M.U.CAPABILITIES).lower(), PrivateMounts='no',
                PrivateTmp='no', PrivateDevices='no', ProtectSystem='no', PrivateUsers='no',
                After=r'a733-guest-prepare.service a733-guest-storage.service "run-a733\\x2dstate.mount" "run-a733\\x2dguests.mount" -.mount systemd-journald.socket',
                Requires=r'a733-guest-prepare.service a733-guest-storage.service "run-a733\\x2dstate.mount" "run-a733\\x2dguests.mount" sysinit.target a733-guests.slice',
                RequiresMountsFor='/run/a733-state /run/a733-guests')
            if self.bad == 'dropin': value['DropInPaths'] = '/run/evil.conf'
            if self.bad == 'namespace': value['PrivateMounts'] = 'yes'
            if self.bad == 'dependency':
                # Fragmentless manually mounted units gain After from
                # RequiresMountsFor on v252, but no generated Requires.
                value['Requires'] = 'a733-guest-prepare.service a733-guest-storage.service'
            if self.bad == 'wrong-mount':
                value['Requires'] = value['Requires'].replace('x2dstate', 'x2dother')
            if self.bad == 'malformed-after': value['After'] += ' "bad\\q.service"'
            if self.bad == 'malformed-requires': value['Requires'] += ' "bad\\q.service"'
        return value

    def queues(self): return [argv for argv in self.commands if 'start' in argv]

    def test_once_and_repeat_has_no_commands_or_inode_changes(self):
        result = M.dispatch()
        self.assertTrue(result['success'], result)
        self.assertTrue(result['queue_acknowledged'])
        self.assertEqual(len(self.queues()), 1)
        record = self.prepared_dir / M.RECORD
        before = record.stat()
        self.commands.clear()
        again = M.dispatch()
        self.assertTrue(again['success'], again)
        self.assertTrue(again['noop'])
        self.assertEqual(self.commands, [])
        self.assertEqual(record.stat(), before)

    def test_lost_ack_is_not_retried(self):
        self.bad = 'lost-ack'
        result = M.dispatch()
        self.assertFalse(result['success'])
        self.assertTrue(result['intent_published'])
        self.assertTrue(result['queue_attempted'])
        self.assertTrue(result['queue_uncertain'])
        self.assertTrue(M.dispatch()['noop'])
        self.assertEqual(len(self.queues()), 1)

    def test_disable_excluded_new_enable_skipped(self):
        self.registry['guests'][0]['enabled'] = False
        self.entry('two')
        result = M.dispatch()
        self.assertTrue(result['success'], result)
        self.assertEqual(result['selected'], [])
        self.assertEqual(result['skipped'], ['two'])
        self.assertEqual(self.queues(), [])

    def test_pid1_contract_refuses_before_intent(self):
        for bad in ('dropin', 'namespace', 'dependency', 'wrong-mount',
                    'malformed-after', 'malformed-requires'):
            self.bad = bad
            result = M.dispatch()
            self.assertFalse(result['success'], result)
            self.assertFalse((self.prepared_dir / M.RECORD).exists())
            self.assertEqual(self.queues(), [])

    def test_changed_unit_inode_refuses(self):
        path = self.unit_dir / 'a733-managed-one.service'
        path.unlink()
        path.write_text('wrong\n')
        path.chmod(0o444)
        self.assertFalse(M.dispatch()['success'])
        self.assertEqual(self.commands, [])

    def test_changed_configuration_refuses(self):
        original = self.configs
        def changed(controller, entries, guard):
            configs = original(controller, entries, guard)
            configs[0]['memory_max_mib'] = 512
            return configs
        with patch.object(P, '_configs', changed):
            result = M.dispatch()
        self.assertFalse(result['success'], result)
        self.assertEqual(self.commands, [])

    def test_publication_uncertainty_never_queues(self):
        real = P.publish
        def fail_after_publish(*args, **kwargs):
            real(*args, **kwargs)
            raise P.STORE.PublicationError(dict(stage='directory-fsync', published=True,
                uncertain=True, durable=False), OSError('lost fsync acknowledgement'))
        with patch.object(P, 'publish', fail_after_publish):
            result = M.dispatch()
        self.assertFalse(result['success'])
        self.assertTrue(result['intent_published'])
        self.assertTrue(result['intent_uncertain'])
        self.assertEqual(self.queues(), [])
        self.assertTrue(M.dispatch()['noop'])

    def test_cleanup_failure_after_intent_prevents_queue(self):
        self.policy_close_bad = True
        result = M.dispatch()
        self.assertFalse(result['success'])
        self.assertTrue(result['intent_published'])
        self.assertEqual(self.queues(), [])
        self.policy_close_bad = False
        self.assertTrue(M.dispatch()['noop'])

    def test_census_failure_before_intent(self):
        self.fail_at = 'census'
        result = M.dispatch()
        self.assertFalse(result['success'])
        self.assertFalse((self.prepared_dir / M.RECORD).exists())
        self.assertEqual(self.queues(), [])

    def test_pid1_stopping_refuses_even_repeat(self):
        self.assertTrue(M.dispatch()['success'])
        self.commands.clear()
        with patch.object(P.NORMAL, 'manager_state', lambda:'stopping'):
            result = M.dispatch()
        self.assertFalse(result['success'], result)
        self.assertEqual(self.commands, [])

    def test_closed_gate_refuses(self):
        with M.A.Admission.open_existing(self.boot, 108, lambda:'running') as admission:
            admission.close_gate('maintenance')
        self.assertFalse(M.dispatch()['success'])
        self.assertEqual(self.commands, [])


if __name__ == '__main__': unittest.main()

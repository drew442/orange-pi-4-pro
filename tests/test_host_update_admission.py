"""Installed-host admission integration; no services, block devices or KVM.

Engine fixtures replace the closure explicitly. Separate native tmpfs tests use
actual InstalledMaintenance/Admission with named physical and PID1 substitutes.
"""
import contextlib
import copy
import fcntl
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
NATIVE_FSTAT = os.fstat


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, BASE / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def engine_maintenance():
    """No-authority closure substitute for regular-file engine-only fixtures."""
    class EngineClosure:
        def __init__(self, storage):
            self.receipt = {'fixture': 'mock closure; no admission authority', 'success': False}
        def recheck(self):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *unused):
            self.close()
        def finish_success(self):
            self.receipt['success'] = True
        def close(self):
            pass
    return SimpleNamespace(InstalledMaintenance=EngineClosure, registered_storage=lambda: {})


class Discovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.M = load('update_discovery', 'scripts/guest-host-maintenance.py')

    def test_root_and_runtime_refuse_before_registry_open(self):
        M = self.M
        with patch.object(M.os, 'getresuid', return_value=(1000, 1000, 1000)), \
                patch.object(M.CONTEXT.ENROLL, 'HeldPath') as held:
            with self.assertRaisesRegex(M.ADMISSION.AdmissionError, 'actual root'):
                M.registered_storage()
            held.assert_not_called()
        with patch.object(M.os, 'getresuid', return_value=(0, 0, 0)), \
                patch.object(M.CONTEXT, 'host_identity'), \
                patch.object(M.CONTEXT.MANAGEMENT, 'runtime_guard', side_effect=ValueError('partial root')), \
                patch.object(M.CONTEXT.ENROLL, 'HeldPath') as held:
            with self.assertRaisesRegex(ValueError, 'partial root'):
                M.registered_storage()
            held.assert_not_called()

    @unittest.skipUnless(os.getresuid() == (0, 0, 0), 'protected discovery fixture requires root')
    def test_exact_path_canonical_protected_bounded_detached_storage_without_lease(self):
        M = self.M
        storage = dict(state_uuid='22222222-2222-4222-8222-222222222222',
                       guest_uuid='33333333-3333-4333-8333-333333333333', nvme_serial='SSD-123')
        value = dict(schema=1, profile=M.CONTEXT.REGISTRY.PROFILE, generation=0,
                     storage=storage, guests=[])
        raw = M.CONTEXT.REGISTRY.canonical(value)
        with tempfile.TemporaryDirectory(dir='/run') as directory:
            path = Path(directory) / 'autostart.json'
            path.write_bytes(raw)
            path.chmod(0o400)
            native_held, native_stat = M.CONTEXT.ENROLL.HeldPath, os.stat
            def held(requested):
                self.assertEqual(requested, '/run/a733-state/guests/autostart.json')
                return native_held(str(path))
            def state_stat(requested, *args, **kwargs):
                return native_stat(directory if requested == '/run/a733-state' else requested,
                                   *args, **kwargs)
            with patch.object(M.CONTEXT.ENROLL, 'HeldPath', side_effect=held), \
                    patch.object(M.os, 'stat', side_effect=state_stat), \
                    patch.object(M.CONTEXT, 'host_identity'), \
                    patch.object(M.CONTEXT.MANAGEMENT, 'runtime_guard') as guard, \
                    patch.object(M.ADMISSION.Admission, 'open_existing', side_effect=AssertionError('lease acquired')):
                result = M.registered_storage()
                self.assertEqual(result, storage)
                result['nvme_serial'] = 'changed'
                self.assertEqual(M.registered_storage(), storage)
                self.assertEqual(guard.call_count, 4)
                for data, mode in ((raw + b' ', 0o400), (raw, 0o600),
                                   (b'x' * (M.CONTEXT.REGISTRY.LIMIT + 1), 0o400),
                                   (b'{"schema":1,"schema":1}', 0o400)):
                    path.chmod(0o600)
                    path.write_bytes(data)
                    path.chmod(mode)
                    with self.assertRaises((ValueError, M.ADMISSION.AdmissionError)):
                        M.registered_storage()
                path.unlink()
                with self.assertRaises(FileNotFoundError):
                    M.registered_storage()


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'native tmpfs/flock fixture requires root')
class AdmissionIntegration(unittest.TestCase):
    def setUp(self):
        # Reuse fixture construction, without inheriting or repeating its tests.
        self.fixture = load('update_native_fixture', 'tests/test_guest_host_maintenance.py')
        self.fixture.Installed.setUp(self)
        self.M = self.fixture.M
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        # Exact protected-path discovery is tested above. This substitution only
        # supplies the physical tuple used by the explicit HostContext fixture.
        self.stack.enter_context(patch.object(self.M, 'registered_storage',
                                             return_value=copy.deepcopy(self.fixture.STORAGE)))

    def gate(self):
        return self.fixture.Installed.gate(self)

    def engine(self, kind):
        if kind == 'update':
            module = load('update_engine_fixture', 'tests/test_update_host.py')
            case = module.StagingTests()
            command = module.UPDATE
        else:
            module = load('selector_engine_fixture', 'tests/test_select_host_root.py')
            case = module.SelectionTransactionTests()
            command = module.LIVE
        case.setUp()
        self.stack.callback(case.doCleanups)
        case.transaction()
        # Native root fixture must preserve real admission/KVM-group metadata;
        # the old engine's global root-owner fstat substitute is unnecessary.
        self.stack.enter_context(patch.object(command.os, 'fstat', new=NATIVE_FSTAT))
        self.stack.enter_context(patch.object(command, 'maintenance_helper', return_value=self.M))
        return case, command

    def assert_locks_released_at_finish(self, case, command, native):
        update = command if hasattr(command, 'update_lock') else command.UPDATE
        # Acquire the same real flock through another open description. Finish
        # must run outside the subsystem lock and closed target descriptor.
        lockpath = case.boot / 'update.lock'
        target = case.target_path if hasattr(case, 'target_path') else case.target
        def finish(operation):
            with update.update_lock(case.context):
                fd = os.open(target, os.O_RDONLY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
            self.assertTrue(lockpath.exists())
            return native(operation)
        return finish

    def test_update_and_selection_stop_close_mutate_unlock_finish_and_close(self):
        # One real admission owner per test boot; successful update permits the
        # second operation to acquire its own distinct closure nonce.
        for kind in ('update', 'select'):
            with self.subTest(kind=kind):
                case, command = self.engine(kind)
                native = self.M.InstalledMaintenance.finish_success
                finish = self.assert_locks_released_at_finish(case, command, native)
                with patch.object(self.M.InstalledMaintenance, 'finish_success', new=finish):
                    result = case.run_apply()
                self.assertTrue(result['success'], result)
                self.assertTrue(result['maintenance']['open_acknowledged'])
                self.assertEqual(self.gate()['state'], 'open')
                with self.admission.admit():
                    pass
        self.assertEqual(self.events, ['stop', 'census', 'census'] * 2)

    def test_manual_lease_closes_then_refuses_before_host_write(self):
        case, command = self.engine('update')
        with self.admission.admit():
            result = case.run_apply()
        case.assert_untouched(result)
        self.assertEqual(result['maintenance']['phase'], 'exclusive-lease')
        self.assertEqual(self.gate()['state'], 'closed')
        self.assertFalse((case.boot / 'update.lock').exists())

    def test_closed_gate_refuses_selection_before_journal(self):
        case, command = self.engine('select')
        owner = self.admission.close_gate('shutdown')
        result = case.run_apply()
        case.assert_unpublished(result)
        self.assertEqual(result['maintenance']['phase'], 'close-gate')
        self.assertEqual(self.gate()['owner_nonce'], owner.nonce)
        self.assertFalse((case.boot / 'update.lock').exists())

    def test_pid1_and_partial_root_refuse_before_host_write(self):
        case, command = self.engine('update')
        for substitute in (patch.object(self.M.NORMAL, 'manager_state', return_value='stopping'),
                           patch.object(self.M.NORMAL.CONTEXT.MANAGEMENT, 'runtime_guard',
                                        side_effect=ValueError('partial root'))):
            with substitute:
                result = case.run_apply()
            case.assert_untouched(result)
            self.assertEqual(self.gate()['state'], 'open')
            self.assertFalse((case.boot / 'update.lock').exists())

    def test_plan_and_tty_refuse_before_maintenance_discovery(self):
        case, command = self.engine('update')
        with patch.object(command, 'maintenance_helper', side_effect=AssertionError('early maintenance')):
            case.context['gpt']['sha256'] = 'changed'
            case.assert_untouched(case.run_apply())
            case.auth.assert_not_called()
            case.context['gpt']['sha256'] = 'b' * 64
            case.auth.side_effect = ValueError('TTY refused')
            case.assert_untouched(case.run_apply())
        self.assertEqual(self.events, [])
        self.assertEqual(self.gate()['state'], 'open')

    def test_latest_registry_is_authenticated_after_readonly_discovery(self):
        case, command = self.engine('update')
        registry = self.state / 'guests/autostart.json'
        def discover():
            value = self.M.CONTEXT.REGISTRY.validate_registry(registry.read_bytes())
            value['guests'][0]['manifest_sha256'] = 'f' * 64
            registry.write_bytes(self.M.CONTEXT.REGISTRY.canonical(value))
            return copy.deepcopy(self.fixture.STORAGE)
        with patch.object(self.M, 'registered_storage', side_effect=discover):
            result = case.run_apply()
        case.assert_untouched(result)
        self.assertEqual(self.gate()['state'], 'closed')
        self.assertEqual(result['maintenance']['phase'], 'entry-quiescence')
        self.assertFalse((case.boot / 'update.lock').exists())

    def test_write_callback_keeps_exclusive_lease_and_subsystem_lock(self):
        case, command = self.engine('update')
        native = command.WRITER.stream_copy_verify
        def write(*args, **options):
            self.assertEqual(self.gate()['state'], 'closed')
            with self.assertRaises(self.M.ADMISSION.AdmissionError):
                self.admission.admit()
            with self.assertRaises(BlockingIOError):
                with command.update_lock(case.context):
                    pass
            options['source_check']()
            return native(*args, **options)
        with patch.object(command.WRITER, 'stream_copy_verify', side_effect=write):
            result = case.run_apply()
        self.assertTrue(result['success'], result)

    def test_owned_journal_cleanup_failure_surfaces_and_keeps_gate_closed(self):
        case, command = self.engine('select')
        native_unlink = command.os.unlink
        def cleanup(name, *args, **kwargs):
            if str(name).startswith('.selection-'):
                raise OSError('owned journal cleanup failed')
            return native_unlink(name, *args, **kwargs)
        with patch.object(command.PREP, 'rename_new', side_effect=OSError('journal publication failed')), \
                patch.object(command.os, 'unlink', side_effect=cleanup):
            result = case.run_apply()
        self.assertFalse(result['success'])
        self.assertIn('owned journal cleanup failed', result['error'])
        self.assertFalse(result['selection_published'])
        self.assertEqual(self.gate()['state'], 'closed')

    def test_selector_publication_failure_keeps_visible_bits_and_closed_gate(self):
        case, command = self.engine('select')
        native = command.SELECT.atomic_store
        def publish(directory, data):
            native(directory, data)
            raise command.SELECT.PublicationError('test publication', True)
        with patch.object(command.SELECT, 'atomic_store', side_effect=publish):
            result = case.run_apply()
        self.assertFalse(result['success'])
        self.assertTrue(result['selection_published'])
        self.assertFalse(result['publication_durable'])
        self.assertEqual(self.gate()['state'], 'closed')
        self.assertFalse(result['maintenance']['open_attempted'])

    def test_context_change_after_closure_refuses_before_write(self):
        case, command = self.engine('update')
        native = command.update_lock
        @contextlib.contextmanager
        def changed(context):
            self.bad_context = True
            with native(context) as held:
                yield held
        with patch.object(command, 'update_lock', new=changed):
            result = case.run_apply()
        case.assert_untouched(result)
        self.assertEqual(self.gate()['state'], 'closed')

    def test_failed_write_keeps_counters_and_closed_maintenance(self):
        case, command = self.engine('update')
        def failed(source, fd, expected, **options):
            options['source_check']()
            os.pwrite(fd, b'partial', 0)
            options['report'].update(target_modified=True, bytes_attempted=8192, bytes_written=7)
            raise OSError('write failed')
        with patch.object(command.WRITER, 'stream_copy_verify', side_effect=failed):
            result = case.run_apply()
        self.assertFalse(result['success'])
        self.assertEqual(result['bytes_written'], 7)
        self.assertEqual(self.gate()['state'], 'closed')
        self.assertFalse(result['maintenance']['open_attempted'])

    def test_finish_failure_keeps_durable_selection_and_closed_gate(self):
        case, command = self.engine('select')
        calls = 0
        def census():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('final consumer remains')
        with patch.object(self.M, 'consumer_check', side_effect=census):
            result = case.run_apply()
        self.assertFalse(result['success'])
        self.assertTrue(result['selection_published'])
        self.assertTrue(result['publication_durable'])
        self.assertEqual(self.gate()['state'], 'closed')
        self.assertEqual(result['maintenance']['phase'], 'final-consumers')

    def test_close_after_open_failure_is_overall_failure_with_written_bits(self):
        case, command = self.engine('update')
        case.args.verify_readback = True
        case.plan = command.make_plan(case.args, case.source)
        native = self.M.LIFECYCLE.StateController.close
        def closed(controller):
            native(controller)
            raise OSError('controller close acknowledgement')
        with patch.object(self.M.LIFECYCLE.StateController, 'close', new=closed):
            result = case.run_apply()
        self.assertFalse(result['success'])
        self.assertTrue(result['target_modified'])
        self.assertEqual(result['bytes_verified'], 8192)
        self.assertTrue(result['maintenance']['open_acknowledged'])
        self.assertEqual(len(result['maintenance']['close_errors']), 1)
        self.assertEqual(self.gate()['state'], 'open')
        with self.admission.admit():
            pass

    def test_open_publication_error_preserves_nested_uncertainty(self):
        case, command = self.engine('select')
        error = self.M.ADMISSION.PublicationError('open-rename', True)
        with patch.object(self.M.ADMISSION.ClosureOwner, 'open', side_effect=error):
            result = case.run_apply()
        self.assertFalse(result['success'])
        self.assertTrue(result['selection_published'])
        self.assertEqual(result['maintenance']['publication_error'],
                         dict(stage=error.stage, uncertain=error.uncertain))

    def test_open_ack_uncertainty_keeps_selection_bits_and_nested_receipt(self):
        case, command = self.engine('select')
        native = self.M.ADMISSION.ClosureOwner.open
        def lost(owner):
            native(owner)
            raise OSError('lost open acknowledgement')
        with patch.object(self.M.ADMISSION.ClosureOwner, 'open', new=lost):
            result = case.run_apply()
        self.assertFalse(result['success'])
        self.assertTrue(result['selection_published'])
        self.assertTrue(result['publication_durable'])
        self.assertEqual(result['maintenance']['gate_state'], 'uncertain')
        self.assertEqual(self.gate()['state'], 'open')


if __name__ == '__main__':
    unittest.main()

"""CLI composition only; actual destination custody is tested independently."""
from contextlib import ExitStack, redirect_stderr
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


M = load('restore_cli_test', 'scripts/restore-managed-guest.py')
C = load('restore_cli_resources', 'scripts/managed-guest-restore-custody.py')
PIN = 'a' * 64
TXID = '33333333-3333-4333-8333-333333333333'


class ObserverError(RuntimeError):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__('historical observer cleanup failed')


class Resource:
    def __init__(self, name, events):
        self.name, self.events, self.closed = name, events, False
        self.value = {'storage': {'test': True}}
        self.context = SimpleNamespace(state_fd=10, guest_fd=11)
        self.fail_close = False

    def check(self):
        if self.closed:
            raise ValueError('closed resource')

    recheck = check

    def close(self):
        self.closed = True
        self.events.append('close-' + self.name)
        if self.fail_close:
            raise OSError(self.name + ' close failed')


class RestoreCLI(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.events = []
        self.policy, self.custody, self.source = [Resource(name, self.events)
                                                  for name in ('policy', 'custody', 'source')]
        self.custody.receipt = dict(activation=dict(success=True, mount_retained=True,
            mount_attempted=True, guest_may_be_modified=True, cleanup_errors=[]))
        self.transaction = Resource('transaction', self.events)
        self.transaction.receipt = dict(success=True, data_committed=True, transaction_id=TXID)
        self.transaction.execute = Mock(side_effect=self.execute)
        self.c = SimpleNamespace(ADMISSION=C.ADMISSION, REGISTRY=C.REGISTRY,
            Resources=C.Resources, BOOT_POLICY=SimpleNamespace(Policy=Mock(return_value=self.policy)),
            ShutdownRestoreCustody=Mock(side_effect=self.open_custody))
        self.module = SimpleNamespace(CUSTODY=self.c,
            SOURCE=SimpleNamespace(Source=Mock(side_effect=self.open_source)),
            RestoreTransaction=Mock(return_value=self.transaction),
            TransactionError=ObserverError,
            observe_completed=Mock(return_value=None))
        for target, name, value in [(M, 'engine', Mock(return_value=self.module)),
                (M.os, 'getresuid', Mock(return_value=(0, 0, 0))),
                (M.os, 'fstat', Mock(return_value=SimpleNamespace(st_dev=123)))]:
            self.stack.enter_context(patch.object(target, name, value))
        self.stack.enter_context(redirect_stderr(io.StringIO()))

    def open_custody(self, expected, name, *, progress, activate_missing):
        self.assertEqual(expected, self.policy.value)
        self.assertEqual(name, 'autotest')
        self.assertTrue(callable(progress))
        self.assertIs(activate_missing, True)
        self.events.append('shutdown-custody')
        return self.custody

    def open_source(self, path, archive_pin, manifest_pin, devices, *, progress):
        self.assertIn('shutdown-custody', self.events)
        self.assertFalse(self.custody.closed)
        self.assertEqual((path, archive_pin, manifest_pin, devices), ('/archive', PIN, PIN, {123}))
        self.assertTrue(callable(progress))
        self.events.append('source-open')
        return self.source

    def execute(self):
        self.assertIn('source-open', self.events)
        self.assertFalse(self.custody.closed or self.source.closed)
        self.events.append('execute')
        return self.transaction.receipt.copy()

    def run_restore(self, **kwargs):
        return M.restore('autotest', '/archive', PIN, PIN, **kwargs)

    def test_live_restore_owns_custody_before_archive_and_closes_in_order(self):
        result = self.run_restore()
        self.assertTrue(result['success'] and result['data_committed'])
        self.assertFalse(result['hardware_acceptance'] or result['historical'])
        self.assertTrue(result['activation']['guest_may_be_modified'])
        self.assertEqual(self.events, ['shutdown-custody', 'source-open', 'execute',
            'close-transaction', 'close-source', 'close-custody', 'close-policy'])
        self.module.RestoreTransaction.assert_called_once_with(
            self.custody, self.source, transaction_id=None, resume=False)

    def test_completed_repeat_observes_before_custody_or_archive_access(self):
        self.module.observe_completed.return_value = dict(success=True, data_committed=True,
                                                         historical=True, transaction_id=TXID)
        result = self.run_restore(resume=TXID)
        self.assertTrue(result['success'] and result['historical'] and result['data_committed'])
        self.assertEqual(self.events, ['close-policy'])
        self.c.ShutdownRestoreCustody.assert_not_called()
        self.module.SOURCE.Source.assert_not_called()
        self.module.RestoreTransaction.assert_not_called()

    def test_incomplete_explicit_resume_names_exact_transaction(self):
        self.assertTrue(self.run_restore(resume=TXID)['success'])
        self.module.observe_completed.assert_called_once_with(self.policy, 'autotest', TXID, PIN, PIN)
        self.module.RestoreTransaction.assert_called_once_with(
            self.custody, self.source, transaction_id=TXID, resume=True)

    def test_unsafe_observation_does_not_fall_through_to_mutation(self):
        self.module.observe_completed.side_effect = ValueError('unsafe completion')
        result = self.run_restore(resume=TXID)
        self.assertFalse(result['success'])
        self.assertIn('unsafe completion', result['error'])
        self.c.ShutdownRestoreCustody.assert_not_called()
        self.module.SOURCE.Source.assert_not_called()

    def test_constructor_failure_keeps_source_and_custody_cleanup(self):
        self.module.RestoreTransaction.side_effect = ValueError('constructor refused')
        result = self.run_restore()
        self.assertFalse(result['success'] or result['data_committed'])
        self.assertTrue(self.source.closed and self.custody.closed and self.policy.closed)

    def test_archive_refusal_never_constructs_transaction(self):
        self.module.SOURCE.Source.side_effect = ValueError('encoded hash differs')
        result = self.run_restore()
        self.assertFalse(result['success'] or result['data_committed'])
        self.module.RestoreTransaction.assert_not_called()
        self.assertTrue(self.custody.closed and self.policy.closed)
        self.assertTrue(result['activation']['guest_may_be_modified'])
        self.assertTrue(result['activation']['mount_retained'])

    def test_cleanup_failure_preserves_data_commit_but_fails_command(self):
        self.source.fail_close = True
        result = self.run_restore()
        self.assertFalse(result['success'])
        self.assertTrue(result['data_committed'] and result['cleanup_uncertain'])
        self.assertIn('source close failed', str(result['failure']))
        self.assertTrue(self.custody.closed and self.policy.closed)

    def test_historical_policy_close_failure_preserves_historical_commit(self):
        self.module.observe_completed.return_value = dict(success=True, data_committed=True,
                                                         historical=True, transaction_id=TXID)
        self.policy.fail_close = True
        result = self.run_restore(resume=TXID)
        self.assertFalse(result['success'])
        self.assertTrue(result['data_committed'] and result['historical'])
        self.module.SOURCE.Source.assert_not_called()

    def test_observer_close_failure_preserves_authenticated_historical_commit(self):
        receipt = dict(data_committed=True, historical=True, transaction_id=TXID,
                       success=False, cleanup_uncertain=True)
        self.module.observe_completed.side_effect = ObserverError(receipt)
        result = self.run_restore(resume=TXID)
        self.assertFalse(result['success'])
        self.assertTrue(result['data_committed'] and result['historical'] and result['cleanup_uncertain'])
        self.c.ShutdownRestoreCustody.assert_not_called()
        self.module.SOURCE.Source.assert_not_called()

    def test_observer_and_policy_close_failures_keep_both_errors_and_commit(self):
        receipt = dict(data_committed=True, historical=True, transaction_id=TXID,
                       success=False, cleanup_uncertain=True)
        self.module.observe_completed.side_effect = ObserverError(receipt)
        self.policy.fail_close = True
        result = self.run_restore(resume=TXID)
        self.assertFalse(result['success'])
        self.assertTrue(result['data_committed'] and result['historical'])
        self.assertIn('historical observer cleanup failed', str(result['failure']))
        self.assertIn('policy close failed', str(result['failure']))

    def test_foreign_or_wrong_transaction_receipt_cannot_claim_historical_commit(self):
        for error in (ValueError('foreign observer error'), ObserverError(dict(
                data_committed=True, historical=True, transaction_id='different'))):
            if not hasattr(error, 'receipt'):
                error.receipt = dict(data_committed=True, historical=True, transaction_id=TXID)
            with self.subTest(error=type(error).__name__):
                self.policy.closed = False
                self.module.observe_completed.side_effect = error
                result = self.run_restore(resume=TXID)
                self.assertFalse(result['success'] or result['data_committed'] or result['historical'])

    def test_refuse_failed_transaction_receipt_and_keep_acknowledged_state(self):
        self.transaction.receipt['success'] = False
        result = self.run_restore()
        self.assertFalse(result['success'])
        self.assertTrue(result['data_committed'])
        self.assertTrue(self.source.closed and self.custody.closed)

    def test_inputs_and_reserved_manual_name_refuse_before_policy(self):
        for name, pin, resume in [('firstkvm', PIN, None), ('bad/name', PIN, None),
                                 ('autotest', 'not-a-pin', None), ('autotest', PIN, 'not-a-uuid')]:
            with self.subTest(name=name, pin=pin, resume=resume):
                self.assertFalse(M.restore(name, '/archive', pin, PIN, resume=resume)['success'])
        self.c.BOOT_POLICY.Policy.assert_not_called()

    def test_nonroot_and_bad_lexical_path_do_not_load_engine(self):
        for value in ('relative', '/archive/../other'):
            self.assertFalse(M.restore('autotest', value, PIN, PIN)['success'])
        with patch.object(M.os, 'getresuid', return_value=(1000, 1000, 1000)):
            self.assertFalse(self.run_restore()['success'])
        M.engine.assert_not_called()


if __name__ == '__main__':
    unittest.main()

"""Actual ExitStack cleanup receipts with explicit checkpoint-authority substitutes."""
from contextlib import nullcontext
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

SPEC = importlib.util.spec_from_file_location(
    'checkpoint_cleanup_tests', Path(__file__).resolve().parents[1] /
    'scripts/managed-guest-checkpoint.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class CleanupTests(unittest.TestCase):
    def custody(self):
        held = M.CheckpointCustody(M._TOKEN)
        held.result = M.result_for('cleanup-test', 'one')
        held._expected = b'fixture head'
        held.store = SimpleNamespace(sync=Mock(return_value={}))
        held.recheck = Mock()
        held.transition = Mock()
        read, write = os.pipe()
        self.live = {read, write}
        self.addCleanup(self.cleanup_pipe)
        self.closed = []
        def failed_close():
            self.live.remove(read)
            os.close(read)
            self.closed.append('reported-failure-after-close')
            raise OSError('first cleanup acknowledgement lost')
        def other_close():
            self.live.remove(write)
            os.close(write)
            self.closed.append('other-close')
        held._stack.callback(other_close)
        held._stack.callback(failed_close)
        return held

    def cleanup_pipe(self):
        while self.live:
            os.close(self.live.pop())

    def test_owner_retains_first_failure_after_idempotent_second_close(self):
        held = self.custody()
        with self.assertRaisesRegex(OSError, 'acknowledgement lost'):
            held.close()
        first = held.result['close_error']
        held.close()
        self.assertEqual(held.result['close_error'], first)
        self.assertEqual(self.closed, ['reported-failure-after-close', 'other-close'])
        self.assertTrue(held._closed)

    def test_checkpoint_does_not_erase_success_path_cleanup_failure(self):
        held = self.custody()
        outcome = SimpleNamespace(_supervisor=SimpleNamespace(receipt={'errors': []}))
        with patch.object(M.CheckpointCustody, 'from_outcome', return_value=held), \
             patch.object(M, '_prepare', return_value=[{}, {}]), \
             patch.object(M, '_publish_manifest'), patch.object(M, '_disk'), \
             patch.object(M, '_finish_registry'):
            with self.assertRaises(M.LIFECYCLE.OperationError) as caught:
                M.checkpoint(outcome)
        self.assertFalse(caught.exception.receipt['success'])
        self.assertIn('acknowledgement lost', caught.exception.receipt['close_error'])
        self.assertIsInstance(caught.exception.cause, OSError)
        self.assertEqual(len(self.closed), 2)

    def test_reconcile_does_not_erase_success_path_cleanup_failure(self):
        held = self.custody()
        with patch.object(M, '_fresh', return_value=held), \
             patch.object(M.HEAD, 'validate_head', return_value={'status': 'ready'}), \
             patch.object(M, '_cohort_records', return_value=[{}, {}]), \
             patch.object(M, '_open_intended_disk', return_value=True), \
             patch.object(M, '_disk'), patch.object(M, '_finish_registry'):
            with self.assertRaises(M.LIFECYCLE.OperationError) as caught:
                M.reconcile(object(), 'one')
        self.assertFalse(caught.exception.receipt['success'])
        self.assertIn('acknowledgement lost', caught.exception.receipt['close_error'])
        self.assertEqual(len(self.closed), 2)

    def test_ready_authentication_does_not_erase_cleanup_failure(self):
        held = self.custody()
        with patch.object(M, '_fresh', return_value=held), \
             patch.object(M.HEAD, 'validate_head', return_value={'status': 'ready', 'sequence': 1}), \
             patch.object(M, '_cohort_records'), \
             patch.object(M, '_open_intended_disk', return_value=True), \
             patch.object(M, '_disk'), patch.object(M, '_finish_registry'):
            with self.assertRaises(M.LIFECYCLE.OperationError) as caught:
                M.authenticate_ready_cohort(object(), 'one')
        self.assertFalse(caught.exception.receipt['success'])
        self.assertIn('acknowledgement lost', caught.exception.receipt['close_error'])
        self.assertEqual(len(self.closed), 2)

    def test_partial_factory_retains_primary_and_unreturned_custody_cleanup(self):
        held = self.custody()
        primary = ValueError('original authentication failed')
        class Controller:
            def __init__(self, *args, **kwargs):
                self.context = SimpleNamespace(storage={})
                self.lease, self.boot_id = object(), 'boot-fixture'
                self.admission = SimpleNamespace(guest_lock=lambda *a, **k: nullcontext(object()))
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        def failed_open(self):
            # Transfer actual owned cleanup callbacks into factory-created custody.
            self._stack = held._stack
            raise primary
        with patch.object(M.LIFECYCLE, 'StateController', Controller), \
             patch.object(M.CheckpointCustody, '_open', failed_open):
            with self.assertRaises(M.LIFECYCLE.OperationError) as caught:
                M._fresh(Controller(), 'one')
        self.assertIs(caught.exception.cause, primary)
        self.assertIn('acknowledgement lost', caught.exception.receipt['close_error'])
        self.assertEqual(len(self.closed), 2)


if __name__ == '__main__':
    unittest.main()

"""Host-only managed admission/CAS tests; protected I/O is real, hardware mocked."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'managed_controller_fixtures', Path(__file__).with_name('test_guest_lifecycle_controller.py'))
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
c = fixtures.c
RUN = '44444444-4444-4444-8444-444444444444'
NEXT_RUN = '55555555-5555-4555-8555-555555555555'


class ManagedRunTests(fixtures.ControllerTests):
    # Inherit the fixture, but avoid duplicating the controller's test collection.
    def setUp(self):
        super().setUp()
        self.enroll(enabled=True, fixture_profile='managed-power-key-v1')
        self.events.clear()

    def admit(self, **kwargs):
        session = self.controller.admit_run('autotest', fixtures.PIN, RUN, **kwargs)
        self.addCleanup(session.close)
        return session

    def rewrite_head(self, head):
        self.head.chmod(0o600)
        self.head.write_bytes(c.HEAD.canonical(head))
        self.head.chmod(0o400)

    def head_value(self):
        return c.HEAD.validate_head(self.head.read_bytes())

    def test_admission_record_precedes_head_and_unlocks_disk_but_retains_guest(self):
        publish = c.STORE.RootDirectory.publish
        events = []
        def observe(store, name, data, **kwargs):
            events.append(name)
            if name == 'head.json':
                self.assertTrue((self.head.parent / 'runs' / (RUN + '.json')).exists())
                self.assertNotIn('inspect-close', self.events)
            return publish(store, name, data, **kwargs)
        with mock.patch.object(c.STORE.RootDirectory, 'publish', observe):
            session = self.admit(automatic=True)
        self.assertEqual(events[0], RUN + '.json')
        self.assertEqual(events[-1], 'head.json')
        self.assertEqual(len(events), 3)
        self.assertEqual(len(events[1]), 134)
        self.assertIn('inspect-close', self.events)
        self.assertNotIn('guest-unlock', self.events)
        self.assertEqual(session.recheck()['status'], 'admitted')
        self.assertIs(session.lease, self.lease)
        record_path = self.head.parent / 'runs' / (RUN + '.json')
        record = c.validate_run(record_path.read_bytes())
        self.assertEqual(record['previous_head']['status'], 'ready')
        self.assertEqual(record['fixture_profile'], 'managed-power-key-v1')
        self.assertEqual(record['shutdown_profile'], 'qmp-guest-shutdown-v1')
        self.assertEqual(record_path.stat().st_mode & 0o777, 0o400)
        self.assertEqual(record_path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(session.admission_sha256, c.digest(record_path.read_bytes()))
        session.manifest['config']['name'] = 'bad'
        session.config['name'] = 'bad'
        self.assertEqual(session.config['name'], 'autotest')
        session.close()
        self.assertIn('guest-unlock', self.events)
        self.assertEqual(self.head_value()['status'], 'admitted')
        self.assertFalse(self.controller.context.closed)

    def test_transitions_cas_and_no_checkpoint_completion_api(self):
        session = self.admit()
        old = session.head_sha256
        result = session.mark_running()
        self.assertTrue(result['success'])
        self.assertNotEqual(old, session.head_sha256)
        self.assertEqual(session.recheck()['status'], 'running')
        session.mark_stopping()
        self.assertEqual(session.recheck()['status'], 'stopping')
        self.assertFalse(hasattr(session, 'complete'))
        self.assertFalse(hasattr(session, 'mark_checkpoint'))

    def test_disable_during_admitted_run_preserves_authority(self):
        session = self.admit()
        old = session.head_sha256
        self.controller.disable('autotest')
        self.assertFalse(self.controller.registry()['registry']['guests'][0]['enabled'])
        self.assertEqual(session.head_sha256, old)
        session.mark_running()

    def test_disabled_automatic_refuses_but_explicit_admission_allowed(self):
        self.controller.disable('autotest')
        self.failure(lambda: self.admit(automatic=True), 'disabled')
        self.assertFalse((self.head.parent / 'runs').exists())
        self.admit()

    def test_automatic_once_per_boot_and_historical_run_id_never_adopted(self):
        previous = self.head_value()
        previous.update(sequence=4, previous_head_sha256='b' * 64,
                        last_boot_id=fixtures.BOOT, run_id=NEXT_RUN,
                        last_completed_intent_sha256='c' * 64)
        self.rewrite_head(previous)
        self.failure(lambda: self.admit(automatic=True), 'boot already admitted')
        with mock.patch.object(c, '_READY_VERIFIER', return_value={'success': True}):
            session = self.admit()
        session.close()
        # Simulate independently completed authority solely to exercise UUID exclusion.
        completed = self.head_value()
        completed.update(status='ready', sequence=6,
                         previous_head_sha256='d' * 64,
                         run_id=NEXT_RUN, last_completed_intent_sha256='c' * 64)
        self.rewrite_head(completed)
        old = self.head.read_bytes()
        with mock.patch.object(c, '_READY_VERIFIER', return_value={'success': True}):
            self.failure(self.admit, 'destination already exists')
        self.assertEqual(old, self.head.read_bytes())

    def test_completed_head_requires_registered_cohort_verifier(self):
        previous = self.head_value()
        previous.update(sequence=5, previous_head_sha256='b' * 64,
                        last_boot_id=fixtures.BOOT, run_id=NEXT_RUN,
                        last_completed_intent_sha256='c' * 64)
        self.rewrite_head(previous)
        self.failure(self.admit, 'cohort verifier unavailable')
        self.assertFalse((self.head.parent / 'runs').exists())

    def test_legacy_fixture_refused_before_run_publication(self):
        self.rewrite_registry(lambda registry: registry['guests'][0].update(
            fixture_profile='first-kvm-http-v1'))
        self.failure(self.admit, 'unsupported managed')
        self.assertFalse((self.head.parent / 'runs').exists())

    def test_invalid_bindings_refuse_before_record_publication(self):
        original = self.head_value()
        for update, message in (({'storage': {**fixtures.STORAGE, 'nvme_serial': 'other'}}, 'storage'),
                                ({'manifest_sha256': 'b' * 64}, 'pin differs'),
                                ({'uid': 60003, 'gid': 60003}, 'identity differs')):
            with self.subTest(update=update):
                self.rewrite_head({**original, **update})
                self.failure(self.admit, message)
                self.assertFalse((self.head.parent / 'runs').exists())
        self.rewrite_head(original)
        self.markers.clear()
        self.failure(self.admit, 'marker missing')
        self.markers.add('autotest')
        self.disk['sha256'] = 'e' * 64
        self.failure(self.admit, 'disk differs')

    def test_registry_identity_pin_storage_conflicts_refuse(self):
        original = self.registry.read_bytes()
        for change in (lambda v: v['guests'][0].update(manifest_sha256='b' * 64),
                       lambda v: v['guests'][0].update(uid=60003, gid=60003),
                       lambda v: v['storage'].update(nvme_serial='other')):
            self.rewrite_registry(change)
            self.failure(self.admit)
            self.registry.chmod(0o600)
            self.registry.write_bytes(original)
            self.registry.chmod(0o400)
        self.assertFalse((self.head.parent / 'runs').exists())

    def test_guard_changes_and_tampered_records_fail_recheck(self):
        session = self.admit()
        self.allow = False
        with self.assertRaises(ValueError):
            session.recheck()
        self.allow = True
        self.markers.clear()
        with self.assertRaises(ValueError):
            session.recheck()
        self.markers.add('autotest')
        record = self.head.parent / 'runs' / (RUN + '.json')
        record.chmod(0o600)
        record.write_bytes(b'{}\n')
        record.chmod(0o400)
        with self.assertRaises(ValueError):
            session.recheck()

    def test_stale_head_transition_poisoned_and_never_overwrites(self):
        session = self.admit()
        current = self.head_value()
        current = c.HEAD.mark_running(current, expected_head_sha256=session.head_sha256)
        self.rewrite_head(current)
        stable = self.head.read_bytes()
        self.failure(session.mark_running, 'pin differs')
        self.assertEqual(stable, self.head.read_bytes())
        with self.assertRaisesRegex(ValueError, 'poisoned'):
            session.recheck()

    def test_postpublication_run_failure_leaves_ready_head_and_excludes_uuid_reuse(self):
        previous = self.head.read_bytes()
        publish = c.STORE.RootDirectory.publish
        def fail(store, name, data, **kwargs):
            result = publish(store, name, data, **kwargs)
            if name == RUN + '.json':
                result.update(stage='postpublication', success=False, uncertain=True)
                raise c.STORE.PublicationError(result, OSError('injected'))
            return result
        with mock.patch.object(c.STORE.RootDirectory, 'publish', fail):
            result = self.failure(self.admit)
        self.assertTrue(result['publication_error']['published'])
        self.assertEqual(previous, self.head.read_bytes())
        self.failure(self.admit, 'destination already exists')

    def test_disable_at_last_registry_lock_prevents_automatic_admission(self):
        self.on_registry_lock = lambda: self.rewrite_registry(
            lambda value: value['guests'][0].update(enabled=False))
        self.failure(lambda: self.admit(automatic=True), 'disabled')
        self.assertFalse((self.head.parent / 'runs').exists())

    def test_unrelated_disable_preserves_running_admission(self):
        self.rewrite_registry(lambda value: value['guests'].append(
            {**value['guests'][0], 'name': 'other', 'uid': 60003, 'gid': 60003}))
        session = self.admit()
        self.controller.disable('other')
        self.assertEqual(session.recheck()['status'], 'admitted')
        self.assertTrue(self.controller.registry()['registry']['guests'][0]['enabled'])
        session.mark_running()

    def test_head_publication_rechecks_immutable_admission(self):
        session = self.admit()
        publish = c.STORE.RootDirectory.publish
        def corrupt(store, name, data, **kwargs):
            record = self.head.parent / 'runs' / (RUN + '.json')
            record.chmod(0o600)
            record.write_bytes(b'{}\n')
            record.chmod(0o400)
            return publish(store, name, data, **kwargs)
        with mock.patch.object(c.STORE.RootDirectory, 'publish', corrupt):
            self.failure(session.mark_running, 'pin differs')
        self.assertEqual(self.head_value()['status'], 'admitted')

    def test_postrename_admission_failure_retains_record_active_head_and_receipt(self):
        publish = c.STORE.RootDirectory.publish
        def fail(store, name, data, **kwargs):
            result = publish(store, name, data, **kwargs)
            if name == 'head.json':
                result.update(stage='postrename', success=False, uncertain=True)
                raise c.STORE.PublicationError(result, OSError('injected'))
            return result
        with mock.patch.object(c.STORE.RootDirectory, 'publish', fail):
            result = self.failure(self.admit)
        self.assertTrue(result['publication_error']['published'])
        self.assertEqual(self.head_value()['status'], 'admitted')
        self.assertTrue((self.head.parent / 'runs' / (RUN + '.json')).exists())
        self.failure(self.admit, 'ready head')

    def test_postrename_transition_failure_keeps_old_pin_and_poison(self):
        session = self.admit()
        old = session.head_sha256
        publish = c.STORE.RootDirectory.publish
        def fail(store, name, data, **kwargs):
            result = publish(store, name, data, **kwargs)
            if name == 'head.json':
                result.update(stage='postrename', success=False, uncertain=True)
                raise c.STORE.PublicationError(result, OSError('injected'))
            return result
        with mock.patch.object(c.STORE.RootDirectory, 'publish', fail):
            result = self.failure(session.mark_running)
        self.assertTrue(result['publication_error']['published'])
        self.assertEqual(session.head_sha256, old)
        self.assertEqual(self.head_value()['status'], 'running')
        self.failure(session.mark_stopping, 'poisoned')

    def test_quarantine_binds_full_current_head_and_immutable_failure(self):
        session = self.admit()
        previous, pin = self.head_value(), session.head_sha256
        result = session.quarantine('qmp-timeout')
        self.assertTrue(result['success'])
        failure_path = self.head.parent / 'runs' / (RUN + '.failure.json')
        failure = json.loads(failure_path.read_bytes())
        self.assertEqual(failure['head'], previous)
        self.assertEqual(failure['head_sha256'], pin)
        self.assertEqual(failure['code'], 'qmp-timeout')
        self.assertEqual(self.head_value()['failure_sha256'], c.digest(failure_path.read_bytes()))
        self.assertEqual(session.recheck()['status'], 'quarantined')
        self.assertEqual(len(result['publications']), 4)
        self.failure(lambda: session.quarantine('other'), 'destination already exists')

    def test_quarantine_head_failure_preserves_failure_receipt(self):
        session = self.admit()
        publish = c.STORE.RootDirectory.publish
        def fail(store, name, data, **kwargs):
            if name == 'head.json':
                raise c.STORE.PublicationError(dict(stage='publish', uncertain=True), OSError('injected'))
            return publish(store, name, data, **kwargs)
        with mock.patch.object(c.STORE.RootDirectory, 'publish', fail):
            result = self.failure(lambda: session.quarantine('child-lost'))
        self.assertTrue(result['publications'][0]['durable'])
        self.assertTrue((self.head.parent / 'runs' / (RUN + '.failure.json')).exists())
        self.assertEqual(self.head_value()['status'], 'admitted')

    def test_invalid_quarantine_code_poison_and_close_idempotent(self):
        session = self.admit()
        self.failure(lambda: session.quarantine('../unsafe'), 'reason code')
        session.close()
        session.close()
        self.assertEqual(self.events.count('guest-unlock'), 1)
        self.assertFalse(self.controller.context.closed)

    def test_close_attempts_every_resource_after_error_and_does_not_retry(self):
        session = self.admit()
        closed = []
        def fail():
            closed.append('fail')
            raise OSError('close failed')
        session._stack.callback(lambda: closed.append('other'))
        session._stack.callback(fail)
        with self.assertRaises(OSError):
            session.close()
        self.assertEqual(closed, ['fail', 'other'])
        self.assertTrue(session._store._closed)
        self.assertTrue(session._runs._closed)
        session.close()
        self.assertEqual(closed, ['fail', 'other'])

    def next_transition(self, session):
        previous = self.head.read_bytes()
        new = c.HEAD.canonical(c.HEAD.mark_running(
            self.head_value(), expected_head_sha256=session.head_sha256))
        name = c.digest(previous) + '-' + c.digest(new) + '.json'
        return name, c.head_transition(previous, new)

    def test_every_head_cas_has_durable_exact_predecessor_chain(self):
        publish = c.STORE.RootDirectory.publish
        seen = []
        def observe(store, name, data, **kwargs):
            if name == 'head.json':
                old = self.head.read_bytes()
                chain = self.head.parent / 'transitions' / (
                    c.digest(old) + '-' + c.digest(data) + '.json')
                self.assertTrue(chain.exists())
                value = c.validate_head_transition(chain.read_bytes())
                self.assertEqual(c.base64.b64decode(value['previous_head_base64']), old)
                self.assertEqual(c.base64.b64decode(value['new_head_base64']), data)
                seen.append(c.HEAD.validate_head(data)['status'])
            return publish(store, name, data, **kwargs)
        with mock.patch.object(c.STORE.RootDirectory, 'publish', observe):
            session = self.admit()
            session.mark_running()
            session.mark_stopping()
            session.quarantine('test-failure')
        self.assertEqual(seen, ['admitted', 'running', 'stopping', 'quarantined'])
        self.assertEqual(len(list((self.head.parent / 'transitions').iterdir())), 4)

    def test_exact_existing_chain_requires_sync_before_head_cas(self):
        session = self.admit()
        name, data = self.next_transition(session)
        chain = self.head.parent / 'transitions' / name
        chain.write_bytes(data)
        chain.chmod(0o400)
        sync = c.STORE.RootDirectory.sync
        calls = []
        def observe(store, record, **kwargs):
            calls.append(record)
            self.assertEqual(self.head_value()['status'], 'admitted')
            return sync(store, record, **kwargs)
        with mock.patch.object(c.STORE.RootDirectory, 'sync', observe):
            result = session.mark_running()
        self.assertEqual(calls, [name])
        self.assertTrue(result['synchronizations'][0]['durable'])
        self.assertEqual(chain.read_bytes(), data)

    def test_existing_chain_malformed_or_conflicting_bytes_refuse(self):
        session = self.admit()
        name, data = self.next_transition(session)
        chain = self.head.parent / 'transitions' / name
        # Valid JSON with the exact same embedded heads still cannot replace
        # immutable chain bytes with a differently encoded record.
        alternative = c.head_transition(self.head.read_bytes(), c.HEAD.canonical(
            c.HEAD.quarantine(self.head_value(), 'e' * 64,
                              expected_head_sha256=session.head_sha256)))
        for bad in (b'{}\n', data + b' ', alternative):
            if chain.exists():
                chain.chmod(0o600)
            chain.write_bytes(bad)
            chain.chmod(0o400)
            result = c.receipt('chain-test', 'autotest')
            previous = self.head.read_bytes()
            new = c.HEAD.canonical(c.HEAD.mark_running(
                self.head_value(), expected_head_sha256=session.head_sha256))
            with self.assertRaises(ValueError):
                with c.publish_head_transition(session._store, previous, new, result):
                    self.fail('conflicting chain admitted')
            self.assertEqual(self.head.read_bytes(), previous)
            self.assertEqual(chain.read_bytes(), bad)

    def test_chain_validation_rejects_hash_base64_schema_size_and_predecessor(self):
        session = self.admit()
        _, data = self.next_transition(session)
        original = json.loads(data)
        for changes in ({'schema': True}, {'new_head_sha256': 'f' * 64},
                        {'previous_head_base64': '!!!'}, {'extra': 1}):
            value = {**original, **changes}
            with self.assertRaises(ValueError):
                c.validate_head_transition(json.dumps(value).encode())
        with self.assertRaises(ValueError):
            c.validate_head_transition(b' ' * (c.TRANSITION_LIMIT + 1))
        # A valid new head still must identify these exact previous bytes.
        new = c.HEAD.validate_head(c.base64.b64decode(original['new_head_base64']))
        new['previous_head_sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            c.head_transition(self.head.read_bytes(), c.HEAD.canonical(new))

    def test_chain_directory_fsync_failure_leaves_old_head_and_admission_record(self):
        old = self.head.read_bytes()
        fsync = os.fsync
        target = self.head.parent / 'transitions'
        def fail(fd):
            if os.readlink('/proc/self/fd/' + str(fd)) == str(target):
                raise OSError('transition directory sync')
            return fsync(fd)
        with mock.patch.object(c.STORE.os, 'fsync', fail):
            result = self.failure(self.admit)
        self.assertEqual(self.head.read_bytes(), old)
        self.assertTrue(result['publication_error']['uncertain'])
        self.assertFalse(result['head_attempted'])
        self.assertTrue((self.head.parent / 'runs' / (RUN + '.json')).exists())

    def test_chain_published_then_head_failure_preserves_chain_and_old_pin(self):
        session = self.admit()
        old = session.head_sha256
        name, data = self.next_transition(session)
        publish = c.STORE.RootDirectory.publish
        def fail(store, record, content, **kwargs):
            if record == 'head.json':
                raise OSError('head CAS unavailable')
            return publish(store, record, content, **kwargs)
        with mock.patch.object(c.STORE.RootDirectory, 'publish', fail):
            result = self.failure(session.mark_running)
        self.assertEqual(session.head_sha256, old)
        self.assertEqual(self.head_value()['status'], 'admitted')
        self.assertEqual((self.head.parent / 'transitions' / name).read_bytes(), data)
        self.assertTrue(result['publications'][-1]['durable'])
        self.failure(session.mark_running, 'poisoned')

    def test_chain_postpublication_failure_does_not_attempt_head_cas(self):
        session = self.admit()
        old = session.head_sha256
        name, data = self.next_transition(session)
        publish = c.STORE.RootDirectory.publish
        def fail(store, record, content, **kwargs):
            result = publish(store, record, content, **kwargs)
            if record == name:
                result.update(stage='postpublication', success=False, uncertain=True)
                raise c.STORE.PublicationError(result, OSError('chain sync uncertainty'))
            return result
        with mock.patch.object(c.STORE.RootDirectory, 'publish', fail):
            result = self.failure(session.mark_running)
        self.assertFalse(result['head_attempted'])
        self.assertTrue(result['publication_error']['published'])
        self.assertEqual(session.head_sha256, old)
        self.assertEqual(self.head_value()['status'], 'admitted')
        self.assertEqual((self.head.parent / 'transitions' / name).read_bytes(), data)

    def _tamper_chain_during_cas(self, *, directory):
        session = self.admit()
        old = session.head_sha256
        name, data = self.next_transition(session)
        target = self.head.parent / 'transitions'
        publish = c.STORE.RootDirectory.publish
        def tamper(store, record, content, **kwargs):
            if record == 'head.json':
                if directory:
                    target.rename(target.with_name('old-transitions'))
                    target.mkdir(mode=0o700)
                    (target / name).write_bytes(data)
                    (target / name).chmod(0o400)
                else:
                    (target / name).chmod(0o600)
                    (target / name).write_bytes(b'{}\n')
                    (target / name).chmod(0o400)
            return publish(store, record, content, **kwargs)
        with mock.patch.object(c.STORE.RootDirectory, 'publish', tamper):
            self.failure(session.mark_running)
        self.assertEqual(session.head_sha256, old)
        self.assertEqual(self.head_value()['status'], 'admitted')

    def test_chain_directory_substitution_before_head_cas_refuses(self):
        self._tamper_chain_during_cas(directory=True)

    def test_chain_record_tamper_before_head_cas_refuses(self):
        self._tamper_chain_during_cas(directory=False)

    def test_existing_chain_file_sync_failure_prevents_head_cas(self):
        session = self.admit()
        name, data = self.next_transition(session)
        chain = self.head.parent / 'transitions' / name
        chain.write_bytes(data)
        chain.chmod(0o400)
        fsync = os.fsync
        def fail(fd):
            if os.readlink('/proc/self/fd/' + str(fd)) == str(chain):
                raise OSError('chain file sync uncertainty')
            return fsync(fd)
        with mock.patch.object(c.STORE.os, 'fsync', fail):
            result = self.failure(session.mark_running)
        self.assertFalse(result['head_attempted'])
        self.assertTrue(result['publication_error']['uncertain'])
        self.assertEqual(self.head_value()['status'], 'admitted')

    def test_chain_close_failure_after_head_cas_poison_keeps_old_expected_pin(self):
        session = self.admit()
        old = session.head_sha256
        close = c.STORE.RootDirectory.close
        def fail(store):
            if (not store._closed and
                    Path(os.readlink('/proc/self/fd/' + str(store.fd))).name == 'transitions' and
                    self.head_value()['status'] == 'running'):
                close(store)
                raise OSError('chain close uncertainty')
            close(store)
        with mock.patch.object(c.STORE.RootDirectory, 'close', fail):
            result = self.failure(session.mark_running)
        self.assertTrue(result['publications'][-1]['published'])
        self.assertEqual(session.head_sha256, old)
        self.assertEqual(self.head_value()['status'], 'running')
        self.failure(session.mark_stopping, 'poisoned')

    def test_admission_record_schema_bounds_and_detachment(self):
        session = self.admit()
        data = (self.head.parent / 'runs' / (RUN + '.json')).read_bytes()
        original = c.validate_run(data)
        for changes in ({'automatic': 1}, {'registry_generation': True},
                        {'run_id': 'bad'}, {'manifest_sha256': 'e' * 64},
                        {'previous_head_sha256': 'e' * 64}, {'extra': 1}):
            value = copy.deepcopy(original)
            value.update(changes)
            with self.assertRaises(ValueError):
                c.canonical_run(value)
        with self.assertRaises(ValueError):
            c.validate_run(b' ' * 65537)
        with self.assertRaises(ValueError):
            c.validate_run(data[:-2] + b',"schema":1}\n')
        original['previous_head']['name'] = 'bad'
        self.assertEqual(c.validate_run(data)['name'], 'autotest')


# Keep inherited fixture helpers while collecting only this module's cases.
for _name in list(fixtures.ControllerTests.__dict__):
    if _name.startswith('test_') and _name not in ManagedRunTests.__dict__:
        setattr(ManagedRunTests, _name, None)
del fixtures.ControllerTests

if __name__ == '__main__':
    unittest.main()

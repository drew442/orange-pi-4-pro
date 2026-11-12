"""External qualifier contracts with fake APIs; no physical hardware proof."""
from contextlib import ExitStack
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('qualification', Path(__file__).resolve().parents[1] /
                                               'scripts/qualify-managed-boot.py')
Q = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(Q)
BOOT = '01234567-89ab-cdef-0123-456789abcdef'


class Resource:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        pass

    def recheck(self):
        pass


class Fixture:
    def __init__(self):
        self.calls = []
        self.head = dict(uid=1, gid=1, manifest_sha256='pin', status='ready', sequence=5, name='autokvm', last_boot_id=BOOT, run_id='run')
        self.run = dict(run_id='run', name='autokvm', boot_id=BOOT, automatic=True,
                        fixture_profile='managed-power-key-v1', shutdown_profile='shutdown')
        self.authentication = dict(success=True, stage='complete', publications=[],
                                   intent_sha256=Q.digest(b'record'), outcome_sha256=Q.digest(b'record'),
                                   completion_sha256=Q.digest(b'record'), registry_sha256=Q.digest(b'registry'))
        self.cleanup_fails = False
        self.census_fails = False
        fixture = self

        class Lease(Resource):
            exclusive = False
            fd = 42

        class Admission(Resource):
            bootstrap_owner = None

            @classmethod
            def open_existing(cls, *args):
                fixture.calls.append('existing')
                return cls()

            def admit(self):
                fixture.lease = Lease()
                fixture.lease.admission = self
                return fixture.lease

            def guest_lock(self, name, lease, *, create):
                assert name == 'autokvm' and create is False
                return Resource()

            def registry_lock(self, lease):
                return Resource()

            def close(self):
                fixture.calls.append('close')
                if fixture.cleanup_fails:
                    raise RuntimeError('cleanup failure')

        class Store(Resource):
            def child(self, name):
                return Store()

            def read(self, name, limit, **kwargs):
                if name == 'head.json':
                    return b'head'
                if name == 'run.json':
                    return b'run'
                return b'record'

        class Controller(Resource):
            def __init__(self, storage, lease, *, boot_id):
                self.context = NS(guard=lambda: None)

            def _registry(self, stack, guard):
                guard()
                return None, b'registry', dict(guests=[dict(fixture.head)])

            def _head_store(self, stack, name, guard):
                guard()
                return Store()

        class Policy(Resource):
            value = dict(storage={})

            def __init__(self, boot):
                pass

            def check(self):
                pass

        def authenticate(controller, name, **kwargs):
            self.calls.append('authenticate')
            assert type(controller) is Controller
            assert set(kwargs) == {'guest_lock', 'head_data'}
            assert kwargs['head_data'] == b'head'
            return self.authentication

        def census():
            self.calls.append('census')
            if self.census_fails:
                raise PermissionError('unreadable proc task fd')

        A = NS(Admission=Admission, Lease=Lease, _boot_id=lambda: BOOT, _held_lock=lambda *a: None)
        L = NS(ADMISSION=A, StateController=Controller, validate_run=lambda raw: self.run,
               canonical_run=lambda run: b'run', TRANSITION_LIMIT=256, head_transition=lambda *a: b'record')
        CP = NS(LIFECYCLE=L, authenticate_ready_cohort=authenticate,
                RECORDS=NS(INTENT_LIMIT=256, OUTCOME_LIMIT=256, FIXTURE_LIMIT=256,
                           COMPLETION_LIMIT=256, CONSOLE_LIMIT=256, validate_intent=lambda raw: dict(fixture=dict(bytes=6, sha256=Q.digest(b'record')),
                                                               console=dict(bytes=6, sha256=Q.digest(b'record')))),
                _heads=lambda *a: [])
        P = NS(CP=CP, Resources=ExitStack, installed_guard=lambda boot: None,
               BOOT_POLICY=NS(Policy=Policy), NORMAL=NS(local_kvm_gid=lambda: 1, manager_state=lambda: None),
               HEAD=NS(LIMIT=256, validate_head=lambda raw: self.head, canonical=lambda head: b'head'),
               REGISTRY=NS(SHUTDOWN_PROFILE='shutdown'), MAINTENANCE=NS(consumer_check=census),
               failure_detail=lambda error: dict(error=str(error)))
        self.unit = dict(LoadState='loaded', ActiveState='inactive', Result='success', MainPID='0',
                         Job='', ExecMainCode='1', ExecMainStatus='0')
        self.M = NS(P=P, L=L, A=A, entry_for=lambda registry, name: registry['guests'][0], D=NS(properties=lambda *a: dict(self.unit)))

    def execute(self, boot=BOOT):
        with patch.object(Q, 'load_installed', return_value=self.M), \
                patch.object(Q.os, 'getresuid', return_value=(0, 0, 0)):
            return Q.qualify(boot)


class QualificationTests(unittest.TestCase):
    def test_default_has_no_installed_import(self):
        with patch.object(Q, 'load_installed', side_effect=AssertionError('import')), \
                patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(Q.main([]), 0)
            self.assertFalse(json.loads(out.getvalue())['nvme_synchronization'])

    def test_actual_cli_default_and_interpreter_flags(self):
        import subprocess
        import sys
        command = [sys.executable, '-I', '-B', str(Path(Q.__file__))]
        planned = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(planned.stdout)['operation'], 'plan')
        refused = subprocess.run([sys.executable, '-B', str(Path(Q.__file__)), '--qualify',
                                  '--expected-boot-id', BOOT], capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('requires python3 -I -B', refused.stderr)

    def test_explicit_boot_required_before_import(self):
        with patch.object(Q, 'load_installed', side_effect=AssertionError('import')), \
                patch('sys.stderr', new_callable=io.StringIO):
            for args in (['--qualify'], ['--qualify', '--expected-boot-id', 'bad']):
                with self.assertRaises(SystemExit):
                    Q.main(args)

    def test_success_borrowed_lock_same_family_cleanup(self):
        f = Fixture()
        result = f.execute()
        self.assertTrue(result['success'], result)
        self.assertFalse(result['hardware_acceptance'])
        self.assertFalse(result['power_authorized'])
        self.assertEqual(f.calls, ['existing', 'census', 'authenticate', 'census', 'close'])
        self.assertEqual(len(result['records']), 8)

    def test_stale_nonautomatic_profile_and_initial_refuse(self):
        for field, value in [('automatic', False), ('fixture_profile', 'none'), ('boot_id', 'old')]:
            f = Fixture()
            f.run[field] = value
            self.assertFalse(f.execute()['success'])
            self.assertNotIn('authenticate', f.calls)
        f = Fixture()
        f.head['sequence'] = 0
        self.assertFalse(f.execute()['success'])
        self.assertFalse(Fixture().execute('old')['success'])

    def test_publication_or_incomplete_auth_refuses(self):
        for change in [dict(publications=[{}]), dict(success=False), dict(stage='pending'),
                       dict(close_error='failed')]:
            f = Fixture()
            f.authentication.update(change)
            self.assertFalse(f.execute()['success'])

    def test_cleanup_failure_retained(self):
        f = Fixture()
        f.cleanup_fails = True
        result = f.execute()
        self.assertFalse(result['success'])
        self.assertEqual(result['cleanup_errors'][0]['resource'], 'admission')

    def test_unit_transient_or_failed_and_census_error_refuse(self):
        for key, value in [('ActiveState', 'activating'), ('MainPID', '12'), ('Job', '1'),
                           ('ExecMainCode', '2'), ('ExecMainStatus', '1'), ('Result', 'failed')]:
            f = Fixture()
            f.unit[key] = value
            self.assertFalse(f.execute()['success'])
            self.assertNotIn('authenticate', f.calls)
        f = Fixture()
        f.census_fails = True
        self.assertFalse(f.execute()['success'])

    def test_family_mismatch_and_unsafe_source_refuse(self):
        f = Fixture()
        f.M.P.CP.LIFECYCLE = object()
        self.assertFalse(f.execute()['success'])
        with patch.object(Q.os, 'statvfs', return_value=NS(f_flag=Q.os.ST_RDONLY)), \
                patch.object(Q, 'protected', side_effect=ValueError('unsafe source')), \
                patch.object(Q.importlib.util, 'spec_from_file_location') as importer:
            with self.assertRaisesRegex(ValueError, 'unsafe source'):
                Q.load_installed()
            importer.assert_not_called()

    def test_post_auth_record_change_and_missing_completion_refuse(self):
        f = Fixture()
        f.authentication['completion_sha256'] = 'changed'
        self.assertFalse(f.execute()['success'])
        f = Fixture()
        f.M.P.CP.authenticate_ready_cohort = lambda *a, **kw: (_ for _ in ()).throw(
            FileNotFoundError('missing completed record'))
        result = f.execute()
        self.assertFalse(result['success'])
        self.assertIn('missing completed record', result['error'])
        self.assertEqual(f.calls[-1], 'close')

    def test_second_quiescence_failure_refuses(self):
        f = Fixture()
        def census():
            f.calls.append('census')
            if f.calls.count('census') == 2:
                raise PermissionError('task scan failed')
        f.M.P.MAINTENANCE.consumer_check = census
        self.assertFalse(f.execute()['success'])
        f = Fixture()
        def properties(*args):
            if 'authenticate' in f.calls:
                return dict(f.unit, Job='pending')
            return dict(f.unit)
        f.M.D.properties = properties
        self.assertFalse(f.execute()['success'])

    def test_unsafe_parents_refuse_before_open(self):
        import stat
        for mode, uid in [(stat.S_IFDIR | 0o777, 0), (stat.S_IFLNK | 0o755, 0),
                          (stat.S_IFDIR | 0o755, 1000)]:
            info = NS(st_mode=mode, st_uid=uid, st_gid=0, st_dev=1)
            with patch.object(Q.os, 'stat', return_value=NS(st_dev=1)), \
                    patch.object(Q.os, 'lstat', return_value=info), \
                    patch.object(Q.os, 'open') as opener:
                with self.assertRaisesRegex(ValueError, 'unsafe source directory'):
                    Q.protected(Q.MODULE, 64, 'hash')
                opener.assert_not_called()

    def test_timestamp_valid_malicious_pyc_refused(self):
        import py_compile
        import tempfile
        import stat
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / 'scripts'
            scripts.mkdir(mode=0o755)
            source = scripts / 'manage-guests.py'
            source.write_text("raise AssertionError('malicious bytecode executed')\n")
            py_compile.compile(str(source), doraise=True)
            # Keep the malicious bytecode timestamp/size valid for different,
            # approved source; SourceFileLoader would otherwise run the pyc.
            info = source.stat()
            safe = b'pass' + b' ' * (info.st_size - 5) + b'\n'
            source.write_bytes(safe)
            Q.os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns))
            self.assertTrue(list(scripts.glob('__pycache__/*.pyc')))
            files = {'usr/lib/a733-tools/scripts/manage-guests.py': {}}
            for number in range(84):
                name = 'file' + str(number)
                (root / name).write_bytes(b'x')
                files['usr/lib/a733-tools/' + name] = {}
            real_lstat = Q.os.lstat
            root_device = root.stat().st_dev
            def root_owned(path):
                value = real_lstat(path)
                fields = list(value)
                fields[4:6] = [0, 0]
                return Q.os.stat_result(fields)
            with patch.object(Q, 'MODULE', source), \
                    patch.object(Q.os, 'stat', return_value=NS(st_dev=root_device)), \
                    patch.object(Q.os, 'lstat', side_effect=root_owned), \
                    patch.object(Q.os, 'statvfs', return_value=NS(f_flag=Q.os.ST_RDONLY)):
                with self.assertRaisesRegex(ValueError, 'unexpected runtime entry'):
                    Q.closed_runtime(files)
                # Legacy adjacent pyc is refused as well.
                for cached in (scripts / '__pycache__').iterdir():
                    cached.unlink()
                (scripts / '__pycache__').rmdir()
                (scripts / 'manage-guests.pyc').write_bytes(b'bad')
                with self.assertRaisesRegex(ValueError, 'unexpected runtime entry'):
                    Q.closed_runtime(files)

    def test_read_write_root_refuses_before_source(self):
        with patch.object(Q.os, 'statvfs', return_value=NS(f_flag=0)), \
                patch.object(Q, 'protected') as reader:
            with self.assertRaisesRegex(ValueError, 'read-only root'):
                Q.load_installed()
            reader.assert_not_called()

    def test_registry_change_refuses(self):
        f = Fixture()
        f.authentication['registry_sha256'] = 'changed'
        self.assertFalse(f.execute()['success'])
        f = Fixture()
        f.M.entry_for = lambda *args: dict(f.head, manifest_sha256='changed')
        self.assertFalse(f.execute()['success'])

    def test_tampered_dependency_refuses_before_import(self):
        marker = dict(slot='B', maintenance_runtime=dict(files={
            'usr/lib/a733-tools/scripts/dependency.py': dict(bytes=1, sha256='bad', mode=292)}))
        with patch.object(Q.os, 'statvfs', return_value=NS(f_flag=Q.os.ST_RDONLY)), \
                patch.object(Q, 'closed_runtime'), \
                patch.object(Q, 'protected', side_effect=[json.dumps(marker).encode(),
                                                      ValueError('source bytes differ')]), \
                patch.object(Q.importlib.util, 'spec_from_file_location') as importer:
            with self.assertRaisesRegex(ValueError, 'source bytes differ'):
                Q.load_installed()
            importer.assert_not_called()


if __name__ == '__main__':
    unittest.main()

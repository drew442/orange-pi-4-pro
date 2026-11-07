"""Root launcher checks use small files and mocked containers; no hardware claim."""
import ast
import base64
import hashlib
import stat
import importlib.util
import json
import os
import signal
import sys
import time
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]

def load(relative):
    spec = importlib.util.spec_from_file_location(relative.replace('/', '_'), REPO / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

m = load('build/launch-host-roots.py')
c = load('build/launch-uboot-nvme-candidate.py')


class LauncherTests(unittest.TestCase):
    def test_closed_profiles_and_default_sizes(self):
        script = m.inner_program('a' * 40, 'test').decode()
        self.assertIn("'fakeroot','--','/usr/bin/python3','-I','-B'", script)
        self.assertIn("'fakeroot-raw-xattrs-v1'", m.payload_program('test').decode())
        self.assertIn("'timesync-20261106'", m.payload_program('test').decode())
        self.assertNotIn('--size', script)
        self.assertEqual(m.SLOT_BYTES, 4294967296)
        for tag in ('../old', 'a b', '/old', '', 'a' * 65):
            with self.assertRaises(ValueError):
                m.phase_path(tag)

    def test_plan_is_pure_and_reports_deferred_admission(self):
        with mock.patch.object(Path, 'lstat', side_effect=AssertionError('physical access')):
            result = m.plan('test', {}, {}, Path('/missing/diag'), Path('/missing/rootfs'))
        self.assertFalse(result['hardware_acceptance'])
        self.assertEqual(result['work_mode'], 'disk')
        self.assertIn('deferred', result['unpacked_size'])
        with self.assertRaises(ValueError):
            m.plan('test', {}, {}, Path('relative'), Path('/rootfs'))

    def test_resource_guards(self):
        good = {'free_bytes': 20_000_000_000, 'available_memory_bytes': 3 * 1024**3}
        m.check_admission(good, {'required_free_bytes': 19_000_000_000})
        for sample in (dict(good, free_bytes=18_000_000_000), dict(good, available_memory_bytes=2**30)):
            with self.assertRaises(ValueError):
                m.check_admission(sample, {'required_free_bytes': 19_000_000_000})
        for sample, elapsed in ((dict(good, free_bytes=2999999999), 0),
                                (dict(good, available_memory_bytes=999999999), 0), (good, 3601)):
            with self.assertRaises(ValueError):
                m.check_resources(sample, elapsed)

    def test_authenticated_inputs_and_symlink_special_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / 'source'
            source.write_bytes(b'fixture')
            expected = m.pin(b'fixture')
            (base / 'destinations').mkdir()
            destination = base / 'destinations/copy'
            m.authenticated(c, source, expected, destination=destination)
            self.assertEqual(destination.read_bytes(), b'fixture')
            self.assertEqual(destination.stat().st_mode & 0o777, 0o444)
            with self.assertRaises(ValueError):
                m.authenticated(c, source, m.pin(b'wrong'))
            link = base / 'link'
            link.symlink_to(source)
            with self.assertRaises((ValueError, OSError)):
                m.authenticated(c, link, expected)
            fifo = base / 'fifo'
            os.mkfifo(fifo)
            with self.assertRaises(ValueError):
                m.authenticated(c, fifo)
            alias = base / 'alias'
            alias.hardlink_to(source)
            with self.assertRaises(ValueError):
                m.authenticated(c, source, expected)
            with self.assertRaises(ValueError):
                m.authenticated(c, Path('/proc/version'))

    def test_source_mutation_during_copy_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'original')
            (root / 'destination').mkdir()
            actual_sha = hashlib.sha256
            class MutatingHash:
                def __init__(self):
                    self.digest = actual_sha()
                def update(self, chunk):
                    self.digest.update(chunk)
                    source.write_bytes(b'mutated!')
                def hexdigest(self):
                    return self.digest.hexdigest()
            expected = m.pin(b'original')
            with mock.patch.object(c.hashlib, 'sha256', MutatingHash):
                with self.assertRaisesRegex(ValueError, 'changed during read'):
                    m.authenticated(c, source, expected, destination=root / 'destination/copy')

    def test_source_snapshot_all_tracked_and_dirty_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.run(['git', '-C', str(root), *args], check=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            git('init')
            git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'user.name', 'Fixture')
            (root / 'one').write_text('one')
            (root / 'later-import').write_text('closure')
            git('add', '.')
            git('commit', '-m', 'Fixture')
            snapshot, contents = m.committed_sources(root)
            self.assertEqual(set(snapshot['files']), {'one', 'later-import'})
            self.assertEqual(contents['later-import'], b'closure')
            (root / 'later-import').write_text('mutated')
            with self.assertRaises(ValueError):
                m.committed_sources(root)
            git('checkout', '--', 'later-import')
            (root / 'one').unlink()
            (root / 'one').symlink_to('later-import')
            with self.assertRaises(ValueError):
                m.committed_sources(root)

    def test_existing_namespace_pattern_and_readonly_anchors(self):
        shared = types.SimpleNamespace(ROOT=Path('/frozen'), SEED=Path('/seed'))
        builder = types.SimpleNamespace(nspawn_command=lambda root, probe: [
            'nspawn', '--private-users=1048576:65536', '--private-users-ownership=chown',
            '--private-network', '--as-pid2', '--drop-capability=ALL', '--bind-ro=/mask:/sys/firmware',
            '/bin/sh', '/probe'])
        args = m.command(shared, builder, Path('/phase'))
        self.assertIn('--read-only', args)
        self.assertIn('--private-users-ownership=off', args)
        self.assertIn('--private-network', args)
        self.assertIn('--bind-ro=/phase/inputs:/a733-debs', args)
        self.assertNotIn('--private-users-ownership=chown', args)
        self.assertIn('/bin/sh /a733-probe.sh && exec', args[-1])

    def test_manifest_binding_and_size_proof_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = {'archive': m.pin(b'archive'), 'binding': {'profile': m.PROFILE},
                      'clock': {'schema': 1}, 'diagnostic': {name: m.pin(b'diag') for name in m.BOOT_FILES}}
            sources = {'rootfs.tar.gz': policy['archive'], 'userspace_profile': policy['binding'],
                       'timesync_state': policy['clock'], 'rootfs_metadata': {'profile': m.METADATA},
                       **policy['diagnostic']}
            files = {}
            for slot in m.SLOT_UUIDS:
                for suffix in ('.ext4', '.ext4.gz'):
                    name = 'root-' + slot + suffix
                    (root / name).write_bytes(b'tiny')
                    files[name] = m.pin(b'tiny')
            result = {'schema': 1, 'slot_bytes': m.SLOT_BYTES, 'slot_uuids': m.SLOT_UUIDS,
                      'sources': sources, 'files': files}
            (root / 'manifest.json').write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, 'root image size'):
                m.verify_output(c, root, policy)
            result['sources']['timesync_state'] = {'schema': 2}
            (root / 'manifest.json').write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, 'binding'):
                m.verify_output(c, root, policy)

    def test_generated_snapshot_captures_namespace_bytes_links_and_xattrs(self):
        tree = ast.parse(m.payload_program('test').decode())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'snapshot')
        namespace = {'pathlib': __import__('pathlib'), 'os': os, 'stat': stat,
                     'hashlib': hashlib, 'base64': base64}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<snapshot>', 'exec'), namespace)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a').write_bytes(b'expected')
            (root / 'b').hardlink_to(root / 'a')
            (root / 'link').symlink_to('a')
            os.setxattr(root / 'a', 'user.test', b'raw\x00value')
            snapshot = namespace['snapshot'](root)
            self.assertEqual(snapshot['.']['type'], 'dir')
            self.assertEqual(snapshot['a']['data'], m.pin(b'expected'))
            self.assertEqual(snapshot['b']['type'], 'hardlink')
            self.assertEqual(snapshot['b']['link'], 'a')
            self.assertEqual(snapshot['link']['link'], 'a')
            self.assertEqual(snapshot['a']['xattrs']['user.test'], base64.b64encode(b'raw\x00value').decode())
            self.assertEqual(snapshot, namespace['snapshot'](root))
            (root / 'a').write_bytes(b'changed')
            self.assertNotEqual(snapshot, namespace['snapshot'](root))

    def test_live_session_timeout_and_interrupt_finish_cleanup_despite_second_signal(self):
        real_shared = load('build/launch-userspace-configuration.py')
        for failure in ('timeout', 'interrupt'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                phase = root / 'phase'
                pidfile = root / 'pids.json'
                child_code = """import json,os,pathlib,signal,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
def terminate(*args):
 child.wait(timeout=5)
 sys.exit(0)
signal.signal(signal.SIGTERM,terminate)
pathlib.Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid]))
while True:time.sleep(.1)
"""
                checks = []
                injected = False
                captured = []
                def resources():
                    nonlocal injected
                    if not injected:
                        deadline = time.monotonic() + 5
                        while not pidfile.exists() and time.monotonic() < deadline:
                            time.sleep(.01)
                        self.assertTrue(pidfile.exists(), 'live child never became ready')
                        captured.extend(json.loads(pidfile.read_text()))
                        injected = True
                        if failure == 'interrupt':
                            os.kill(os.getpid(), signal.SIGINT)
                        raise ValueError('runtime resource floor or timeout exceeded')
                    return {'free_bytes': 20_000_000_000, 'available_memory_bytes': 3 * 1024**3}
                def stop(proc):
                    # A second, different handled signal arrives during cleanup.
                    os.kill(os.getpid(), signal.SIGTERM)
                    real_shared.stop_process(proc)
                    checks.append('stopped')
                def atomic(path, value):
                    if path.name == 'receipt.json':
                        os.kill(os.getpid(), signal.SIGHUP)
                    path.write_text(json.dumps(value))
                shared = types.SimpleNamespace(
                    safe_directory=lambda *a, **k: None, UID_BASE=os.getuid(),
                    interrupted=real_shared.interrupted, stop_process=stop,
                    mapped_processes=lambda: checks.append('processes') or [],
                    phase_mounts=lambda p: checks.append('mounts') or [],
                    INVENTORY=['inventory'], resource_sample=resources, atomic_json=atomic)
                def stage(*args):
                    (phase / 'work/partial').write_bytes(b'retain live failure')
                    return {'fixture': True}
                originals = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
                original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
                with mock.patch.object(m, 'stage_inputs', side_effect=stage), \
                     mock.patch.object(m, 'command', return_value=[sys.executable, '-c', child_code, str(pidfile)]), \
                     mock.patch.object(m.os, 'chown'), \
                     mock.patch.object(m.subprocess, 'check_output', return_value=b'package\n' * 842):
                    with self.assertRaisesRegex((ValueError, RuntimeError), 'timeout|interrupted'):
                        m.execute(shared, c, None, root, phase, {}, {}, b'package\n' * 842,
                                  {}, Path('/rootfs'), Path('/diagnostic'), 'test')
                self.assertEqual(checks, ['stopped', 'processes', 'mounts'])
                self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), original_mask)
                self.assertEqual({sig: signal.getsignal(sig) for sig in originals}, originals)
                for pid in captured:
                    self.assertFalse(Path('/proc', str(pid)).exists(), 'child or grandchild survives cleanup')
                self.assertEqual((phase / 'work/partial').read_bytes(), b'retain live failure')
                receipt = json.loads((phase / 'receipt.json').read_text())
                self.assertEqual(receipt['state'], 'failed')
                self.assertTrue(receipt['host_inventory_unchanged'])
                self.assertEqual(receipt['mapped_processes_final'], [])
                with self.assertRaises(FileExistsError):
                    m.execute(shared, c, None, root, phase, {}, {}, b'', {}, Path('/r'), Path('/d'), 'test')

    def test_successful_child_with_mismatched_output_proof_is_failed_and_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            phase = root / 'phase'
            shared = types.SimpleNamespace(
                safe_directory=lambda *a, **k: None, UID_BASE=os.getuid(),
                interrupted=lambda *a: None, stop_process=mock.Mock(), mapped_processes=lambda: [],
                phase_mounts=lambda p: [], INVENTORY=['inventory'],
                resource_sample=lambda: {'free_bytes': 20_000_000_000, 'available_memory_bytes': 3 * 1024**3},
                atomic_json=lambda p, v: p.write_text(json.dumps(v)))
            builder = types.SimpleNamespace(validate_probe=lambda log: {'fixture_probe': True})
            def stage(*args):
                (phase / 'work/partial').write_bytes(b'payload finished')
                return {}
            with mock.patch.object(m, 'stage_inputs', side_effect=stage), \
                 mock.patch.object(m, 'command', return_value=[sys.executable, '-c', 'print("probe")']), \
                 mock.patch.object(m, 'verify_output', side_effect=ValueError('profile binding differs')), \
                 mock.patch.object(m.os, 'chown'), \
                 mock.patch.object(m.subprocess, 'check_output', return_value=b'package\n' * 842):
                with self.assertRaisesRegex(ValueError, 'profile binding'):
                    m.execute(shared, c, builder, root, phase, {}, {}, b'package\n' * 842,
                              {}, Path('/rootfs'), Path('/diagnostic'), 'test')
            receipt = json.loads((phase / 'receipt.json').read_text())
            self.assertEqual(receipt['returncode'], 0)
            self.assertEqual(receipt['state'], 'failed')
            self.assertFalse(receipt['success'])
            self.assertEqual((phase / 'work/partial').read_bytes(), b'payload finished')

    def test_tampered_expectation_and_stability_receipts_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for slot in m.SLOT_UUIDS:
                (root / ('slot-' + slot + '.expected.json')).write_text(json.dumps(
                    {'schema': 1, 'slot': slot, 'members': {'.': {'type': 'dir'}}}))
                (root / ('slot-' + slot + '.stable.json')).write_text(json.dumps(
                    {'schema': 1, 'slot': slot, 'creator_input_stable': True}))
            self.assertEqual(set(m.verify_expectations(c, root)), {'A', 'B'})
            (root / 'slot-A.stable.json').write_text(json.dumps(
                {'schema': 1, 'slot': 'A', 'creator_input_stable': False}))
            with self.assertRaisesRegex(ValueError, 'stability proof'):
                m.verify_expectations(c, root)
            (root / 'slot-A.stable.json').write_text(json.dumps(
                {'schema': 1, 'slot': 'A', 'creator_input_stable': True}))
            (root / 'slot-B.expected.json').write_text(json.dumps(
                {'schema': 1, 'slot': 'A', 'members': {'.': {'type': 'dir'}}}))
            with self.assertRaisesRegex(ValueError, 'expected tree'):
                m.verify_expectations(c, root)

    def test_failed_payload_retained_and_journaled_no_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            phase = Path(directory) / 'host-roots-test'
            shared = types.SimpleNamespace(
                safe_directory=lambda *a, **k: None, UID_BASE=os.getuid(),
                interrupted=lambda *a: None, stop_process=mock.Mock(), mapped_processes=lambda: [],
                phase_mounts=lambda p: [], INVENTORY=['inventory'],
                resource_sample=lambda: {'free_bytes': 20_000_000_000, 'available_memory_bytes': 3 * 1024**3},
                atomic_json=lambda p, v: p.write_text(json.dumps(v)))
            report = {}
            def failed_stage(*args):
                (phase / 'work' / 'partial').write_bytes(b'retain')
                raise ValueError('staging fixture failed')
            with mock.patch.object(m, 'stage_inputs', side_effect=failed_stage), \
                 mock.patch.object(m.os, 'chown'), \
                 mock.patch.object(m.subprocess, 'check_output', return_value=b'package\n' * 842):
                with self.assertRaisesRegex(ValueError, 'staging fixture failed'):
                    m.execute(shared, c, None, Path(directory), phase, {}, {}, b'package\n' * 842,
                              report, Path('/rootfs'), Path('/diagnostic'), 'test')
            self.assertEqual((phase / 'work/partial').read_bytes(), b'retain')
            receipt = json.loads((phase / 'receipt.json').read_text())
            self.assertEqual(receipt['state'], 'failed')
            self.assertEqual(receipt['mapped_processes_final'], [])
            self.assertTrue(receipt['host_inventory_unchanged'])
            shared.stop_process.assert_called_once_with(None)
            with self.assertRaises(FileExistsError):
                m.execute(shared, c, None, Path(directory), phase, {}, {}, b'', {}, Path('/r'), Path('/d'), 'test')


if __name__ == '__main__':
    unittest.main()

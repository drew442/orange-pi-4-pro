"""Offline archive acquisition guards; no builder or network access."""
import copy
import importlib.util
import json
import os
import signal
import time
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('builder_packages', ROOT / 'build/fetch-builder-packages.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class BuilderPackagesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        self.output = self.repo / 'out/builder-packages-test'
        self.output.mkdir(parents=True, mode=0o700)
        self.path = 'pool/main/a/acl/acl_1_amd64.deb'
        self.data = b'archive fixture'
        self.record = {'bytes': len(self.data), 'sha256': m.digest(self.data),
                       'package': 'acl', 'version': '1', 'architecture': 'amd64',
                       'origins': m.ORIGINS + [m.SNAPSHOT]}
        self.records = {self.path: self.record}

    def install_inputs(self, lock=None):
        lock = lock or json.loads((ROOT / m.LOCK).read_text())
        for name in (m.LOCK, m.INVENTORY):
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / m.LOCK).write_text(json.dumps(lock))
        (self.repo / m.INVENTORY).write_bytes((ROOT / m.INVENTORY).read_bytes())
        evidence = self.repo / m.EVIDENCE
        evidence.parent.mkdir(parents=True)
        evidence.write_text(json.dumps({'schema': 1, 'archive_pin_lock_sha256':
            m.digest((self.repo / m.LOCK).read_bytes()), 'verified_live_index_packages': 841,
            'snapshot_package': 'xfsprogs'}))

    def test_real_lock_coverage_and_snapshot(self):
        self.install_inputs()
        records = m.inputs(self.repo)
        self.assertEqual(len(records), 842)
        self.assertEqual(sum(r['bytes'] for r in records.values()), 983768778)
        xfs = next(r for r in records.values() if r['package'] == 'xfsprogs')
        self.assertEqual(xfs['origins'], [m.XFS_ORIGIN])

    def test_lock_injection_and_duplicates_rejected(self):
        original = json.loads((ROOT / m.LOCK).read_text())
        for bad in ('../escape.deb', 'pool/main/a/acl/evil.deb\n  dir=/tmp',
                    'pool/main/a/acl/..', 'pool/main/a/acl/a%0a.deb'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as temporary:
                self.repo = Path(temporary)
                lock = copy.deepcopy(original)
                lock['packages'][0]['filename'] = bad
                self.install_inputs(lock)
                with self.assertRaises(ValueError):
                    m.inputs(self.repo)
        with tempfile.TemporaryDirectory() as temporary:
            self.repo = Path(temporary)
            original['packages'][1] = original['packages'][0]
            self.install_inputs(original)
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                m.inputs(self.repo)

    def archive(self, data):
        destination = self.output / self.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return destination

    def test_completed_reuse_and_corruption_refusal(self):
        destination = self.archive(self.data)
        self.assertEqual(m.prepare(self.output, self.records, True), [])
        for data in (self.data[:-1], b'x' * len(self.data)):
            destination.write_bytes(data)
            with self.assertRaises(ValueError):
                m.prepare(self.output, self.records, True)

    def test_partial_requires_bound_resume_and_control(self):
        destination = self.archive(self.data[:3])
        with self.assertRaises(ValueError):
            m.prepare(self.output, self.records, True)
        control = Path(str(destination) + '.aria2')
        control.write_bytes(b'opaque aria control')
        self.assertEqual(m.prepare(self.output, self.records, True), [self.path])
        with self.assertRaisesRegex(ValueError, 'unbound'):
            m.prepare(self.output, self.records, False)
        destination.unlink()
        with self.assertRaisesRegex(ValueError, 'orphan'):
            m.prepare(self.output, self.records, True)

    def test_foreign_file_symlink_and_hardlink_refused(self):
        extra = self.output / 'unrelated'
        extra.write_text('preserve')
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            m.prepare(self.output, self.records, True)
        extra.unlink()
        destination = self.archive(self.data)
        destination.unlink()
        destination.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            m.prepare(self.output, self.records, True)
        destination.unlink()
        extra = self.repo / 'foreign'
        extra.write_bytes(self.data)
        os.link(extra, destination)
        with self.assertRaises(ValueError):
            m.prepare(self.output, self.records, True)

    def test_aria_input_and_command_boundaries(self):
        value = m.aria_input(self.output, [self.path], self.records)
        self.assertIn('  out=acl_1_amd64.deb\n', value)
        self.assertIn('checksum=sha-256=' + self.record['sha256'], value)
        self.assertEqual(value.count('https://'), 3)
        command = m.aria_command(self.output)
        for flag in ('--no-conf=true', '--max-concurrent-downloads=2', '--split=1',
                     '--max-connection-per-server=1', '--check-certificate=true',
                     '--follow-torrent=false', '--follow-metalink=false', '--auto-file-renaming=false',
                     '--allow-overwrite=false'):
            self.assertIn(flag, command)
        self.assertFalse(any(k.lower().endswith('proxy') for k in m.ENV))

    @unittest.skipUnless(Path('/usr/bin/aria2c').is_file(), 'native aria2 binary unavailable')
    def test_real_aria2_option_parser_offline(self):
        version = subprocess.check_output(['/usr/bin/aria2c', '--no-conf=true', '--version'], env=m.ENV)
        if not version.startswith(b'aria2 version 1.36.0\n'):
            self.skipTest('native pinned aria2 1.36.0 unavailable')
        command = m.aria_command(self.output)
        result = subprocess.run(command + ['--help=#basic'], env=m.ENV, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        unknown = subprocess.run(command + ['--a733-unknown-option=true', '--help=#basic'],
                                 env=m.ENV, capture_output=True)
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn(b'unrecognized option', unknown.stderr)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_clean_git_binding_and_changed_head(self):
        def git(*args):
            subprocess.run(['/usr/bin/git', '-C', str(self.repo), *args],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        git('init')
        for filename in m.FILES:
            path = self.repo / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture')
        git('add', '.')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'Initial')
        binding = m.binding(self.repo)
        (self.repo / m.RECIPE).write_text('changed')
        with self.assertRaisesRegex(ValueError, 'clean'):
            m.binding(self.repo)
        git('add', '.')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'Change')
        self.assertNotEqual(m.binding(self.repo), binding)

    def mock_fetch(self, resume, **kwargs):
        with patch.object(m, 'binding', return_value={'head': 'fixture', 'files': {}}), \
             patch.object(m, 'inputs', return_value=self.records), \
             patch.object(m.helper, 'check_builder', return_value={'inventory': 'exact'}), \
             patch.object(m, 'run', return_value=b'aria2 version 1.36.0\n'), \
             patch.object(m, 'download_command', **kwargs):
            return m.fetch(self.repo, self.output, resume)

    def test_resume_binding_and_simultaneous_writer_refusal(self):
        (self.output / 'fetch-state.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'binding'):
            self.mock_fetch(True)
        (self.output / 'fetch-state.json').write_text(json.dumps({'head': 'fixture', 'files': {}}))
        with (self.output / '.fetch.lock').open('w') as guard:
            m.fcntl.flock(guard, m.fcntl.LOCK_EX | m.fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.mock_fetch(True)

    def test_failure_report_without_receipt_and_after_inventory(self):
        (self.output / 'fetch-state.json').write_text(json.dumps({'head': 'fixture', 'files': {}}))
        with self.assertRaisesRegex(ValueError, 'verification'):
            self.mock_fetch(True, return_value=7)
        report = json.loads((self.output / 'fetch-report.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['aria2_exit'], 7)
        self.assertEqual(report['builder_after'], {'inventory': 'exact'})
        self.assertFalse((self.output / 'receipt.json').exists())
        self.assertEqual(report['archives'][0]['status'], 'failed')

    def test_option_smoke_failure_prevents_any_transfer(self):
        (self.output / 'fetch-state.json').write_text(json.dumps({'head': 'fixture', 'files': {}}))
        failure = subprocess.CalledProcessError(28, ['aria2c', '--unsupported=true'])
        with patch.object(m, 'binding', return_value={'head': 'fixture', 'files': {}}), \
             patch.object(m, 'inputs', return_value=self.records), \
             patch.object(m.helper, 'check_builder', return_value={'inventory': 'exact'}), \
             patch.object(m, 'run', side_effect=[b'aria2 version 1.36.0\n', failure]), \
             patch.object(m, 'download_command') as download:
            with self.assertRaises(ValueError):
                m.fetch(self.repo, self.output, True)
            download.assert_not_called()
        report = json.loads((self.output / 'fetch-report.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertIsNone(report['aria2_exit'])
        self.assertEqual(report['aria2_option_smoke']['exit'], 28)
        self.assertFalse((self.output / 'receipt.json').exists())

    def test_old_receipt_removed_on_corrupt_cache(self):
        self.archive(b'bad')
        (self.output / 'fetch-state.json').write_text(json.dumps({'head': 'fixture', 'files': {}}))
        (self.output / 'receipt.json').write_text('{"status": "complete"}')
        with self.assertRaisesRegex(ValueError, 'length'):
            self.mock_fetch(True, side_effect=AssertionError('must refuse before aria2'))
        self.assertFalse((self.output / 'receipt.json').exists())
        report = json.loads((self.output / 'fetch-report.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(len(report['archives']), 1)

    def test_output_injection_refused_before_subprocess(self):
        for name in ('builder-packages-inject\noption', 'builder-packages-has space'):
            with self.subTest(name=name), patch.object(m, 'binding') as binding:
                with self.assertRaises(ValueError):
                    m.fetch(self.repo, self.output.parent / name)
                binding.assert_not_called()

    def test_interrupt_retains_failure_report_and_after_inventory(self):
        (self.output / 'fetch-state.json').write_text(json.dumps({'head': 'fixture', 'files': {}}))
        for error in (KeyboardInterrupt(), InterruptedError('TERM')):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error) if isinstance(error, KeyboardInterrupt) else ValueError):
                    self.mock_fetch(True, side_effect=error)
                report = json.loads((self.output / 'fetch-report.json').read_text())
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['builder_after'], {'inventory': 'exact'})
                self.assertFalse((self.output / 'receipt.json').exists())

    def test_term_ignoring_descendant_killed_after_leader_exits(self):
        # The real leader exits on TERM; its child ignores TERM. No network/filesystem writes.
        source = ("import os, signal, time; child=os.fork(); "
                  "signal.signal(signal.SIGTERM, signal.SIG_IGN) if child == 0 else None; "
                  "print(os.getpid(), flush=True) if child == 0 else None; time.sleep(30)")
        logfile = self.repo / 'process.log'
        report = {}
        real_wait = subprocess.Popen.wait
        leader = []
        def interrupt_wait(process, *args, **kwargs):
            if not leader:
                leader.append(process.pid)
                deadline = time.monotonic() + 5
                while not logfile.read_text().strip() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(logfile.read_text().strip(), 'child never became ready')
                os.kill(os.getpid(), signal.SIGTERM)
            return real_wait(process, *args, **kwargs)
        with logfile.open('wb') as log, m.interruption_signals(), \
             patch.object(subprocess.Popen, 'wait', interrupt_wait):
            with self.assertRaises(InterruptedError):
                m.download_command(['/usr/bin/python3', '-c', source], log, report)
        self.assertEqual(report['aria2_exit'], -signal.SIGTERM)
        child = int(logfile.read_text().strip())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = Path('/proc') / str(child) / 'stat'
            if not status.exists() or status.read_text().split()[2] == 'Z':
                break
            time.sleep(0.01)
        else:
            os.kill(child, signal.SIGKILL)
            self.fail('TERM-ignoring child survived process group cleanup')

    def test_success_rehash_and_receipt(self):
        self.archive(self.data)
        (self.output / 'fetch-state.json').write_text(json.dumps({'head': 'fixture', 'files': {}}))
        report = self.mock_fetch(True, side_effect=AssertionError('must reuse verified cache'))
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(json.loads((self.output / 'receipt.json').read_text()), report)


if __name__ == '__main__':
    unittest.main()

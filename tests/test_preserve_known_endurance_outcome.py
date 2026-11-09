"""Ordinary-file and mocked-process checks; these do not validate hardware."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/preserve-known-endurance-outcome.py'
spec = importlib.util.spec_from_file_location('preserve_known_outcome', SCRIPT)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def captured(data=b'', returncode=0):
    return {'stdout_b64': base64.b64encode(data).decode(), 'stderr_b64': '',
            'returncode': returncode, 'timed_out': False, 'truncated': False}


class OutcomeTests(unittest.TestCase):
    def test_real_subprocess_failure_and_missing_executable(self):
        result = helper.bounded_command([sys.executable, '-c',
                                         'import sys; print("failure"); sys.exit(7)'], 1024)
        self.assertEqual(result['returncode'], 7)
        self.assertEqual(base64.b64decode(result['stdout_b64']), b'failure\n')
        missing = helper.bounded_command(['/definitely/not/a/program'], 1024)
        self.assertIn('error', missing)
        self.assertIsNone(missing['returncode'])

    def test_subprocess_both_stream_bounds_and_timeout(self):
        for stream in ('stdout', 'stderr'):
            with self.subTest(stream=stream):
                result = helper.bounded_command([sys.executable, '-c',
                    'import sys; sys.' + stream + '.buffer.write(b"x"*200000)'], 1000)
                self.assertTrue(result['truncated'])
                maximum = 1000 if stream == 'stdout' else 65536
                self.assertEqual(len(base64.b64decode(result[stream + '_b64'])), maximum)
        result = helper.bounded_command([sys.executable, '-c',
                                         'import time; time.sleep(10)'], 100, timeout=.05)
        self.assertTrue(result['timed_out'])
        self.assertLess(result['returncode'], 0)

    def test_source_pipe_is_written_without_unbounded_capture(self):
        result = helper.bounded_command([sys.executable, '-c',
            'import sys; print(len(sys.stdin.buffer.read()))'], 100,
            source=b'a' * 100000)
        self.assertEqual(base64.b64decode(result['stdout_b64']), b'100000\n')

    def test_file_tail_binary_missing_symlink_and_fifo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'abc\x00\xff')
            record = helper.file_tail(source, 2)
            self.assertEqual(base64.b64decode(record['data_b64']), b'\x00\xff')
            self.assertEqual(record['offset'], 3)
            self.assertTrue(record['truncated'])
            link = root / 'link'
            link.symlink_to(source)
            fifo = root / 'fifo'
            os.mkfifo(fifo)
            for path in (root / 'missing', link, fifo):
                self.assertIn('error', helper.file_tail(path, 100))

    def test_remote_mismatch_never_blessed_and_guest_limit_visible(self):
        commands = []
        def command(argv, limit, **kwargs):
            commands.append(argv)
            data = b'ENDURANCE_PASS\n' * 10000 if '--unit=' + helper.UNITS[0] in argv else b''
            return captured(data)
        def tail(path, limit):
            return {'data_b64': base64.b64encode(b'other-boot').decode()}
        with mock.patch.object(helper, 'bounded_command', side_effect=command), \
                mock.patch.object(helper, 'file_tail', side_effect=tail):
            result = helper.remote_snapshot(True)
        self.assertFalse(result['boot_matches'])
        self.assertFalse(result['hardware_acceptance'])
        self.assertEqual(result['classification'], 'unreviewed')
        self.assertTrue(result['guest_journal']['possible_truncation'])
        journals = [argv for argv in commands if argv[0] == 'journalctl']
        self.assertEqual(len(journals), 3)
        # journalctl parses hyphens as boot-offset delimiters on the target;
        # bind its compact 128-bit descriptor independently of the formatter.
        self.assertTrue(all('--boot=dd8d6f67d2974f3bbc9a8a41c9742051' in argv
                            for argv in journals))
        self.assertIn(['nvme', 'error-log', '/dev/nvme0', '-e', '1',
                       '--output-format=json'], commands)
        self.assertTrue(all(argv[0] in ('systemctl', 'journalctl', 'dmesg', 'nvme')
                            for argv in commands))
        self.assertEqual(commands[0][1], 'show')

    def test_health_commands_skipped_without_collector_exit_evidence(self):
        with mock.patch.object(helper, 'bounded_command', return_value=captured()) as command, \
                mock.patch.object(helper, 'file_tail', return_value={'error': 'missing'}):
            result = helper.remote_snapshot()
        self.assertIn('nvme_health_skipped', result)
        self.assertFalse(any(call.args[0][0] == 'nvme' for call in command.call_args_list))

    def test_all_journal_record_caps_and_byte_truncation_are_explicit(self):
        for count, truncated in ((0, False), (2000, False), (10000, False),
                                 (1, True)):
            with self.subTest(count=count, truncated=truncated):
                sample = captured(b'{}\n' * count)
                sample['truncated'] = truncated
                with mock.patch.object(helper, 'bounded_command', side_effect=lambda *args, **kwargs: dict(sample)), \
                        mock.patch.object(helper, 'file_tail', return_value={'error': 'missing'}):
                    result = helper.remote_snapshot()
                for name, cap in (('guest_journal', 10000), ('kernel_journal', 2000),
                                  ('manager_journal', 2000)):
                    journal = result[name]
                    self.assertEqual(journal['record_limit'], cap)
                    self.assertEqual(journal['records_captured'], count)
                    self.assertEqual(journal['possible_truncation'],
                                     truncated or count >= cap)

    def test_phase_cli_closed_default_and_known_final(self):
        with mock.patch.object(helper, 'preserve', return_value=0) as preserve:
            self.assertEqual(helper.main([]), 0)
            preserve.assert_called_once_with('collector-exit')
            preserve.reset_mock()
            self.assertEqual(helper.main(['--phase', 'scheduled-final']), 0)
            preserve.assert_called_once_with('scheduled-final')
            preserve.reset_mock()
            for args in (['--phase', 'arbitrary'], ['--output', '/tmp/arbitrary'],
                         ['--command', 'arbitrary']):
                with self.subTest(args=args), mock.patch('sys.stderr'), self.assertRaises(SystemExit):
                    helper.main(args)
            preserve.assert_not_called()
        self.assertEqual(helper.OUTPUT, helper.REPO / 'out/endurance-outcome-20261109-01')
        self.assertEqual(helper.FINAL_OUTPUT, helper.REPO / 'out/endurance-outcome-20261109-final')

    def test_phase_directories_independent_and_missing_collector_unproven(self):
        for load_state in ('loaded', 'not-found', ''):
            with self.subTest(load_state=load_state), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                first, final = root / 'first', root / 'final'
                props = ('LoadState=' + load_state +
                         '\nActiveState=inactive\nMainPID=0\nExecMainStatus=0\n').encode()
                def command(argv, *args, **kwargs):
                    return captured(props if argv[0] == 'systemctl' else b'ENDURANCE_PASS')
                with mock.patch.object(helper, 'OUTPUT', first), \
                        mock.patch.object(helper, 'FINAL_OUTPUT', final), \
                        mock.patch.object(helper, 'UART', root / 'missing-uart'), \
                        mock.patch.object(helper.shutil, 'disk_usage',
                                          return_value=types.SimpleNamespace(free=300 * helper.MIB)), \
                        mock.patch.object(helper, 'remote_source', return_value=b'fixed') as source, \
                        mock.patch.object(helper, 'bounded_command', side_effect=command):
                    for phase, target in (('collector-exit', first), ('scheduled-final', final)):
                        self.assertEqual(helper.preserve(phase), 0)
                        summary = json.loads((target / 'summary.json').read_bytes())
                        self.assertEqual(summary['phase'], phase)
                        self.assertEqual(summary['collector_exit_meaning'], 'unknown_or_other')
                        self.assertFalse(summary['hardware_acceptance'])
                        self.assertEqual(summary['classification'], 'unreviewed')
                        self.assertEqual(summary['nvme_health_allowed_after_collector_exit'],
                                         load_state == 'loaded')
                        source.assert_called_with(load_state == 'loaded')
                        with self.assertRaises(FileExistsError):
                            helper.preserve(phase)
                    self.assertTrue(first.is_dir() and final.is_dir())

    def test_remote_source_is_standalone_without_execution(self):
        source = helper.remote_source()
        compile(source, '<remote>', 'exec')
        self.assertIn(helper.BOOT.encode(), source)
        self.assertNotIn(b'/home/drew/.ssh', source)

    def test_fixed_targets_and_no_cli_overrides(self):
        argv = helper.ssh_argv()
        self.assertEqual(argv[-2:], ['root@192.168.1.116', 'python3 -I -B -'])
        for option in ('IdentitiesOnly=yes', 'BatchMode=yes', 'StrictHostKeyChecking=yes',
                       'HostKeyAlias=192.168.1.116', 'ConnectTimeout=5'):
            self.assertIn(option, argv)
        with mock.patch.object(helper, 'preserve') as preserve:
            with mock.patch('sys.stderr'), self.assertRaises(SystemExit):
                helper.main(['--host', 'elsewhere'])
            preserve.assert_not_called()

    def test_private_fresh_artifacts_and_transport_errors_preserved(self):
        for exit_code, meaning in ((1, 'failure'), (2, 'pending_external_completion')):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / 'outcome'
                uart = root / 'uart'
                uart.write_bytes(b'console')
                outputs = [captured(('ExecMainStatus=%d\n' % exit_code).encode()),
                           captured(b'', 255)]
                with mock.patch.object(helper, 'OUTPUT', target), \
                        mock.patch.object(helper, 'UART', uart), \
                        mock.patch.object(helper.shutil, 'disk_usage',
                                          return_value=types.SimpleNamespace(free=300 * helper.MIB)), \
                        mock.patch.object(helper, 'remote_source', return_value=b'fixed test source'), \
                        mock.patch.object(helper, 'bounded_command', side_effect=outputs):
                    self.assertEqual(helper.preserve(), 0)
                    summary = json.loads((target / 'summary.json').read_bytes())
                    self.assertFalse(summary['hardware_acceptance'])
                    self.assertEqual(summary['classification'], 'unreviewed')
                    self.assertEqual(summary['collector_exit_meaning'], meaning)
                    self.assertEqual(summary['ssh_returncode'], 255)
                    self.assertEqual(target.stat().st_mode & 0o777, 0o700)
                    self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600
                                        for path in target.iterdir()))
                    self.assertLess(sum(p.stat().st_size for p in target.iterdir()), helper.TOTAL_LIMIT)
                    before = (target / 'summary.json').read_bytes()
                    with self.assertRaises(FileExistsError):
                        helper.preserve()
                    self.assertEqual((target / 'summary.json').read_bytes(), before)

    def test_low_space_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'outcome'
            with mock.patch.object(helper, 'OUTPUT', target), \
                    mock.patch.object(helper.shutil, 'disk_usage',
                                      return_value=types.SimpleNamespace(free=256 * helper.MIB)):
                with self.assertRaises(ValueError):
                    helper.preserve()
                self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()

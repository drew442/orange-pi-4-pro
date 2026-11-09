"""Software checks only; no VM, storage device or hardware acceptance."""
import gzip
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('endurance_guest', REPO / 'build/endurance-guest.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def archive():
    return b''.join(M.record(name, mode, data) for name, mode, data in (
        ('.', stat.S_IFDIR | 0o755, b''),
        ('etc', stat.S_IFDIR | 0o755, b''),
        ('init', stat.S_IFREG | 0o755, b'old init'),
        ('etc/keep', stat.S_IFREG | 0o644, b'unchanged'),
        ('TRAILER!!!', 0, b'')))


class ArchiveTests(unittest.TestCase):
    def test_preserves_every_original_record_except_init(self):
        raw = archive()
        additions = {'init': (stat.S_IFREG | 0o755, b'new init'),
                     'etc/endurance.conf': (stat.S_IFREG | 0o644, b'MODE=smoke\n')}
        updated, _ = M.repack(raw, additions)
        before = {e['name']: raw[e['start']:e['end']] for e in M.HELPER.entries(raw)}
        after = {e['name']: updated[e['start']:e['end']] for e in M.HELPER.entries(updated)}
        self.assertEqual(set(after) - set(before), {'etc/endurance.conf'})
        for name in set(before) - {'init'}:
            self.assertEqual(before[name], after[name])

    def test_rejects_other_replacement_and_traversal(self):
        with self.assertRaisesRegex(ValueError, 'only init'):
            M.repack(archive(), {'init': (stat.S_IFREG | 0o755, b''),
                                 'etc/keep': (stat.S_IFREG | 0o644, b'overwrite')})
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            M.repack(archive(), {'init': (stat.S_IFREG | 0o755, b''),
                                 '../escape': (stat.S_IFREG | 0o644, b'')})

    def test_rejects_truncated_duplicate_trailing_and_oversize(self):
        for raw in (archive()[:-1], archive() + b'x',
                    M.record('init', stat.S_IFREG | 0o755, b'') + archive()):
            with self.assertRaises(ValueError):
                M.HELPER.entries(raw)
        with mock.patch.object(M.HELPER, 'MAX_RAW', 20):
            with self.assertRaisesRegex(ValueError, 'budget'):
                M.HELPER.entries(archive())

    def test_source_pin_precedes_decompression_and_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'bad.gz'
            source.write_bytes(b'not gzip')
            with mock.patch.object(M.gzip, 'GzipFile', side_effect=AssertionError('decompressed untrusted input')):
                with self.assertRaises(ValueError):
                    M.build(source, REPO / 'out/endurance-guest-never-created')

    def test_deterministic_packaging_manifest_and_private_output(self):
        # Synthetic authenticated source exercises packaging; this is no boot proof.
        raw, _ = M.repack(archive(), {'init': (stat.S_IFREG | 0o755,
            (REPO / 'configs/endurance-guest-init').read_bytes())})
        encoded = gzip.compress(raw, mtime=0)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.gz'
            source.write_bytes(encoded)
            outputs = []
            for _ in range(2):
                path = Path(tempfile.mkdtemp(prefix='endurance-guest-test-', dir=REPO / 'out'))
                path.rmdir()
                self.addCleanup(shutil.rmtree, path, True)
                outputs.append(path)
            with mock.patch.object(M, 'BASE', M.HOST.record(encoded)):
                manifests = [M.build(source, output, 60) for output in outputs]
            self.assertEqual(manifests[0], manifests[1])
            self.assertEqual((outputs[0] / 'initramfs.cpio.gz').read_bytes(),
                             (outputs[1] / 'initramfs.cpio.gz').read_bytes())
            self.assertEqual(manifests[0]['mode'], 'smoke')
            self.assertEqual(manifests[0]['required_seconds'], 60)
            self.assertIs(manifests[0]['hardware_acceptance'], False)
            self.assertEqual(outputs[0].stat().st_mode & 0o777, 0o700)
            self.assertEqual((outputs[0] / 'initramfs.cpio.gz').read_bytes()[4:8], bytes(4))
            entries = M.HELPER.entries(gzip.decompress((outputs[0] / 'initramfs.cpio.gz').read_bytes()))
            self.assertIn(b'MODE=smoke', next(e['data'] for e in entries if e['name'] == 'etc/endurance.conf'))

    def test_duration_modes(self):
        self.assertEqual(M.duration_mode(), (28800, 'release'))
        for seconds in (30, 60, 300):
            self.assertEqual(M.duration_mode(seconds), (seconds, 'smoke'))
        for seconds in (0, 29, 301, 28800, 86400, True, '60'):
            with self.assertRaises(ValueError):
                M.duration_mode(seconds)

    def test_release_manifest_and_embedded_config_agree(self):
        raw, _ = M.repack(archive(), {'init': (stat.S_IFREG | 0o755,
            (REPO / 'configs/endurance-guest-init').read_bytes())})
        encoded = gzip.compress(raw, mtime=0)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.gz'
            source.write_bytes(encoded)
            output = Path(tempfile.mkdtemp(prefix='endurance-guest-test-', dir=REPO / 'out'))
            output.rmdir()
            self.addCleanup(shutil.rmtree, output, True)
            with mock.patch.object(M, 'BASE', M.HOST.record(encoded)):
                manifest = M.build(source, output)
            entries = M.HELPER.entries(gzip.decompress((output / 'initramfs.cpio.gz').read_bytes()))
            config = next(e['data'] for e in entries if e['name'] == 'etc/endurance.conf')
            self.assertEqual(manifest['mode'], 'release')
            self.assertEqual(manifest['required_seconds'], 28800)
            self.assertTrue(config.startswith(b'DURATION=28800\nMODE=release\n'))
            self.assertFalse(manifest['hardware_acceptance'])


class GuestSafetyTests(unittest.TestCase):
    def setUp(self):
        self.init = (REPO / 'configs/endurance-guest-init').read_text()

    def test_guest_configuration_accepts_only_current_release_or_bounded_smoke(self):
        # Execute the real configuration guard without any boot or device code.
        guard = self.init.split('case "$DURATION:$MODE"', 1)[1].split('\nrun_id=', 1)[0]
        guard = 'case "$DURATION:$MODE"' + guard
        for mode, seconds, accepted in [('release', 28800, True),
                                        ('release', 28799, False),
                                        ('release', 28801, False),
                                        ('release', 86400, False),
                                        ('release', 60, False),
                                        ('smoke', 30, True), ('smoke', 300, True),
                                        ('smoke', 29, False), ('smoke', 301, False),
                                        ('smoke', 28800, False), ('other', 28800, False)]:
            with self.subTest(mode=mode, seconds=seconds):
                result = subprocess.run(['sh', '-c', 'fail() { exit 42; };\n' + guard],
                                        env={'MODE': mode, 'DURATION': str(seconds)},
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0 if accepted else 42)

    def test_identity_guards_precede_all_fixture_writes(self):
        script = self.init
        self.assertLess(script.index('= linux,dummy-virt'), script.index('fixture_disk_identity /dev/vda'))
        self.assertLess(script.index('fixture_disk_identity /dev/vda'), script.index('ro,noload /dev/vda'))
        self.assertLess(script.index('a733-kvm-guest-fixture-v1'), script.index('-o rw /dev/vda'))
        self.assertLess(script.index('-o rw /dev/vda'), script.index('> /mnt/counter'))
        self.assertIn('1116 12 28000000c20200006b040000', script)
        self.assertIn('= 262144', script)
        self.assertNotIn('mkfs', script)
        self.assertNotIn('/dev/nvme', script)
        self.assertNotIn('/dev/mmc', script)
        self.assertIn('of=/mnt/endurance-scratch.bin bs=1048576 count=16', script)
        self.assertIn('verify_hash /mnt/endurance-scratch.bin "$DISK_SHA256"', script)

    def test_status_schema_and_monotonic_finish(self):
        line = next(line for line in self.init.splitlines() if "printf '{\"schema\"" in line)
        template = line.split("printf '", 1)[1].split("'", 1)[0].replace('\\n', '')
        status = json.loads(template % ('12345678-1234-1234-1234-123456789abc', 'release',
                                       'running', '28800', '30', '2', '4', 'hash'))
        self.assertEqual(set(status), {'schema', 'profile', 'run_id', 'mode', 'state',
                                      'required_seconds', 'elapsed_seconds', 'cycles',
                                      'disk_counter', 'cpu_workers', 'cpu_workers_alive', 'disk_sha256'})
        self.assertEqual(status['cpu_workers_alive'], 4)
        self.assertIn('/proc/uptime', self.init)
        self.assertIn('[ "$elapsed" -lt "$DURATION" ] || break', self.init)
        self.assertIn('[ "$beat" -gt "$previous" ] || fail cpu-stalled', self.init)
        self.assertLess(self.init.index('cleanup || fail cleanup'), self.init.index("printf 'ENDURANCE_PASS"))
        self.assertNotIn('120', self.init)

    def test_cgi_rejects_posts_and_queries_without_interpretation(self):
        for method, query in [('POST', ''), ('GET', '$(touch /tmp/endurance-cgi-unsafe)')]:
            result = subprocess.run(['sh', str(REPO / 'configs/endurance-guest-status.cgi')],
                                    env={'REQUEST_METHOD': method, 'QUERY_STRING': query},
                                    check=True, capture_output=True, text=True)
            self.assertIn('400 Bad Request', result.stdout)
            self.assertNotIn('application/json', result.stdout)


if __name__ == '__main__':
    unittest.main()

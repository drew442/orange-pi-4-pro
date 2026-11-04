"""Guards and exact call-site scope for the separate experimental recipe.

No compiler, network, block device, or hardware acceptance is exercised.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
RECIPE = REPO / 'build/el2-diagnostic.sh'
PATCH = REPO / 'build/patches/uboot-a733-el2-handoff.patch'


class El2DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / 'repo'
        self.workspace = root / 'workspace'
        self.base = root / 'base'
        self.output = self.repo / 'out/el2-diagnostic-test'
        for directory in (self.repo / 'out', self.repo / 'configs',
                          self.repo / 'docs/evidence', self.repo / 'build/patches',
                          self.workspace / 'downloads', self.base / 'notices',
                          self.base / 'busybox-source'):
            directory.mkdir(parents=True)
        lock = {}
        for name, tree in [('vendor', 'vendor'), ('uboot', 'u-boot'), ('kernel', 'kernel')]:
            directory = self.workspace / tree
            directory.mkdir()
            self.git(directory, 'init', '-q')
            (directory / 'source').write_text('pinned source\n')
            self.git(directory, 'add', 'source')
            self.git(directory, '-c', 'user.name=Fixture', '-c', 'user.email=f@example.test',
                     'commit', '-qm', 'Fixture')
            lock[name] = {'commit': self.git(directory, 'rev-parse', 'HEAD').stdout.strip()}
        firmware = []
        for index in range(26):
            path = self.workspace / 'vendor' / f'input-{index}'
            path.write_bytes(f'input {index}'.encode())
            firmware.append(f'{self.hash(path)}  {path.name}\n')
        (self.repo / 'configs/vendor-boot-inputs.sha256').write_text(''.join(firmware))
        archive = self.workspace / 'downloads/compiler.tar.xz'
        archive.write_bytes(b'pinned archive fixture')
        lock['uboot_toolchain'] = {'filename': archive.name, 'sha256': self.hash(archive)}
        patch = self.repo / 'build/patches/baseline.patch'
        patch.write_text('baseline patch fixture')
        lock['patches'] = [{'path': str(patch.relative_to(self.repo)), 'sha256': self.hash(patch)}]
        (self.repo / 'configs/diagnostic-sources.lock.json').write_text(json.dumps(lock))
        image = self.base / 'Image'
        image.write_bytes(b'known booted fixture')
        facts = {'files': {'Image': {'sha256': self.hash(image), 'bytes': image.stat().st_size}}}
        (self.repo / 'docs/evidence/diagnostic-20261104.json').write_text(json.dumps(facts))

    @staticmethod
    def hash(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def git(directory, *args):
        return subprocess.run(['git', '-C', str(directory), *args], text=True,
                              capture_output=True, check=True)

    def validate(self, output=None, base=None):
        return subprocess.run(['bash', '-c',
                               'source "$1"; el2_validate_inputs "$2" "$3" "$4" "$5"',
                               'guard', str(RECIPE), str(self.repo), str(self.workspace),
                               str(base or self.base), str(output or self.output)],
                              text=True, capture_output=True)

    def rejected(self, message, **kwargs):
        result = self.validate(**kwargs)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)
        self.assertFalse(self.output.exists())

    def test_sourceable_guard_accepts_pinned_inputs_without_writes(self):
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse((self.workspace / '.diagnostic-build.lock').exists())

    def test_existing_output_rejected_and_untouched(self):
        self.output.mkdir()
        sentinel = self.output / 'sentinel'
        sentinel.write_text('preserve')
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Output must not exist', result.stderr)
        self.assertEqual(sentinel.read_text(), 'preserve')

    def test_devices_and_output_outside_storage_rejected(self):
        self.rejected('Device/pseudo-filesystem', output=Path('/dev/el2-diagnostic-test'))
        self.rejected('directly under', output=self.workspace / 'el2-diagnostic-test')
        self.rejected('Use el2-diagnostic-NAME', output=self.repo / 'out/baseline')

    def test_symlink_parent_and_traversal_rejected(self):
        alias = self.repo / 'alias'
        alias.symlink_to(self.repo / 'out', target_is_directory=True)
        self.rejected('Symlink path forbidden', output=alias / self.output.name)
        self.rejected('Parent traversal', output=self.repo / 'out/../out' / self.output.name)

    def test_modified_base_bytes_rejected_before_output_created(self):
        (self.base / 'Image').write_bytes(b'known booted fixturX')
        self.rejected('Untrusted base artifact')

    def test_modified_vendor_or_compiler_rejected(self):
        firmware = self.workspace / 'vendor/input-0'
        original = firmware.read_bytes()
        firmware.write_bytes(b'changed')
        self.rejected('Untrusted vendor input')
        firmware.write_bytes(original)
        (self.workspace / 'downloads/compiler.tar.xz').write_bytes(b'changed')
        self.rejected('Untrusted toolchain archive')

    def test_tracked_source_change_or_wrong_revision_rejected(self):
        (self.workspace / 'u-boot/source').write_text('modified')
        self.rejected('Tracked source changes')
        self.git(self.workspace / 'u-boot', 'checkout', '--', 'source')
        lockfile = self.repo / 'configs/diagnostic-sources.lock.json'
        lock = json.loads(lockfile.read_text())
        lock['uboot']['commit'] = '0' * 40
        lockfile.write_text(json.dumps(lock))
        self.rejected('Unpinned u-boot HEAD')

    def test_symlink_base_sources_rejected(self):
        (self.base / 'notices/link').symlink_to(self.base / 'Image')
        self.rejected('Unsafe base notice/source')

    def test_patch_changes_only_normal_bootm_service_and_preserves_arguments(self):
        # Exact pinned b791be84 bootm.c declaration and call-site excerpts, including tabs.
        original = '''\tchar *s;
\tunsigned long r2;
\tint fake = (flag & BOOTM_STATE_OS_FAKE_GO);
\tu32 ARM_SVC_RUNNSOS = 0x8000ff04;
\t//phys_size_t fdt_size;

\tkernel_entry = (void (*)(int, int, uint))images->ep;

\t\tif (sunxi_probe_secure_monitor() && (sunxi_get_force_32bit_os() == 0)) {
\t\t\tdebug("u-boot: Calling sunxi_smc_call_atf(): kernel_entry = 0x%lx, dtb_addr = 0x%lx\\n",
\t\t\t\t(ulong)kernel_entry, (ulong)r2);
\t\t\tsunxi_smc_call_atf(ARM_SVC_RUNNSOS, (ulong)kernel_entry, (ulong)r2,  1);
\t\t} else {
\t\t\tdebug("u-boot: Calling kernel_entry()\\n");
\t\t\tkernel_entry(0, machid, r2);
\t\t}
'''
        tree = Path(self.temp.name) / 'patch-tree'
        self.git(Path(self.temp.name), 'init', '-q', str(tree))
        path = tree / 'arch/arm/lib/bootm.c'
        path.parent.mkdir(parents=True)
        path.write_text(original)
        self.git(tree, 'apply', str(PATCH))
        updated = path.read_text()
        expected = original.replace(
            '\tu32 ARM_SVC_RUNNSOS = 0x8000ff04;',
            '\t/* A733 EL2 diagnostic only: use the existing monitor hyper service. */\n'
            '\tu32 ARM_SVC_HYPER = 0x8000ff40;')
        expected = expected.replace('sunxi_smc_call_atf(ARM_SVC_RUNNSOS,',
                                    'sunxi_smc_call_atf(ARM_SVC_HYPER,')
        self.assertEqual(updated, expected)
        self.assertEqual(updated.count('0x8000ff40'), 1)
        self.assertEqual(updated.count('ARM_SVC_HYPER'), 2)
        self.assertNotIn('ARM_SVC_RUNNSOS', updated)



class El2OverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'docs/evidence').mkdir(parents=True)
        self.baseline = self.root / 'baseline.img'
        self.package = self.root / 'boot_package.fex'
        self.output = self.root / 'new.img'
        self.old_package = b'old-package!' * (1392640 // 12) + b'old-package!'[:1392640 % 12]
        with self.baseline.open('wb') as stream:
            stream.truncate(268435456)
            stream.seek(16793600)
            stream.write(self.old_package)
            # Sentinels cover boot0, partition table and complete filesystem.
            for position in (0, 8192, 33554432, 268435455):
                stream.seek(position)
                stream.write(b'B')
        self.package.write_bytes(b'N' * 1392640)
        h = hashlib.sha256()
        with self.baseline.open('rb') as stream:
            for block in iter(lambda: stream.read(1048576), b''):
                h.update(block)
        self.facts = {'files': {
            'a733-diagnostic.img': {'bytes': 268435456, 'sha256': h.hexdigest()},
            'boot_package.fex': {'bytes': 1392640, 'sha256': hashlib.sha256(self.old_package).hexdigest()},
        }}
        self.manifest = self.root / 'docs/evidence/diagnostic-20261104.json'
        self.manifest.write_text(json.dumps(self.facts))

    def overlay(self, output=None, baseline=None):
        return subprocess.run(['bash', '-c',
                               'source "$1"; el2_overlay_boot_package "$2" "$3" "$4" "$5"',
                               'overlay', str(RECIPE), str(self.root),
                               str(baseline or self.baseline), str(self.package), str(output or self.output)],
                              capture_output=True, text=True)

    def test_overlay_changes_only_exact_package_region(self):
        result = self.overlay()
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads(result.stdout)
        self.assertEqual(evidence['outside_package_byte_comparison'], 'identical')
        self.assertEqual(evidence['package_offset'], 16793600)
        self.assertEqual(self.output.stat().st_size, 268435456)
        with self.baseline.open('rb') as old, self.output.open('rb') as new:
            self.assertEqual(old.read(16793600), new.read(16793600))
            self.assertEqual(new.read(1392640), self.package.read_bytes())
            old.seek(16793600 + 1392640)
            for block in iter(lambda: old.read(1048576), b''):
                self.assertEqual(block, new.read(len(block)))
        self.assertEqual(self.baseline.stat().st_size, 268435456)

    def test_overlay_rejects_wrong_package_length_before_creating_output(self):
        self.package.write_bytes(b'too short')
        result = self.overlay()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('match baseline package size', result.stderr)
        self.assertFalse(self.output.exists())

    def test_overlay_rejects_corrupt_baseline_before_creating_output(self):
        with self.baseline.open('r+b') as stream:
            stream.write(b'X')
        result = self.overlay()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Untrusted baseline raw image', result.stderr)
        self.assertFalse(self.output.exists())

    def test_overlay_rejects_existing_output_and_symlink_and_device_paths(self):
        self.output.write_bytes(b'preserve')
        result = self.overlay()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Output must not exist', result.stderr)
        self.assertEqual(self.output.read_bytes(), b'preserve')
        alias = self.root / 'alias.img'
        alias.symlink_to(self.output)
        self.assertIn('Symlink path forbidden', self.overlay(output=alias).stderr)
        self.assertIn('Device/pseudo-filesystem', self.overlay(output=Path('/dev/new.img')).stderr)

    def test_overlay_rejects_nonregular_input(self):
        result = self.overlay(baseline=self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Expected regular input', result.stderr)
        self.assertFalse(self.output.exists())

    def test_overlay_rejects_oversize_layout_and_package_region_mismatch(self):
        self.facts['files']['a733-diagnostic.img']['bytes'] = 1977614337
        self.manifest.write_text(json.dumps(self.facts))
        result = self.overlay()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unexpected baseline image size', result.stderr)
        self.assertFalse(self.output.exists())
        self.facts['files']['a733-diagnostic.img']['bytes'] = 268435456
        self.facts['files']['boot_package.fex']['sha256'] = '0' * 64
        self.manifest.write_text(json.dumps(self.facts))
        result = self.overlay()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Baseline package region mismatches', result.stderr)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()

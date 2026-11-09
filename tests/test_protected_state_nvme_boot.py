"""Bounded regular-file component checks; no hardware acceptance claim."""
import copy
import gzip
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('protected_nvme_boot', ROOT / 'build/protected-state-nvme-boot.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


class NvmeBootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nvme-boot-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_extracts_only_extent_and_supports_implicit_zero_bytes(self):
        raw = b'prefix' + bytes(4096) + b'BOOT' + b'not emitted' * 100
        source = self.root / 'base.gz'
        source.write_bytes(gzip.compress(raw, mtime=0))
        with P.RUNTIME.HeldInput(source, 10000) as held:
            P.extract_boot(held, self.root / 'boot', 6, 4100)
            held.verify()
        self.assertEqual((self.root / 'boot').read_bytes(), bytes(4096) + b'BOOT')
        with P.RUNTIME.HeldInput(source, 10000) as held, self.assertRaises(ValueError):
            P.extract_boot(held, self.root / 'short', 0, len(raw) + 1)
        with P.RUNTIME.HeldInput(source, 10000) as held, self.assertRaises(FileExistsError):
            P.extract_boot(held, self.root / 'boot', 0, 1)

    def test_fixed_wire_pin_and_mutation_rejected(self):
        path = self.root / 'input'
        path.write_bytes(b'wrong')
        with self.assertRaises(ValueError):
            P.RUNTIME.HeldInput(path, P.WIRE['bytes'], P.WIRE)
        with P.RUNTIME.HeldInput(path, 100) as held:
            path.write_bytes(b'other')
            with self.assertRaises(ValueError):
                held.verify()
        link = self.root / 'link'
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            P.RUNTIME.HeldInput(link, 100)

    def test_manifest_binds_exact_nvme_namespace_and_layout(self):
        namespace = {name: {'data': pin} for name, pin in P.HELP.BOOT_FILES.items()}
        namespace[P.HELP.DTB_PATH] = {'data': P.RUNTIME.PATCHED_DTB_RECORD}
        manifest = {'files': {'nvme.img.gz': P.WIRE, 'nvme.img': P.RAW},
                    'state_uuid': P.RUNTIME.STATE_UUID,
                    'layout': {'partitions': [{'number': 1, 'name': 'boot',
                                              'start_bytes': P.BOOT_OFFSET, 'size_bytes': P.BOOT_BYTES}]},
                    'boot_files': {n: i['data'] for n, i in namespace.items()},
                    'derivation': {'complete_boot_namespace_after': namespace}}
        P.validate_base(manifest, namespace)
        for field in ('commands', 'dtb', 'extra', 'layout', 'state'):
            bad, ns = copy.deepcopy(manifest), copy.deepcopy(namespace)
            if field == 'commands':
                ns['boot/boot.cmd']['data'] = P.SD.SD_COMMANDS['boot/boot.cmd']
            elif field == 'dtb':
                ns[P.HELP.DTB_PATH]['data'] = {'bytes': 1, 'sha256': '0' * 64}
            elif field == 'extra':
                ns['unexpected'] = {}
            elif field == 'layout':
                bad['layout']['partitions'][0]['start_bytes'] = 0
            else:
                bad['state_uuid'] = 'wrong'
            with self.subTest(field=field), self.assertRaises(ValueError):
                P.validate_base(bad, ns)

    def test_output_scope_and_two_gib_admission(self):
        (self.root / 'out').mkdir()
        with mock.patch.object(P, 'REPO', self.root):
            good = self.root / 'out/protected-state-nvme-boot-fixture'
            self.assertEqual(P.output_path(good), good)
            good.mkdir()
            for bad in (good, self.root / 'out/other', Path('relative')):
                with self.assertRaises(ValueError):
                    P.output_path(bad)
        with mock.patch.object(P.SD, 'resources', return_value={'free_bytes': P.SD.DISK_FLOOR + P.SCRATCH_BUDGET - 1}):
            P.resources(self.root)
            with self.assertRaises(ValueError):
                P.resources(self.root, admission=True)

    @unittest.skipUnless(all(shutil.which(t) for t in ('mke2fs', 'debugfs', 'e2fsck')), 'ext4 tools required')
    def test_real_small_ext4_preserves_nvme_commands_and_all_metadata(self):
        tree = self.root / 'tree'
        for name in P.HELP.BOOT_FILES:
            target = tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(name.encode())
        (tree / 'boot/boot.cmd').write_bytes(b'NVMe boot command fixture')
        image = self.root / 'boot.ext4'
        with image.open('wb') as stream:
            stream.truncate(16 * 1024**2)
        subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-F', '-d', str(tree), str(image)], check=True, capture_output=True)
        before = P.HELP.namespace(image, self.root / 'before')
        payload = self.root / 'uInitrd'
        payload.write_bytes(b'new initrd fixture')
        P.SD.patch_boot(image, payload, before, self.root / 'patch.log')
        after = P.HELP.namespace(image, self.root / 'after')
        P.SD.compare_namespace(before, after, P.record(payload.read_bytes()))
        for name in ('boot/boot.cmd', 'boot/boot.scr', 'boot/uImage', P.HELP.DTB_PATH):
            self.assertEqual(before[name], after[name])
        after['boot/boot.cmd']['metadata']['uid'] = 1
        with self.assertRaises(ValueError):
            P.SD.compare_namespace(before, after, P.record(payload.read_bytes()))


if __name__ == '__main__':
    unittest.main()

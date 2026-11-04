"""Small regular-file bundle fixtures; no physical or installation acceptance."""
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('nvme_bundle_tests', REPO / 'build/nvme-bundle.py')
BUNDLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUNDLE)
BUILDER_PACKAGES = BUNDLE.builder_packages
RUNTIME_EVIDENCE = BUNDLE.runtime_evidence


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'out').mkdir()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.output = self.root / 'out/nvme-bundle-test'
        self.raw = bytes(range(256)) * 32 + b'\0' * (2*1024**2)
        (self.source / 'nvme.img').write_bytes(self.raw)
        self.manifest = {'schema': 1, 'kernel_release': '6.6.98-a733-diag',
                         'logical_sector_bytes': 512, 'physical_sector_bytes': 4096,
                         'layout': {'test_fixture_geometry': True},
                         'classification': 'nondeployable test fixture',
                         'deployment_intent': {'for_installation': False, 'preserved': ['fixture']},
                         'files': {'nvme.img': record(self.raw)}, 'unknown_field': {'preserve': 19}}
        self.save_manifest()
        for name, value in [('REPO', self.root)]:
            patcher = patch.object(BUNDLE, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(BUNDLE.WRITER.LAYOUT, 'layout', return_value=self.manifest['layout'])
        patcher.start()
        self.addCleanup(patcher.stop)
        # Geometry checks have a separate test against the real shared layout.
        self.real_layout = importlib.util.spec_from_file_location('bundle_test_layout', REPO / 'scripts/nvme_layout.py')
        self.layout_module = importlib.util.module_from_spec(self.real_layout)
        self.real_layout.loader.exec_module(self.layout_module)
        for target, name, value in [(BUNDLE, 'RECIPES', ()),
                                    (BUNDLE, 'builder_packages', {'pinned-test': 'fixture'}),
                                    (BUNDLE, 'runtime_evidence', {'runtime-test': 'fixture'}),
                                    (BUNDLE.STATE, 'validate_workspace_storage', {'fstype': 'ext4'})]:
            patcher = patch.object(target, name, value) if name == 'RECIPES' else patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def save_manifest(self):
        (self.source / 'manifest.json').write_text(json.dumps(self.manifest) + '\n')

    def build(self):
        return BUNDLE.build(self.source, self.output)

    def test_roundtrip_preserves_all_source_semantics_and_sidecar(self):
        original = (self.source / 'manifest.json').read_bytes()
        result = self.build()
        old_fields = copy.deepcopy(result)
        old_fields.pop('distribution')
        old_fields['files'].pop('nvme.img.gz')
        self.assertEqual(old_fields, self.manifest)
        encoded = (self.output / 'nvme.img.gz').read_bytes()
        self.assertEqual(gzip.decompress(encoded), self.raw)
        self.assertEqual(result['files']['nvme.img.gz'], record(encoded))
        self.assertEqual(result['distribution']['source_manifest'], record(original))
        final = (self.output / 'manifest.json').read_bytes()
        self.assertEqual((self.output / 'manifest.sha256').read_text(), hashlib.sha256(final).hexdigest() + '  manifest.json\n')
        self.assertFalse((self.output / 'nvme.img').exists())
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for path in self.output.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertFalse(result['deployment_intent']['for_installation'])
        self.assertFalse(encoded[3] & 8)  # no filename field
        self.assertEqual(encoded[4:8], b'\0'*4)

    def test_legacy_fixture_preserves_absent_deployment_intent(self):
        self.manifest.pop('deployment_intent')
        self.save_manifest()
        result = self.build()
        self.assertNotIn('deployment_intent', result)
        original_fields = copy.deepcopy(result)
        original_fields.pop('distribution')
        original_fields['files'].pop('nvme.img.gz')
        self.assertEqual(original_fields, self.manifest)
        with self.assertRaisesRegex(ValueError, 'fixture'):
            BUNDLE.WRITER.validate_manifest(result)

    def test_present_malformed_deployment_intent_refused(self):
        for intent in (None, {}, False, {'for_installation': 0}, {'for_installation': 'false'}):
            self.manifest['deployment_intent'] = intent
            with self.subTest(intent=intent), self.assertRaisesRegex(ValueError, 'malformed deployment intent'):
                BUNDLE.validate_manifest(self.manifest)

    def test_gzip_is_deterministic_for_same_input(self):
        self.build()
        first = (self.output / 'nvme.img.gz').read_bytes()
        second = self.root / 'out/nvme-bundle-repeat'
        BUNDLE.build(self.source, second)
        self.assertEqual(first, (second / 'nvme.img.gz').read_bytes())

    def test_source_hash_mismatch_keeps_private_failed_output(self):
        self.manifest['files']['nvme.img']['sha256'] = '0'*64
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            self.build()
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_source_size_mismatch_fails_before_mkdir(self):
        with (self.source / 'nvme.img').open('ab') as stream:
            stream.write(b'x')
        with self.assertRaisesRegex(ValueError, 'source size'):
            self.build()
        self.assertFalse(self.output.exists())

    def test_source_changes_after_compression_refused(self):
        original = BUNDLE.compress
        def changed(source, *args):
            original(source, *args)
            os.utime(self.source / 'nvme.img', ns=(1, 1))
        with patch.object(BUNDLE, 'compress', side_effect=changed), self.assertRaisesRegex(ValueError, 'changed while held'):
            self.build()
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_manifest_changes_after_compression_refused(self):
        original = BUNDLE.compress
        def changed(source, *args):
            original(source, *args)
            os.utime(self.source / 'manifest.json', ns=(1, 1))
        with patch.object(BUNDLE, 'compress', side_effect=changed), self.assertRaisesRegex(ValueError, 'manifest changed'):
            self.build()
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_nonregular_and_symlink_sources_refused_without_blocking(self):
        for name in ('nvme.img', 'manifest.json'):
            path = self.source / name
            saved = path.read_bytes()
            for kind in ('fifo', 'symlink', 'directory'):
                path.unlink()
                if kind == 'fifo':
                    os.mkfifo(path)
                elif kind == 'symlink':
                    path.symlink_to(self.source / 'absent')
                else:
                    path.mkdir()
                with self.subTest(name=name, kind=kind), self.assertRaises((ValueError, OSError)):
                    self.build()
                if kind == 'directory':
                    path.rmdir()
                else:
                    path.unlink()
                path.write_bytes(saved)
                self.assertFalse(self.output.exists())

    def test_symlink_source_namespace_refused(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink ancestors'):
            BUNDLE.build(alias, self.output)

    def test_output_namespace_existing_directory_and_symlink_refused(self):
        for path in (Path('out/nvme-bundle-relative'), self.root/'nvme-bundle-outside', self.root/'out/nvme-bundle-'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                BUNDLE.validate_output(path)
        self.output.mkdir()
        with self.assertRaises(ValueError):
            self.build()
        self.output.rmdir()
        self.output.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.build()

    def test_symlink_output_parent_refused(self):
        (self.root / 'out').rmdir()
        (self.root / 'out').symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink ancestors'):
            self.build()

    def test_storage_guard_failure_precedes_mkdir(self):
        with patch.object(BUNDLE.STATE, 'validate_workspace_storage', side_effect=ValueError('SD refused')):
            with self.assertRaisesRegex(ValueError, 'SD refused'):
                self.build()
        self.assertFalse(self.output.exists())

    def test_insufficient_space_precedes_mkdir(self):
        with patch.object(BUNDLE.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 1})()):
            with self.assertRaisesRegex(ValueError, 'free space'):
                self.build()
        self.assertFalse(self.output.exists())

    def test_encoded_output_budget_enforced(self):
        with self.assertRaisesRegex(ValueError, 'free-space budget'):
            BUNDLE.compress(io.BytesIO(self.raw), io.BytesIO(), record(self.raw), 10)

    def test_inflate_bound_short_data_and_bad_crc(self):
        encoded = gzip.compress(self.raw, mtime=0)
        for data, expected in ((encoded, record(self.raw[:-1])),
                               (encoded, record(self.raw + b'x')),
                               (encoded[:-1], record(self.raw)),
                               (encoded[:-8] + b'\0'*8, record(self.raw))):
            with self.subTest(length=len(data), expected=expected['bytes']):
                with self.assertRaises((ValueError, EOFError, gzip.BadGzipFile)):
                    BUNDLE.verify_encoded(io.BytesIO(data), expected, len(data))

    def test_corrupt_readback_never_publishes_manifest(self):
        with patch.object(BUNDLE, 'verify_encoded', side_effect=ValueError('corrupt expansion')):
            with self.assertRaisesRegex(ValueError, 'corrupt expansion'):
                self.build()
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_schema_profile_intent_and_already_bundled_validation(self):
        for field, bad in (('schema', True), ('kernel_release', 'other'),
                           ('deployment_intent', {'for_installation': 'yes'}), ('layout', {}),
                           ('distribution', {})):
            changed = copy.deepcopy(self.manifest)
            changed[field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                BUNDLE.validate_manifest(changed)

    def test_real_geometry_and_installation_eligibility_unchanged(self):
        candidate = copy.deepcopy(self.manifest)
        candidate['files']['nvme.img']['bytes'] = 250_000_000_000
        candidate['layout'] = self.layout_module.layout(250_000_000_000, 512, 4096)
        candidate['deployment_intent'] = {'for_installation': True, 'management_mac': '02:00:00:73:33:12',
                                        'management_cidr': '192.168.50.0/24',
                                        'administration_public_key_fingerprint': 'SHA256:' + 'A'*43}
        with patch.object(BUNDLE.WRITER.LAYOUT, 'layout', side_effect=self.layout_module.layout):
            self.assertEqual(BUNDLE.validate_manifest(candidate), candidate['files']['nvme.img'])
            BUNDLE.WRITER.validate_manifest(candidate)
            candidate['layout']['partitions'][0]['size_bytes'] += 512
            with self.assertRaisesRegex(ValueError, 'layout'):
                BUNDLE.validate_manifest(candidate)
            candidate['layout'] = self.layout_module.layout(250_000_000_000, 512, 4096)
            candidate['deployment_intent']['management_cidr'] = '192.0.2.0/24'
            with self.assertRaisesRegex(ValueError, 'fixture'):
                BUNDLE.validate_manifest(candidate)
        with self.assertRaisesRegex(ValueError, 'fixture'):
            BUNDLE.WRITER.validate_manifest(self.manifest)

    def test_compression_package_versions_match_lock_exactly(self):
        (self.root / 'configs').mkdir()
        lock = (REPO / 'configs/builder-packages.lock.tsv').read_text()
        (self.root / 'configs/builder-packages.lock.tsv').write_text(lock)
        selected = {'python3.10-minimal', 'libpython3.10-minimal', 'libpython3.10-stdlib', 'zlib1g'}
        rows = [row for row in lock.splitlines() if row.split('\t')[0].split(':')[0] in selected]
        output = '\n'.join(rows) + '\n'
        with patch.object(BUNDLE.subprocess, 'check_output', return_value=output):
            result = BUILDER_PACKAGES()
        self.assertEqual(set(result), selected)
        with patch.object(BUNDLE.subprocess, 'check_output', return_value=output.replace('3.10.12', '3.10.99')):
            with self.assertRaisesRegex(ValueError, 'differ from pinned'):
                BUILDER_PACKAGES()
        with patch.object(BUNDLE.subprocess, 'check_output', return_value=output + rows[0] + '\n'):
            with self.assertRaisesRegex(ValueError, 'ambiguous installed'):
                BUILDER_PACKAGES()
        (self.root / 'configs/builder-packages.lock.tsv').write_text('')
        with self.assertRaisesRegex(ValueError, 'lacks compression dependencies'):
            BUILDER_PACKAGES()

    def test_runtime_binds_interpreter_isolation_module_paths_and_owners(self):
        from types import SimpleNamespace
        packages = {name: {'architecture': 'amd64'} for name in
                    ('python3.10-minimal', 'libpython3.10-stdlib', 'zlib1g')}
        library = Path('/usr/lib/x86_64-linux-gnu/libz.so.1.2.11')
        paths = {'/usr/bin/python3.10': 'python3.10-minimal',
                 '/usr/lib/python3.10/gzip.py': 'libpython3.10-stdlib',
                 '/lib/x86_64-linux-gnu/libz.so.1.2.11': 'zlib1g'}
        def ownership(command, **kwargs):
            return paths[command[-1]] + ':amd64: ' + command[-1] + '\n'
        def resolve(path):
            return library if str(path) == '/lib/x86_64-linux-gnu/libz.so.1.2.11' else path
        with patch.object(BUNDLE.sys, 'flags', SimpleNamespace(isolated=1, ignore_environment=1, no_user_site=1)), \
             patch.object(BUNDLE.sys, 'version_info', (3, 10, 12)), \
             patch.object(BUNDLE.sys, 'executable', '/usr/bin/python3.10'), \
             patch.object(BUNDLE.sys, 'builtin_module_names', ('zlib',)), \
             patch.object(BUNDLE.gzip, '__file__', '/usr/lib/python3.10/gzip.py'), \
             patch.object(BUNDLE.zlib, '__spec__', SimpleNamespace(origin='built-in')), \
             patch.object(BUNDLE.zlib, 'ZLIB_VERSION', '1.2.11'), \
             patch.object(BUNDLE.zlib, 'ZLIB_RUNTIME_VERSION', '1.2.11'), \
             patch.object(BUNDLE, 'loaded_zlib', return_value=library), \
             patch.object(Path, 'resolve', resolve), \
             patch.object(BUNDLE.os, 'access', return_value=True), \
             patch.object(BUNDLE, 'file_record', return_value=record(b'runtime')), \
             patch.object(BUNDLE.subprocess, 'check_output', side_effect=ownership):
            evidence = RUNTIME_EVIDENCE(packages)
            self.assertEqual(set(evidence), {'executable', 'gzip_module', 'zlib_module', 'zlib_library'})
            self.assertEqual(evidence['gzip_module']['sha256'], record(b'runtime')['sha256'])
            self.assertEqual(evidence['zlib_module']['covered_by'], 'executable')
            for target, field, value in (
                    (BUNDLE.sys, 'executable', '/usr/local/bin/python3.10'),
                    (BUNDLE.sys, 'version_info', (3, 10, 13)),
                    (BUNDLE.sys, 'flags', SimpleNamespace(isolated=0, ignore_environment=1, no_user_site=1)),
                    (BUNDLE.sys, 'flags', SimpleNamespace(isolated=1, ignore_environment=0, no_user_site=1)),
                    (BUNDLE.sys, 'builtin_module_names', ()),
                    (BUNDLE.gzip, '__file__', '/tmp/gzip.py'),
                    (BUNDLE.zlib, '__spec__', SimpleNamespace(origin='/tmp/zlib.so')),
                    (BUNDLE.zlib, 'ZLIB_VERSION', '1.2.13'),
                    (BUNDLE.zlib, 'ZLIB_RUNTIME_VERSION', '1.2.13')):
                with self.subTest(field=field, value=value), patch.object(target, field, value):
                    with self.assertRaises(ValueError):
                        RUNTIME_EVIDENCE(packages)
            with patch.object(BUNDLE, 'loaded_zlib', return_value=Path('/tmp/libz.so.1.2.11')):
                with self.assertRaisesRegex(ValueError, 'differs from packaged'):
                    RUNTIME_EVIDENCE(packages)
            with patch.object(BUNDLE.subprocess, 'check_output', return_value='other: /usr/bin/python3.10\n'):
                with self.assertRaisesRegex(ValueError, 'unexpected package owner'):
                    RUNTIME_EVIDENCE(packages)
            with patch.object(BUNDLE.subprocess, 'check_output', return_value=''):
                with self.assertRaisesRegex(ValueError, 'ambiguous runtime package ownership'):
                    RUNTIME_EVIDENCE(packages)

    def test_mapped_zlib_library_identity_is_unique_and_pinned(self):
        mapped = '/usr/lib/x86_64-linux-gnu/libz.so.1.2.11'
        def maps(paths):
            return ''.join('7f00-7fff r-xp 00000000 08:01 123 ' + path + '\n' for path in paths).encode()
        with patch.object(BUNDLE.WRITER, 'open_regular', side_effect=lambda path: io.BytesIO(maps([mapped, mapped]))), \
             patch.object(Path, 'resolve', lambda path: path):
            self.assertEqual(BUNDLE.loaded_zlib(), Path(mapped))
        for paths in ([], [mapped, '/tmp/libz.so.1.2.11'], ['/tmp/libz.so.1.2.11'],
                      ['/usr/lib/x86_64-linux-gnu/libz.so.1.2.13']):
            with self.subTest(paths=paths), patch.object(BUNDLE.WRITER, 'open_regular', side_effect=lambda path: io.BytesIO(maps(paths))):
                with self.assertRaises(ValueError):
                    BUNDLE.loaded_zlib()

    def test_sparse_holes_are_included_in_logical_hash(self):
        path = self.source / 'nvme.img'
        with path.open('wb') as stream:
            stream.write(self.raw[:8192])
            stream.truncate(len(self.raw))
        self.assertEqual(path.read_bytes(), self.raw)
        result = self.build()
        self.assertEqual(result['files']['nvme.img'], record(self.raw))
        self.assertEqual(gzip.decompress((self.output / 'nvme.img.gz').read_bytes()), self.raw)

    def test_manifest_size_bound_and_duplicate_fields(self):
        for data in (b' '*(4*1024**2+1), b'{"schema":1,"schema":1}'):
            (self.source / 'manifest.json').write_bytes(data)
            with self.assertRaises(ValueError):
                self.build()
            self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()

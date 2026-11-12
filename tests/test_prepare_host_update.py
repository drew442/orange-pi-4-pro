"""Small authenticated fixtures test staging policy, never hardware acceptance."""
import ast
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import shutil
import subprocess
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('prepare_host_update', Path(__file__).resolve().parents[1] / 'scripts/prepare-host-update.py')
UPDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPDATE)
SIZE = 4096


def digest(data):
    return hashlib.sha256(data).hexdigest()


@contextlib.contextmanager
def test_parent(path):
    """Tests permit /tmp ancestry; production CLI never replaces safe_parent."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


LEGACY_READER = '50bc9e70698d4a09c3705594dcc61c64e1715cb9'


def legacy_definitions(path, names, namespace):
    """Execute exact historical reader bodies with explicit local dependencies."""
    source = subprocess.check_output(['git', 'show', f'{LEGACY_READER}:{path}'],
                                     cwd=UPDATE.REPO)
    parsed = ast.parse(source)
    nodes = [node for node in parsed.body if getattr(node, 'name', None) in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError('historical reader definitions missing')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, 'exec'), namespace)
    return namespace


@unittest.skipUnless(os.geteuid() == 0 and all(shutil.which(tool) for tool in
    ('git', 'mke2fs', 'tune2fs', 'e2fsck', 'debugfs')), 'root and filesystem tools required')
class LegacyArtifactCompatibilityTests(unittest.TestCase):
    def test_exact_old_source_and_manifest_read_authenticated_new_and_legacy_roots(self):
        payload = UPDATE.INIT.PAYLOAD
        historical_payload = ast.parse(subprocess.check_output(
            ['git', 'show', f'{LEGACY_READER}:build/host-payload.py'], cwd=UPDATE.REPO))
        original_four = next(ast.literal_eval(node.value) for node in historical_payload.body
                            if isinstance(node, ast.Assign) and any(
                                isinstance(target, ast.Name) and target.id == 'MANAGEMENT_FILES'
                                for target in node.targets))
        self.assertEqual(payload.MANAGEMENT_FILES, original_four)
        init_globals = legacy_definitions('build/host-initramfs.py', ['root_marker'],
                                         dict(vars(UPDATE.INIT)))
        old_init = SimpleNamespace(**vars(UPDATE.INIT))
        old_init.root_marker = init_globals['root_marker']
        prep_globals = dict(vars(UPDATE), INIT=old_init)
        legacy_definitions('scripts/prepare-host-update.py', ['validate_manifest', 'compatibility'],
                           prep_globals)
        old_prep = SimpleNamespace(**prep_globals)
        reader_globals = dict(vars(UPDATE), PREP=old_prep, storage_guard=lambda fd: None)
        legacy_definitions('scripts/update-host.py', ['Source'], reader_globals)
        # Manifest's own globals must retain the historical PREP reader.
        manifest_globals = dict(reader_globals, UPDATE=SimpleNamespace(storage_guard=lambda fd: None))
        legacy_definitions('scripts/select-host-root.py', ['Manifest'], manifest_globals)
        diagnostic = json.loads((UPDATE.REPO / 'docs/evidence/diagnostic-20261104.json').read_bytes())['files']
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            evidence = base / 'docs/evidence'
            evidence.mkdir(parents=True)
            (evidence / 'diagnostic-20261104.json').write_text(json.dumps({'files': diagnostic}))
            root = base / 'seed'
            root.mkdir()
            management = payload.install_management(root)
            guest_boot = payload.install_guest_boot(root)
            for _name, (target, kind) in payload.PACKAGE.COMPONENTS.items():
                path = root / target
                path.parent.mkdir(parents=True, exist_ok=True)
                if kind == 'directory':
                    path.mkdir()
                else:
                    path.write_bytes(b'fixture')
            package = payload.PACKAGE.snapshot(root)
            for extended in (False, True):
                candidate = base / ('extended' if extended else 'legacy')
                candidate.mkdir(mode=0o700)
                marker = {'slot': 'A', 'filesystem_uuid': payload.SLOT_UUIDS['A'],
                          'kernel_release': payload.KERNEL_RELEASE, 'sources': diagnostic,
                          'management_runtime': management, 'package_state': package}
                if extended:
                    marker['guest_boot_runtime'] = guest_boot
                payload.write_json(root / 'usr/lib/orangepi-hypervisor/payload.json', marker)
                image = candidate / 'root.ext4'
                size = 32 * 1024**2
                payload.make_image(root, image, 'A', size=size)
                manifest = {'schema': 1, 'board': 'Orange Pi 4 Pro A733', 'slot_bytes': size,
                            'slot_uuids': payload.SLOT_UUIDS, **{name: marker[name] for name in
                                ('kernel_release', 'sources', 'management_runtime', 'package_state')},
                            'files': {'root-A.ext4': payload.file_record(image)}}
                if extended:
                    manifest['guest_boot_runtime'] = guest_boot
                # The retired six-file management shape is refused by both validators.
                six = copy.deepcopy(manifest)
                six['management_runtime']['files'].update(guest_boot['files'])
                for validator in (old_prep.validate_manifest, UPDATE.validate_manifest):
                    validator(manifest, 'A', size)
                    with self.assertRaisesRegex(ValueError, 'management profile'):
                        validator(six, 'A', size)
                for name, _mode in payload.GUEST_BOOT_FILES.values():
                    data = subprocess.check_output(['debugfs', '-R', 'cat /' + name, str(image)],
                                                   stderr=subprocess.DEVNULL)
                    self.assertEqual(UPDATE.record(data), guest_boot['files'][name])
                    details = subprocess.check_output(['debugfs', '-R', 'stat /' + name, str(image)],
                                                      stderr=subprocess.DEVNULL)
                    self.assertRegex(details, rb'Mode:\s+0?644\b')
                manifest_path = candidate / 'manifest.json'
                payload.write_json(manifest_path, manifest)
                with image.open('rb') as stream:
                    selector, _ = old_prep.compatibility(stream, 'A', manifest)
                with image.open('rb') as stream:
                    current_selector, _ = UPDATE.compatibility(stream, 'A', manifest)
                self.assertEqual(current_selector, selector)
                with mock.patch.object(UPDATE.SELECT, 'REPO', base), \
                        mock.patch.object(UPDATE.SELECT.INIT.PAYLOAD, 'SLOT_BYTES', size):
                    self.assertEqual(UPDATE.SELECT.prepare(image, 'A', manifest_path), selector)
                (candidate / 'selected.conf').write_bytes(selector)
                for path in candidate.iterdir():
                    path.chmod(0o400)
                pin = payload.file_record(manifest_path)['sha256']
                source = reader_globals['Source'](candidate, pin, 'A', parent_opener=test_parent,
                    guard=lambda fd: None, slot_bytes=size)
                source.close()
                protected = manifest_globals['Manifest'](manifest_path, pin, 'A',
                    parent_opener=test_parent, guard=lambda fd: None, slot_bytes=size)
                protected.close()
                # Source still authenticates complete roots and derived selectors.
                for filename in ('root.ext4', 'selected.conf'):
                    path = candidate / filename
                    original = path.read_bytes()
                    path.chmod(0o600)
                    path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
                    path.chmod(0o400)
                    with self.subTest(extended=extended, altered=filename), self.assertRaises(ValueError):
                        reader_globals['Source'](candidate, pin, 'A', parent_opener=test_parent,
                            guard=lambda fd: None, slot_bytes=size)
                    path.chmod(0o600)
                    path.write_bytes(original)
                    path.chmod(0o400)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.output = self.parent / 'candidate'
        self.image = self.parent / 'root-A.ext4'
        self.content = b'root' + bytes(SIZE - 4)
        self.image.write_bytes(self.content)
        self.manifest = self.parent / 'payload.json'
        self.boot = {name: {'bytes': 1, 'sha256': digest(name.encode())}
                     for name in UPDATE.INIT.PAYLOAD.BOOT_FILES}
        self.value = {'schema': 1, 'board': 'Orange Pi 4 Pro A733',
                      'slot_bytes': SIZE, 'slot_uuids': UPDATE.INIT.PAYLOAD.SLOT_UUIDS.copy(),
                      'kernel_release': UPDATE.INIT.PAYLOAD.KERNEL_RELEASE,
                      'files': {'root-A.ext4': UPDATE.record(self.content),
                                'root-B.ext4': UPDATE.record(self.content)},
                      'sources': self.boot,
                      'management_runtime': {'profile': 'a733-management-v1',
                          'files': {v[0]: {'bytes': 1, 'sha256': 'a' * 64}
                                    for v in UPDATE.INIT.PAYLOAD.MANAGEMENT_FILES.values()}},
                      'package_state': {'profile': UPDATE.INIT.PAYLOAD.PACKAGE.PROFILE,
                                        'manifest': {'bytes': 1, 'sha256': 'b' * 64}}}
        self.metadata = {key: copy.deepcopy(self.value[key])
                         for key in ('sources', 'management_runtime', 'package_state')}
        self.write_manifest()

    def write_manifest(self, data=None):
        self.data = json.dumps(self.value).encode() if data is None else data
        self.manifest.write_bytes(self.data)
        self.pin = digest(self.data)

    def inspect(self, stream, slot, manifest):
        self.assertEqual(stream.read(), self.content)
        self.assertEqual(stat.S_IMODE(os.fstat(stream.fileno()).st_mode), 0o400)
        with mock.patch.object(UPDATE.INIT, 'root_marker', return_value=(b'marker', self.metadata)):
            return UPDATE.compatibility(stream, slot, manifest, self.boot)

    def prepare(self, **kw):
        options = {'slot_bytes': SIZE, 'reserve': 0, 'parent_opener': test_parent,
                   'storage_guard': lambda fd, kind: None, 'inspect': self.inspect}
        options.update(kw)
        return UPDATE.prepare(self.image, self.output, 'A', self.manifest, self.pin,
                              'external', **options)

    def assert_clean(self):
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.parent.glob('.host-update-*')), [])

    def test_guest_boot_extension_validates_exact_presence_and_pins(self):
        payload = UPDATE.INIT.PAYLOAD
        extension = {'profile': 'a733-guest-boot-v1', 'files': {
            dest: {'bytes': 1, 'sha256': 'a' * 64} for dest, _ in payload.GUEST_BOOT_FILES.values()}}
        UPDATE.validate_manifest(self.value, 'A', SIZE)
        extended = {**self.value, 'guest_boot_runtime': extension}
        UPDATE.validate_manifest(extended, 'A', SIZE)
        invalid = [None, {}, [], {**extension, 'extra': 1}, {**extension, 'profile': 'unknown'},
                   {**extension, 'files': {}}, {**extension, 'files': {**extension['files'], 'extra': {}}}]
        for bad_record in ({'bytes': True, 'sha256': 'a' * 64}, {'bytes': 0, 'sha256': 'a' * 64},
                           {'bytes': 1, 'sha256': 'A' * 64}, {'bytes': 1},
                           {'bytes': 1, 'sha256': 'a' * 64, 'mode': 420}):
            bad = copy.deepcopy(extension)
            bad['files'][next(iter(bad['files']))] = bad_record
            invalid.append(bad)
        for bad in invalid:
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'guest_boot_runtime'):
                UPDATE.validate_manifest({**self.value, 'guest_boot_runtime': bad}, 'A', SIZE)
            with mock.patch.object(UPDATE.INIT, 'root_marker', return_value=(b'marker', {
                    **self.metadata, 'guest_boot_runtime': bad})):
                with self.assertRaisesRegex(ValueError, 'guest_boot_runtime'):
                    UPDATE.compatibility(None, 'A', extended, self.boot)
        six = copy.deepcopy(self.value)
        six['management_runtime']['files'].update(extension['files'])
        with self.assertRaisesRegex(ValueError, 'management profile'):
            UPDATE.validate_manifest(six, 'A', SIZE)
        extended_marker = {**self.metadata, 'guest_boot_runtime': extension}
        changed = copy.deepcopy(extended_marker)
        next(iter(changed['guest_boot_runtime']['files'].values()))['sha256'] = 'b' * 64
        for manifest, metadata, valid in ((self.value, self.metadata, True),
                (extended, extended_marker, True), (self.value, extended_marker, False),
                (extended, self.metadata, False), (extended, changed, False)):
            with self.subTest(valid=valid), mock.patch.object(UPDATE.INIT, 'root_marker',
                    return_value=(b'marker', metadata)):
                if valid:
                    _, compatible = UPDATE.compatibility(None, 'A', manifest, self.boot)
                    self.assertEqual('guest_boot_runtime' in compatible, 'guest_boot_runtime' in metadata)
                else:
                    with self.assertRaisesRegex(ValueError, 'guest_boot_runtime'):
                        UPDATE.compatibility(None, 'A', manifest, self.boot)

    def test_success_exact_manifest_complete_records_private_files(self):
        result = self.prepare()
        self.assertEqual((self.output / 'root.ext4').read_bytes(), self.content)
        self.assertEqual((self.output / 'manifest.json').read_bytes(), self.data)
        self.assertEqual(result['root'], UPDATE.record(self.content))
        selector = (self.output / 'selected.conf').read_bytes()
        self.assertEqual(UPDATE.SELECT.parse(selector)['SLOT'], 'A')
        self.assertEqual(result['selector'], UPDATE.record(selector))
        self.assertEqual(result['manifest']['sha256'], self.pin)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for path in self.output.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)
        self.assertEqual(json.loads((self.output / 'candidate.json').read_bytes()), result)

    def test_unauthenticated_manifest_never_parsed(self):
        self.pin = '0' * 64
        with mock.patch.object(UPDATE.json, 'loads', side_effect=AssertionError('parser reached')):
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                self.prepare()
        self.assert_clean()

    def test_duplicate_fields_at_any_depth_rejected(self):
        for data in (b'{"schema":1,"schema":1}', b'{"a":{"x":1,"x":2}}'):
            self.write_manifest(data)
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                self.prepare()
            self.assert_clean()

    def test_empty_oversize_symlink_nonregular_manifest(self):
        for content in (b'', b' ' * (UPDATE.LIMIT + 1)):
            self.write_manifest(content)
            with self.assertRaises(ValueError):
                self.prepare()
        self.manifest.unlink()
        self.manifest.symlink_to(self.image)
        with self.assertRaises(OSError):
            self.prepare()
        self.manifest.unlink()
        os.mkfifo(self.manifest)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assert_clean()

    def test_wrong_manifest_compatibility_rejected_before_image_parse(self):
        changes = [('slot_bytes', SIZE + 1), ('slot_bytes', True), ('slot_uuids', {}),
                   ('kernel_release', 'other'), ('board', 'RK3399'), ('schema', True),
                   ('management_runtime', {}), ('package_state', {})]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                original = self.value[key]
                self.value[key] = value
                self.write_manifest()
                with self.assertRaises(ValueError):
                    self.prepare(inspect=mock.Mock(side_effect=AssertionError('image parser reached')))
                self.value[key] = original
                self.assert_clean()

    def test_root_clock_and_profile_bindings_must_match_manifest(self):
        for name in ('userspace_profile', 'timesync_state'):
            with self.subTest(name=name):
                self.metadata['sources'][name] = {'forged': True}
                with self.assertRaises(ValueError):
                    self.prepare()
                self.metadata['sources'].pop(name)
                self.assert_clean()

    def test_time_roots_require_versioned_management_contract(self):
        self.value['sources'].update(userspace_profile={'profile': 'timesync-20261106'},
            timesync_state={'schema': 1, 'identity': 996, 'directory': 'var/lib/systemd/timesync',
                            'helper': {'bytes': 1, 'sha256': 'a' * 64}})
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'management profile'):
            self.prepare(inspect=mock.Mock(side_effect=AssertionError('image parser reached')))
        self.assert_clean()
        self.value['management_runtime']['profile'] = 'a733-management-v2-timesync'
        self.metadata = {key: copy.deepcopy(self.value[key])
                         for key in ('sources', 'management_runtime', 'package_state')}
        self.write_manifest()
        self.prepare()
        self.assertTrue(self.output.is_dir())

    def test_wrong_root_size_record(self):
        self.value['files']['root-A.ext4']['bytes'] = SIZE - 1
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'fixed slot'):
            self.prepare()
        self.assert_clean()

    def test_unknown_slot(self):
        with self.assertRaisesRegex(ValueError, 'slot'):
            UPDATE.validate_manifest(self.value, 'C', SIZE)

    def test_hash_mismatch_never_reaches_filesystem_parser(self):
        self.image.write_bytes(b'evil' + bytes(SIZE - 4))
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            self.prepare(inspect=mock.Mock(side_effect=AssertionError('image parser reached')))
        self.assert_clean()

    def test_short_long_symlink_fifo_directory_root(self):
        for length in (SIZE - 1, SIZE + 1):
            self.image.write_bytes(bytes(length))
            with self.assertRaisesRegex(ValueError, 'size'):
                self.prepare()
            self.assert_clean()
        self.image.unlink()
        self.image.symlink_to(self.manifest)
        with self.assertRaises(OSError):
            self.prepare()
        self.image.unlink()
        os.mkfifo(self.image)
        with self.assertRaises(ValueError):
            self.prepare()
        self.image.unlink()
        self.image.mkdir()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assert_clean()

    def test_gzip_explicitly_unsupported(self):
        self.image = self.parent / 'root-A.ext4.gz'
        self.image.write_bytes(self.content)
        with self.assertRaisesRegex(ValueError, 'gzip is unsupported'):
            self.prepare()
        self.assert_clean()

    def test_input_mutation_detected_even_if_original_bytes_read(self):
        original = self.image.stat()
        def mutate():
            self.image.write_bytes(self.content)
            # Some filesystems give consecutive writes the same timestamp.
            os.utime(self.image, ns=(original.st_atime_ns, original.st_mtime_ns + 10**9))
        with tempfile.TemporaryFile() as snapshot, self.assertRaisesRegex(ValueError, 'changed'):
            UPDATE.capture_root(self.image, UPDATE.record(self.content), snapshot, after_chunk=mutate)

    def test_snapshot_reauthenticated_before_inspect(self):
        original = UPDATE.WRITER.hash_stream
        calls = []
        def verify(stream, expected):
            calls.append('hash')
            original(stream, expected)
        def inspect(stream, slot, manifest):
            self.assertEqual(calls, ['hash'])
            return self.inspect(stream, slot, manifest)
        with mock.patch.object(UPDATE.WRITER, 'hash_stream', side_effect=verify):
            self.prepare(inspect=inspect)

    def test_wrong_boot_package_management_marker(self):
        for key, value in [('sources', {}), ('package_state', {}), ('management_runtime', {})]:
            old = self.metadata[key]
            self.metadata[key] = value
            with self.subTest(key=key), self.assertRaises((ValueError, KeyError)):
                self.prepare()
            self.metadata[key] = old
            self.assert_clean()

    def test_wrong_manifest_boot(self):
        self.value['sources'] = {**self.boot, 'Image': {'bytes': 2, 'sha256': 'c' * 64}}
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'boot stack'):
            self.prepare()
        self.assert_clean()

    def test_existing_file_directory_and_symlink_untouched(self):
        for kind in ('file', 'directory', 'symlink'):
            if kind == 'file':
                self.output.write_bytes(b'keep')
            elif kind == 'directory':
                self.output.mkdir()
            else:
                self.output.symlink_to('nonexistent')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                self.prepare()
            if kind == 'file':
                self.assertEqual(self.output.read_bytes(), b'keep')
            if kind == 'directory':
                self.output.rmdir()
            else:
                self.output.unlink()

    def test_publication_race_cannot_overwrite(self):
        def racing_publish(parent, old, new):
            self.output.mkdir()
            (self.output / 'keep').write_bytes(b'keep')
            UPDATE.rename_new(parent, old, new)
        with self.assertRaises(UPDATE.PublicationError) as raised:
            self.prepare(publish=racing_publish)
        self.assertFalse(raised.exception.published)
        self.assertEqual((self.output / 'keep').read_bytes(), b'keep')
        self.assertEqual(list(self.parent.glob('.host-update-*')), [])

    def test_parser_failure_cleans_owned_files(self):
        with self.assertRaisesRegex(ValueError, 'parse failed'):
            self.prepare(inspect=mock.Mock(side_effect=ValueError('parse failed')))
        self.assert_clean()

    def test_cleanup_does_not_follow_swapped_stage_name(self):
        replacement = self.parent / 'replacement'
        replacement.mkdir()
        (replacement / 'keep').write_bytes(b'keep')
        moved = self.parent / 'moved-stage'
        def swap_stage(stream, slot, manifest):
            stage = next(self.parent.glob('.host-update-*'))
            stage.rename(moved)
            stage.symlink_to(replacement, target_is_directory=True)
            raise ValueError('stage swapped')
        with self.assertRaisesRegex(ValueError, 'stage swapped'):
            self.prepare(inspect=swap_stage)
        self.assertEqual((replacement / 'keep').read_bytes(), b'keep')
        self.assertEqual(list(moved.iterdir()), [])
        self.assertTrue(next(self.parent.glob('.host-update-*')).is_symlink())

    def test_cleanup_preserves_replaced_file_and_original_error(self):
        def replace_root(stream, slot, manifest):
            stage = next(self.parent.glob('.host-update-*'))
            image = stage / 'root.ext4'
            image.unlink()
            image.write_bytes(b'foreign file')
            raise ValueError('image replaced')
        with self.assertRaisesRegex(ValueError, 'image replaced'):
            self.prepare(inspect=replace_root)
        stage = next(self.parent.glob('.host-update-*'))
        self.assertEqual((stage / 'root.ext4').read_bytes(), b'foreign file')

    def test_fsync_failure_before_publication_cleans(self):
        with mock.patch.object(UPDATE.os, 'fsync', side_effect=OSError('fsync failed')):
            with self.assertRaises(UPDATE.PublicationError) as raised:
                self.prepare()
        self.assertFalse(raised.exception.published)
        self.assert_clean()

    def test_parent_fsync_failure_retains_published_candidate(self):
        original = os.fsync
        def fail_parent(fd):
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == (self.parent.stat().st_dev, self.parent.stat().st_ino):
                raise OSError('parent durability failed')
            original(fd)
        with mock.patch.object(UPDATE.os, 'fsync', side_effect=fail_parent):
            with self.assertRaises(UPDATE.PublicationError) as raised:
                self.prepare()
        self.assertTrue(raised.exception.published)
        self.assertIn('durability=uncertain', str(raised.exception))
        self.assertTrue((self.output / 'candidate.json').is_file())

    def test_insufficient_space_before_capture(self):
        fake = mock.Mock(f_bavail=1, f_frsize=1)
        with mock.patch.object(UPDATE.os, 'fstatvfs', return_value=fake):
            with self.assertRaisesRegex(ValueError, 'insufficient'):
                self.prepare()
        self.assert_clean()

    def test_storage_guard_failure_before_output(self):
        with self.assertRaisesRegex(ValueError, 'MMC'):
            self.prepare(storage_guard=mock.Mock(side_effect=ValueError('MMC forbidden')))
        self.assert_clean()

    def test_no_cli_slot_size_override(self):
        with self.assertRaises(ValueError):
            UPDATE.validate_manifest(self.value, 'A')


class AncestorTests(unittest.TestCase):
    def test_trusted_root_parent_succeeds_without_policy_injection(self):
        with UPDATE.safe_parent(Path('/')) as fd:
            self.assertTrue(stat.S_ISDIR(os.fstat(fd).st_mode))
            self.assertEqual(os.fstat(fd).st_ino, os.stat('/').st_ino)

    def test_symlink_ancestor_rejected_before_leaf(self):
        # /proc/self is a symlink beneath root-owned non-writable ancestors.
        with self.assertRaises(OSError):
            with UPDATE.safe_parent(Path('/proc/self/root')):
                self.fail('symlink ancestor accepted')

    def test_symlink_and_writable_ancestors_rejected(self):
        # /tmp itself is insecure, regardless of a private leaf's permissions.
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, 'ancestors'):
                with UPDATE.safe_parent(Path(temporary)):
                    self.fail('insecure ancestor accepted')
        with self.assertRaises(ValueError):
            with UPDATE.safe_parent(Path('/a/../b')):
                self.fail('parent traversal accepted')

    def test_storage_mmc_and_mapper_dependency_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            self.addCleanup(os.close, fd)
            device = os.fstat(fd).st_dev
            mm = f'{os.major(device)}:{os.minor(device)}'
            node = root / 'mmcblk0'
            node.mkdir()
            (root / '179:0').symlink_to(node)
            mounts = f'1 0 {mm} / / rw - ext4 /dev/dm-0 rw\n'
            with mock.patch.object(UPDATE.ACQUIRE.WRITER, 'block_dependencies',
                                   return_value={mm, '179:0'}):
                # Add a regular NVMe-looking node for the mapper identity.
                nvme = root / 'dm-0'
                nvme.mkdir()
                (root / mm).symlink_to(nvme)
                with self.assertRaisesRegex(ValueError, 'SD/MMC'):
                    UPDATE.ACQUIRE.storage_guard(fd, 'external', mounts, root)


if __name__ == '__main__':
    unittest.main()

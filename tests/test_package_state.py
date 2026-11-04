"""Package-state fixtures use private regular-file trees, never mounts/devices."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('package_state', Path(__file__).resolve().parents[1] / 'configs/package_state.py')
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


def fixture(root):
    (root / PACKAGE.ROOT).parent.mkdir(parents=True)
    for name, (target, kind) in PACKAGE.COMPONENTS.items():
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == 'directory':
            path.mkdir()
            (path / 'record').write_bytes(name.encode())
        else:
            path.write_bytes(name.encode())
            path.chmod(0o644)
    (root / 'var/lib/dpkg/alternatives').mkdir()
    (root / 'var/lib/dpkg/alternatives/editor').write_bytes(b'complete alternatives db')
    (root / 'etc/alternatives').mkdir(parents=True)
    (root / 'etc/alternatives/editor').symlink_to('/usr/bin/editor')
    for path in root.rglob('*'):
        if path.is_dir():
            path.chmod(0o755)


class PackageStateTests(unittest.TestCase):
    def test_complete_snapshot_metadata_and_empty_mountpoints(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root)
            record = root / 'var/lib/dpkg/record'
            record.chmod(0o640)
            (record.parent / 'link').symlink_to('record')
            if os.geteuid() == 0:
                os.chown(record, 123, 456)
            owner = (record.stat().st_uid, record.stat().st_gid)
            inode = record.stat().st_ino
            os.link(record, record.parent / 'hardlink')
            profile = PACKAGE.snapshot(root)
            destination = root / PACKAGE.ROOT
            data = (destination / 'manifest.json').read_bytes()
            self.assertEqual(profile, {'profile': PACKAGE.PROFILE, 'manifest': {
                'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}})
            self.assertEqual((destination / 'FORMAT').read_bytes(), PACKAGE.FORMAT)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o755)
            for file in ('FORMAT', 'manifest.json'):
                self.assertEqual(stat.S_IMODE((destination / file).stat().st_mode), 0o444)
            manifest = json.loads(data)
            self.assertEqual(manifest['schema'], 1)
            self.assertEqual(set(manifest['components']), set(PACKAGE.COMPONENTS))
            for name, (target, kind) in PACKAGE.COMPONENTS.items():
                with self.subTest(name=name):
                    original = root / target
                    stored = destination / name
                    self.assertEqual(manifest['components'][name]['target'], target)
                    self.assertEqual(manifest['components'][name]['kind'], kind)
                    self.assertEqual(manifest['components'][name]['entries'], PACKAGE._entries(stored))
                    if kind == 'directory':
                        self.assertEqual(list(original.iterdir()), [])
                        self.assertEqual(stat.S_IMODE(original.stat().st_mode), 0o755)
                        self.assertEqual((stored / 'record').read_bytes(), name.encode())
                    else:
                        self.assertEqual(original.read_bytes(), b'')
                        self.assertEqual(stat.S_IMODE(original.stat().st_mode), 0o644)
                        self.assertEqual(stored.read_bytes(), name.encode())
                    self.assertEqual(original.stat().st_uid, os.geteuid())
            self.assertEqual((destination / 'dpkg/record').stat().st_ino, inode)
            self.assertEqual(((destination / 'dpkg/record').stat().st_uid,
                              (destination / 'dpkg/record').stat().st_gid), owner)
            self.assertEqual((destination / 'dpkg/hardlink').stat().st_ino, inode)
            self.assertEqual((destination / 'dpkg/record').stat().st_mode & 0o777, 0o640)
            self.assertEqual((destination / 'dpkg/link').readlink(), Path('record'))
            self.assertEqual((destination / 'dpkg/alternatives/editor').read_bytes(), b'complete alternatives db')
            self.assertEqual(os.readlink(root / 'etc/alternatives/editor'), '/usr/bin/editor')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                PACKAGE.snapshot(root)

    def test_preflight_rejects_before_any_move(self):
        for mutation in ('missing', 'wrong-kind', 'top-link', 'unsafe-mode', 'unsafe-parent',
                         'symlink-parent', 'fifo', 'escaping-link', 'absolute-link', 'cycle', 'destination'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                fixture(root)
                path = root / 'var/lib/systemd/catalog'
                if mutation == 'missing':
                    (path / 'record').unlink(); path.rmdir()
                elif mutation == 'wrong-kind':
                    (path / 'record').unlink(); path.rmdir(); path.write_bytes(b'wrong')
                elif mutation == 'top-link':
                    (path / 'record').unlink(); path.rmdir(); path.symlink_to('../pam')
                elif mutation == 'unsafe-mode':
                    path.chmod(0o777)
                elif mutation == 'unsafe-parent':
                    path.parent.chmod(0o777)
                elif mutation == 'symlink-parent':
                    path.parent.rename(path.parent.with_name('other'))
                    path.parent.symlink_to('other')
                elif mutation == 'fifo':
                    os.mkfifo(path / 'fifo')
                elif mutation == 'escaping-link':
                    (path / 'link').symlink_to('../../pam/record')
                elif mutation == 'absolute-link':
                    (path / 'link').symlink_to('/etc/passwd')
                elif mutation == 'cycle':
                    (path / 'link').symlink_to('link')
                else:
                    (root / PACKAGE.ROOT).symlink_to(root / 'elsewhere')
                with self.assertRaises((ValueError, FileNotFoundError)):
                    PACKAGE.snapshot(root)
                self.assertEqual((root / 'var/lib/dpkg/record').read_bytes(), b'dpkg')
                if mutation != 'destination':
                    self.assertFalse(os.path.lexists(root / PACKAGE.ROOT))

    def test_source_changed_after_preflight_is_not_moved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root)
            original_mkdir = Path.mkdir
            def mkdir(path, *args, **kwargs):
                result = original_mkdir(path, *args, **kwargs)
                if path == root / PACKAGE.ROOT:
                    source = root / 'var/lib/dpkg'
                    source.rename(source.with_name('saved-dpkg'))
                    source.symlink_to('saved-dpkg')
                return result
            with mock.patch.object(Path, 'mkdir', mkdir), self.assertRaises(ValueError):
                PACKAGE.snapshot(root)
            self.assertEqual((root / 'var/lib/saved-dpkg/record').read_bytes(), b'dpkg')
            self.assertFalse((root / PACKAGE.ROOT / 'dpkg').exists())

    def test_manifest_limit_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root)
            with mock.patch.object(PACKAGE, 'MANIFEST_LIMIT', 30), self.assertRaisesRegex(ValueError, 'limit'):
                PACKAGE.snapshot(root)
            self.assertFalse((root / PACKAGE.ROOT).exists())
            self.assertTrue((root / 'var/lib/dpkg/record').exists())

    def test_io_failure_refuses_retry_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root)
            with mock.patch.object(Path, 'rename', side_effect=OSError('fixture failure')):
                with self.assertRaisesRegex(OSError, 'fixture failure'):
                    PACKAGE.snapshot(root)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                PACKAGE.snapshot(root)
            self.assertEqual((root / 'var/lib/dpkg/record').read_bytes(), b'dpkg')

    def test_profile_exact_schema(self):
        good = {'profile': PACKAGE.PROFILE, 'manifest': {'bytes': 1, 'sha256': 'a' * 64}}
        self.assertEqual(PACKAGE.validate_profile(good), good)
        bad = [None, [], {}, {**good, 'extra': 1}, {**good, 'profile': 'old'}]
        for record in ({'bytes': 0, 'sha256': 'a'*64}, {'bytes': True, 'sha256': 'a'*64},
                       {'bytes': 1024**2+1, 'sha256': 'a'*64}, {'bytes': 1, 'sha256': 'A'*64},
                       {'bytes': 1, 'sha256': 'a'*63}, {**good['manifest'], 'extra': 1}):
            bad.append({**good, 'manifest': record})
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                PACKAGE.validate_profile(value)

    def test_manifest_authentication_duplicate_fields_and_descriptor_shape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root)
            profile = PACKAGE.snapshot(root)
            data = (root / PACKAGE.ROOT / 'manifest.json').read_bytes()
            good = PACKAGE.validate_manifest(data, profile)
            for changed in (data + b' ', data.replace(b'dpkg', b'dpkx', 1)):
                with self.assertRaisesRegex(ValueError, 'bytes/hash'):
                    PACKAGE.validate_manifest(changed, profile)
            with self.assertRaises(ValueError):
                PACKAGE.validate_manifest(data.decode(), profile)
            duplicate = data.replace(b'"schema":1', b'"schema":1,"schema":1')
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                PACKAGE.validate_manifest(duplicate, {'profile': PACKAGE.PROFILE,
                    'manifest': PACKAGE._record(duplicate)})
            mutations = []
            def mutate(change):
                value = copy.deepcopy(good)
                change(value)
                mutations.append(value)
            mutate(lambda value: value.update(schema=True))
            mutate(lambda value: value.update(profile='old'))
            mutate(lambda value: value.update(extra=1))
            mutate(lambda value: value['components'].pop('apt'))
            mutate(lambda value: value['components']['dpkg'].update(target='var/lib/other'))
            mutate(lambda value: value['components']['dpkg'].update(kind='file'))
            mutate(lambda value: value['components']['dpkg'].update(entries=[]))
            for field, content in [('path', '../escape'), ('path', 'a//b'), ('path', '/escape'),
                                   ('path', 'a/./b'), ('path', ''), ('type', 'device'),
                                   ('mode', '9999'), ('uid', True), ('gid', -1), ('extra', 1)]:
                mutate(lambda value, field=field, content=content:
                       value['components']['dpkg']['entries'][0].update({field: content}))
            mutate(lambda value: value['components']['dpkg']['entries'].append(
                copy.deepcopy(value['components']['dpkg']['entries'][0])))
            mutate(lambda value: value['components']['dpkg']['entries'].append({
                'path': 'missing/child', 'type': 'directory', 'mode': '0755', 'uid': 0, 'gid': 0}))
            for field, content in [('bytes', True), ('bytes', -1), ('sha256', 'bad')]:
                mutate(lambda value, field=field, content=content:
                       value['components']['shells']['entries'][0].update({field: content}))
            for target in ('../escape', '/escape', '', None):
                mutate(lambda value, target=target: value['components']['dpkg']['entries'].append({
                    'path': 'link', 'type': 'symlink', 'mode': '0777', 'uid': 0, 'gid': 0,
                    'link_target': target}))
            for index, value in enumerate(mutations):
                encoded = json.dumps(value).encode()
                with self.subTest(index=index), self.assertRaises(ValueError):
                    PACKAGE.validate_manifest(encoded, {'profile': PACKAGE.PROFILE,
                        'manifest': PACKAGE._record(encoded)})

    def test_prune_exact_files_records_and_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(PACKAGE.prune_caches(root), [])
            for relative in PACKAGE.CACHE_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode())
            keep = root / 'var/cache/apt/keep'
            keep.write_bytes(b'keep')
            for path in root.rglob('*'):
                if path.is_dir():
                    path.chmod(0o755)
            records = PACKAGE.prune_caches(root)
            self.assertEqual([r['path'] for r in records], list(PACKAGE.CACHE_FILES))
            for record in records:
                self.assertEqual(record['bytes'], len(record['path'].encode()))
                self.assertEqual(record['sha256'], hashlib.sha256(record['path'].encode()).hexdigest())
                self.assertFalse((root / record['path']).exists())
            self.assertEqual(keep.read_bytes(), b'keep')
            self.assertEqual(PACKAGE.prune_caches(root), [])

    def test_prune_rejects_all_candidates_before_deleting(self):
        for kind in ('link', 'parent-link', 'directory', 'fifo'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / 'var/cache/apt').mkdir(parents=True)
                first = root / PACKAGE.CACHE_FILES[0]
                first.write_bytes(b'keep on rejection')
                path = root / PACKAGE.CACHE_FILES[-1]
                path.parent.mkdir()
                for directory in root.rglob('*'):
                    if directory.is_dir():
                        directory.chmod(0o755)
                if kind == 'link':
                    path.symlink_to(first)
                elif kind == 'parent-link':
                    path.parent.rmdir(); path.parent.symlink_to('apt')
                elif kind == 'directory':
                    path.mkdir()
                else:
                    os.mkfifo(path)
                with self.assertRaises(ValueError):
                    PACKAGE.prune_caches(root)
                self.assertEqual(first.read_bytes(), b'keep on rejection')


if __name__ == '__main__':
    unittest.main()

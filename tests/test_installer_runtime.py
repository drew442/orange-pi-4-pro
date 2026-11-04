"""Runtime staging safety and dependency closure regressions; no target execution."""
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer_runtime', REPO / 'build/installer-runtime.py')
RUNTIME = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RUNTIME)


def elf(machine=b'\xb7\x00'):
    value = bytearray(64)
    value[:6] = b'\x7fELF\x02\x01'
    value[18:20] = machine
    return bytes(value)


def archive(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as target:
        for name, kind, value in entries:
            member = tarfile.TarInfo(name)
            if kind == 'file':
                member.size = len(value)
                member.mode = 0o644
                target.addfile(member, io.BytesIO(value))
            else:
                member.type = {'dir': tarfile.DIRTYPE, 'link': tarfile.SYMTYPE,
                               'hard': tarfile.LNKTYPE}[kind]
                member.linkname = value
                target.addfile(member)
    buffer.seek(0)
    return tarfile.open(fileobj=buffer, mode='r:')


def policy():
    return {'executables': ['usr/bin/python3.11'], 'required_trees': ['usr/lib/python3.11'],
            'optional_trees': ['usr/lib/aarch64-linux-gnu/ossl-modules'],
            'required_data': [], 'optional_data': [],
            'optional_libraries': ['libnss_files.so.2'],
            'library_directories': ['lib/aarch64-linux-gnu', 'usr/lib/aarch64-linux-gnu'],
            'loader': 'lib/ld-linux-aarch64.so.1',
            'symlinks': {'usr/bin/python3': 'python3.11'}}


def fixture():
    return [('lib', 'link', 'usr/lib'),
            ('usr/bin/python3.11', 'file', elf()),
            ('usr/bin/python3', 'link', 'python3.11'),
            ('usr/lib/python3.11', 'dir', ''),
            ('usr/lib/python3.11/os.py', 'file', b'pass\n'),
            ('usr/lib/python3.11/os_alias.py', 'link', 'os.py'),
            ('usr/lib/python3.11/lib-dynload/a.so', 'file', elf()),
            ('usr/lib/aarch64-linux-gnu/libc.so.6', 'file', elf()),
            ('usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1', 'file', elf()),
            ('usr/lib/aarch64-linux-gnu/libnss_files.so.2', 'file', elf()),
            ('usr/lib/aarch64-linux-gnu/ossl-modules', 'dir', ''),
            ('usr/lib/aarch64-linux-gnu/ossl-modules/legacy.so', 'file', elf()),
            ('usr/share/doc/python3.11/copyright', 'file', b'license'),
            ('usr/share/doc/python3', 'link', 'python3.11')]


class NamespaceTests(unittest.TestCase):
    def test_unsafe_namespace(self):
        cases = [[('../evil', 'file', b'x')], [('/evil', 'file', b'x')],
                 [('a', 'hard', '../b')], [('a', 'hard', '/b')], [('a', 'file', b'x'), ('./a', 'file', b'y')],
                 [('a', 'link', '../evil')],
                 [('a', 'link', 'b'), ('a/child', 'file', b'x')]]
        for entries in cases:
            with self.subTest(entries=entries), archive(entries) as source:
                with self.assertRaises(ValueError):
                    RUNTIME.Namespace(source)

    def test_aliases_cycles_and_missing(self):
        with archive(fixture()) as source:
            ns = RUNTIME.Namespace(source)
            self.assertEqual(ns.resolve('lib/aarch64-linux-gnu/libc.so.6'),
                             'usr/lib/aarch64-linux-gnu/libc.so.6')
            self.assertEqual(ns.read('usr/share/doc/python3/copyright')[1], b'license')
        with archive([('a', 'link', 'b'), ('b', 'link', 'a')]) as source:
            with self.assertRaisesRegex(ValueError, 'cycle'):
                RUNTIME.Namespace(source).resolve('a')
        with archive([('a', 'link', '/missing')]) as source:
            with self.assertRaisesRegex(ValueError, 'missing'):
                RUNTIME.Namespace(source).read('a')

    def test_hardlink_aliases_and_bounded_cycles(self):
        entries = fixture() + [('usr/bin/gzip', 'file', b'gzip bytes'),
                               ('usr/bin/gunzip', 'hard', './usr/bin/gzip'),
                               ('usr/lib/python3.11/hard.py', 'hard', 'usr/lib/python3.11/os_alias.py')]
        with archive(entries) as source, tempfile.TemporaryDirectory() as directory:
            ns = RUNTIME.Namespace(source)
            self.assertEqual(ns.read('usr/bin/gunzip')[1], b'gzip bytes')
            with patch.object(RUNTIME, 'elf_dependencies', return_value=([], [])):
                files, _ = RUNTIME.stage(ns, Path(directory), policy())
            path = Path(directory) / 'usr/lib/python3.11/hard.py'
            original = Path(directory) / 'usr/lib/python3.11/os.py'
            self.assertEqual(path.read_bytes(), original.read_bytes())
            self.assertNotEqual(path.stat().st_ino, original.stat().st_ino)
            self.assertEqual(files['usr/lib/python3.11/hard.py']['resolved_source'],
                             'usr/lib/python3.11/os.py')
        for entries in ([('a', 'link', 'a/x')],
                        [('a', 'hard', 'b'), ('b', 'link', 'a')],
                        [('a', 'hard', 'b'), ('b', 'hard', 'a')],
                        [('a', 'hard', 'b'), ('b', 'dir', '')]):
            with self.subTest(entries=entries), archive(entries) as source:
                with self.assertRaises(ValueError):
                    RUNTIME.Namespace(source)
        with archive([('a', 'hard', 'missing')]) as source:
            ns = RUNTIME.Namespace(source)
            with self.assertRaisesRegex(ValueError, 'missing'):
                ns.read('a')

    def test_closure_extensions_providers_and_notices(self):
        with archive(fixture()) as source, tempfile.TemporaryDirectory() as directory:
            with patch.object(RUNTIME, 'elf_dependencies', return_value=(['libc.so.6'],
                              ['/lib/ld-linux-aarch64.so.1'])) as inspect:
                files, closure = RUNTIME.stage(RUNTIME.Namespace(source), Path(directory), policy())
            self.assertIn('lib/aarch64-linux-gnu/libc.so.6', files)
            self.assertIn('lib/ld-linux-aarch64.so.1', files)
            self.assertIn('lib/aarch64-linux-gnu/libnss_files.so.2', closure)
            self.assertIn('usr/lib/python3.11/lib-dynload/a.so', closure)
            self.assertIn('usr/lib/aarch64-linux-gnu/ossl-modules/legacy.so', closure)
            self.assertIn('usr/share/doc/python3/copyright', files)
            self.assertEqual(files['usr/lib/python3.11/os_alias.py']['sha256'],
                             files['usr/lib/python3.11/os.py']['sha256'])
            self.assertEqual((Path(directory) / 'usr/bin/python3').readlink(), Path('python3.11'))
            self.assertEqual(files['usr/bin/python3.11']['mode'], 0o755)
            self.assertGreaterEqual(inspect.call_count, 6)

    def test_bad_extension_and_ambiguous_provider(self):
        entries = fixture() + [('lib64', 'dir', ''), ('lib64/libc.so.6', 'file', b'different')]
        selected = policy()
        selected['library_directories'].append('lib64')
        with archive(entries) as source, tempfile.TemporaryDirectory() as directory:
            with patch.object(RUNTIME, 'elf_dependencies', return_value=(['libc.so.6'], [])):
                with self.assertRaisesRegex(ValueError, 'ambiguous ELF dependency'):
                    RUNTIME.stage(RUNTIME.Namespace(source), Path(directory), selected)
        entries = [(name, kind, b'invalid' if '/lib-dynload/' in name else value)
                   for name, kind, value in fixture()]
        with archive(entries) as source, tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'AArch64'):
                # The last queued provider is valid; parse all headers, without readelf.
                def inspect(data, scratch):
                    if data != elf():
                        raise ValueError('not ELF64 little-endian AArch64')
                    return [], []
                with patch.object(RUNTIME, 'elf_dependencies', side_effect=inspect):
                    RUNTIME.stage(RUNTIME.Namespace(source), Path(directory), policy())

    def test_missing_transitive_dependency(self):
        with archive(fixture()) as source, tempfile.TemporaryDirectory() as directory:
            with patch.object(RUNTIME, 'elf_dependencies', return_value=(['absent.so'], [])):
                with self.assertRaisesRegex(ValueError, 'missing ELF dependency'):
                    RUNTIME.stage(RUNTIME.Namespace(source), Path(directory), policy())

    def test_elf_architecture_and_search_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory) / 'elf'
            for data in (b'bad', elf(b'\x3e\x00')):
                with self.assertRaisesRegex(ValueError, 'AArch64'):
                    RUNTIME.elf_dependencies(data, scratch)
            result = type('Result', (), {'stdout': '(RUNPATH) [evil]'})()
            with patch.object(RUNTIME.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(ValueError, 'search path'):
                    RUNTIME.elf_dependencies(elf(), scratch)
            result.stdout = '(NEEDED) Shared library: [libc.so.6]\n[Requesting program interpreter: /lib/ld-linux-aarch64.so.1]'
            with patch.object(RUNTIME.subprocess, 'run', return_value=result):
                self.assertEqual(RUNTIME.elf_dependencies(elf(), scratch),
                                 (['libc.so.6'], ['/lib/ld-linux-aarch64.so.1']))
            self.assertFalse(scratch.exists())

    def test_output_guard(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(RUNTIME, 'REPO', Path(directory)):
            parent = Path(directory) / 'out'
            parent.mkdir()
            valid = parent / 'installer-runtime-test'
            self.assertEqual(RUNTIME.validate_output(valid), valid)
            for path in (Path('out/installer-runtime-test'), parent / 'other', parent / 'installer-runtime-'):
                with self.assertRaises(ValueError):
                    RUNTIME.validate_output(path)
            valid.mkdir()
            with self.assertRaises(ValueError):
                RUNTIME.validate_output(valid)
            valid.rmdir()
            parent.rmdir()
            parent.symlink_to('/tmp')
            with self.assertRaises(ValueError):
                RUNTIME.validate_output(valid)

    def test_capture_and_expansion_caps(self):
        import gzip
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = gzip.compress(b'tar fixture')
            captured = root / 'captured.gz'
            RUNTIME.capture_archive(io.BytesIO(data), captured, RUNTIME.record(data))
            self.assertEqual(captured.read_bytes(), data)
            self.assertEqual(captured.stat().st_mode & 0o777, 0o600)
            self.assertEqual(RUNTIME.decompress_archive(captured, root / 'tar'), 11)
            self.assertEqual((root / 'tar').read_bytes(), b'tar fixture')
            for bad in (data[:-1], data + b'excess', bytes(len(data))):
                target = root / 'bad.gz'
                with self.assertRaisesRegex(ValueError, 'archive'):
                    RUNTIME.capture_archive(io.BytesIO(bad), target, RUNTIME.record(data))
                target.unlink()
            with self.assertRaisesRegex(ValueError, 'expansion cap'):
                RUNTIME.decompress_archive(captured, root / 'bomb', limit=4)
            self.assertLessEqual((root / 'bomb').stat().st_size, 4)

    def test_hash_and_storage_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'configs').mkdir()
            (root / 'out').mkdir()
            config = {'source_evidence': 'evidence.json', 'archive': RUNTIME.record(b'good!')}
            import json
            (root / 'configs/installer-runtime-policy.json').write_text(json.dumps(config))
            (root / 'evidence.json').write_text(json.dumps({'files': {'rootfs.tar.gz': config['archive']}}))
            source = root / 'archive.tar'
            source.write_bytes(b'wrong')
            output = root / 'out/installer-runtime-test'
            with patch.object(RUNTIME, 'REPO', root), patch.object(RUNTIME, 'builder_tools', return_value={}):
                with patch.object(RUNTIME.STATE, 'validate_workspace_storage', side_effect=ValueError('SD refused')):
                    with self.assertRaisesRegex(ValueError, 'SD refused'):
                        RUNTIME.package(source, output)
                self.assertFalse(output.exists())
                with patch.object(RUNTIME.STATE, 'validate_workspace_storage', return_value={}), \
                     patch.object(RUNTIME.tarfile, 'open') as parse:
                    with self.assertRaisesRegex(ValueError, 'hash/size mismatch'):
                        RUNTIME.package(source, output)
                    parse.assert_not_called()
                self.assertTrue(output.is_dir())
                self.assertFalse((output / 'runtime-manifest.json').exists())
                self.assertFalse((output / 'root').exists())


if __name__ == '__main__':
    unittest.main()

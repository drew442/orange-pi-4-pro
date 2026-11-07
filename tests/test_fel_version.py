"""Offline FEL frontend and recipe guards; no USB or physical acceptance."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fel_version_recipe', REPO / 'build/fel-version.py')
recipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)

HEADER = '''
#include <stdint.h>
struct aw_fel_version {
 char signature[8]; uint32_t soc_id, unknown_0a; uint16_t protocol;
 uint8_t unknown_12, unknown_13; uint32_t scratchpad, pad[2];
};
typedef struct { struct aw_fel_version soc_version; } feldev_handle;
feldev_handle *feldev_open(int, int, uint16_t, uint16_t);
void feldev_done(feldev_handle *);
'''
STUB = '''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fel_lib.h"
static feldev_handle dev;
feldev_handle *feldev_open(int bus, int device, uint16_t vendor, uint16_t product) {
 fprintf(stderr, "OPEN %d %d %04x %04x\\n", bus, device, vendor, product);
 if (getenv("NULL_OPEN")) return NULL;
 memcpy(dev.soc_version.signature, getenv("BAD_MAGIC") ? "INVALID!" : "AWUSBFEX", 8);
 dev.soc_version.soc_id = getenv("BAD_SOC") ? 0x1851 : 0x1903;
 dev.soc_version.protocol = 1;
 dev.soc_version.scratchpad = 0x12345678;
 return &dev;
}
void feldev_done(feldev_handle *handle) {
 if (handle != &dev) abort();
 fputs("CLOSE\\n", stderr);
}
'''


class FrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc')
        if not compiler:
            raise unittest.SkipTest('C compiler unavailable for offline stub tests')
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        (root / 'fel_lib.h').write_text(HEADER)
        (root / 'stub.c').write_text(STUB)
        cls.binary = root / 'fel-version'
        subprocess.run([compiler, '-std=gnu99', '-Wall', '-Wextra', '-Werror',
                        '-I' + str(root), str(REPO / 'build/fel-version.c'),
                        str(root / 'stub.c'), '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def invoke(self, args, **env):
        return subprocess.run([str(self.binary), *args], env={'PATH': '/usr/bin:/bin', **env},
                              text=True, capture_output=True, timeout=5)

    def test_help_never_opens_usb(self):
        result = self.invoke(['--help'])
        self.assertEqual(result.returncode, 0)
        self.assertIn('Usage:', result.stdout)
        self.assertNotIn('OPEN', result.stderr)

    def test_invalid_arguments_never_open_usb(self):
        invalid = [[], ['1'], ['1', '2', '3'], ['--help', '1'], ['version', '1'],
                   ['1', 'read'], ['1', '2', 'write'], ['1', '0'], ['256', '1'],
                   ['1', '128'], ['00', '1'], ['01', '1'], ['1', '01'], ['+1', '1'],
                   ['-1', '1'], [' 1', '1'], ['1 ', '1'], ['', '1'], ['0x1', '1'],
                   ['1', '1\n'], ['9' * 100, '1'], ['１', '1']]
        for args in invalid:
            with self.subTest(args=args):
                result = self.invoke(args)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn('OPEN', result.stderr)
                self.assertNotIn('CLOSE', result.stderr)

    def test_exact_device_and_decoded_version(self):
        for args in (['1', '1'], ['255', '127']):
            with self.subTest(args=args):
                result = self.invoke(args)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr, f'OPEN {args[0]} {args[1]} 1f3a efe8\nCLOSE\n')
                self.assertIn('signature=AWUSBFEX soc_id=0x1903', result.stdout)
                self.assertIn('scratchpad=0x12345678', result.stdout)

    def test_mismatched_version_closes_and_fails(self):
        for env, message in (({'BAD_MAGIC': '1'}, 'signature'), ({'BAD_SOC': '1'}, 'SoC ID')):
            with self.subTest(env=env):
                result = self.invoke(['7', '13'], **env)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertEqual(result.stderr.count('OPEN '), 1)
                self.assertEqual(result.stderr.count('CLOSE\n'), 1)
                self.assertEqual(result.stdout, '')

    def test_failed_open(self):
        result = self.invoke(['7', '13'], NULL_OPEN='1')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('CLOSE', result.stderr)


class RecipeGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args],
                                       env={'PATH': '/usr/bin:/bin', 'HOME': str(self.root)}, text=True).strip()

    def source_fixture(self):
        self.git('init', '-q')
        self.git('config', 'user.name', 'Offline Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.root / 'fel_lib.c').write_text('fixture\n')
        self.git('add', 'fel_lib.c')
        self.git('commit', '-qm', 'Fixture')
        return self.git('rev-parse', 'HEAD')

    def test_clean_pinned_source_and_dirty_rejection(self):
        pin = self.source_fixture()
        with mock.patch.object(recipe, 'COMMIT', pin):
            hashes = recipe.check_source(self.root)
            self.assertEqual(hashes, {'fel_lib.c': recipe.digest(b'fixture\n')})
            (self.root / 'fel_lib.c').write_text('modified\n')
            with self.assertRaisesRegex(ValueError, 'clean'):
                recipe.check_source(self.root)

    def test_wrong_pin_and_untracked_source_rejected(self):
        pin = self.source_fixture()
        with self.assertRaisesRegex(ValueError, 'HEAD'):
            recipe.check_source(self.root)
        (self.root / 'injected.h').write_text('bad\n')
        with mock.patch.object(recipe, 'COMMIT', pin):
            with self.assertRaisesRegex(ValueError, 'clean'):
                recipe.check_source(self.root)

    def test_symlinked_source_and_nonregular_input_rejected(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'physical'):
            recipe.check_source(alias)
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, 'regular'):
            recipe.regular(fifo)

    def test_fresh_scoped_output(self):
        (self.root / 'out').mkdir()
        with mock.patch.object(recipe, 'REPO', self.root):
            target = self.root / 'out/fel-version-test'
            self.assertEqual(recipe.check_output(target), target)
            for invalid in (self.root / 'fel-version-test', self.root / 'out/other',
                            self.root / 'out/fel-version-'):
                with self.assertRaisesRegex(ValueError, 'output'):
                    recipe.check_output(invalid)
            target.mkdir()
            with self.assertRaisesRegex(ValueError, 'already exists'):
                recipe.check_output(target)

    def test_symlinked_output_parent_rejected(self):
        (self.root / 'actual').mkdir()
        (self.root / 'out').symlink_to(self.root / 'actual', target_is_directory=True)
        with mock.patch.object(recipe, 'REPO', self.root):
            with self.assertRaisesRegex(ValueError, 'physical'):
                recipe.check_output(self.root / 'out/fel-version-test')

    def test_forbidden_symbols_and_libraries_rejected(self):
        good = '  00000000 T feldev_open\n'
        dynamic = '(NEEDED) Shared library: [libusb-1.0.so.0]\n(NEEDED) Shared library: [libc.so.6]\n'
        with mock.patch.object(recipe, 'run', side_effect=[good, dynamic]):
            self.assertEqual(set(recipe.check_symbols(Path('fake'))[2]), {'libusb-1.0.so.0', 'libc.so.6'})
        for symbol in ('aw_fel_write', 'aw_fel_execute', 'fel_writel_n', 'fel_read_sid',
                       'list_fel_devices', 'aw_fel_read', 'spi_flash_write'):
            with self.subTest(symbol=symbol), mock.patch.object(recipe, 'run', return_value=f'0 T {symbol}\n'):
                with self.assertRaisesRegex(ValueError, 'forbidden'):
                    recipe.check_symbols(Path('fake'))
        with mock.patch.object(recipe, 'run', side_effect=[good, dynamic + '(NEEDED) [libevil.so]\n']):
            with self.assertRaisesRegex(ValueError, 'runtime'):
                recipe.check_symbols(Path('fake'))

    def test_compiler_uses_both_upstream_header_directories(self):
        output = self.root / 'out/fel-version-fixture'
        source = output / 'source'
        command = recipe.compiler_command(output, source, ['-I/usr/include/libusb-1.0', '-lusb-1.0'])
        self.assertIn('-I' + str(source), command)
        self.assertIn('-I' + str(source / 'include'), command)
        self.assertNotIn(str(source / 'fel.c'), command)

    def test_failed_compiler_diagnostics_are_retained(self):
        log = self.root / 'compiler.log'
        error = subprocess.CalledProcessError(1, ['compiler'], output=b'', stderr=b'missing header')
        with mock.patch.object(recipe.subprocess, 'run', side_effect=error):
            with self.assertRaises(subprocess.CalledProcessError):
                recipe.run(['compiler'], diagnostics=log)
        self.assertIn(b'missing header', log.read_bytes())

    def test_wrong_builder_package_refused(self):
        def fake_run(args, **kwargs):
            if '--print-architecture' in args:
                return 'amd64\n'
            return 'wrong-version'
        with mock.patch.object(Path, 'read_text', return_value='ID=ubuntu\nVERSION_ID="22.04"\n'), \
             mock.patch.object(recipe, 'run', side_effect=fake_run):
            with self.assertRaisesRegex(ValueError, 'package differs'):
                recipe.check_builder()


if __name__ == '__main__':
    unittest.main()

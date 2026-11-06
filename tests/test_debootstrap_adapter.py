"""No mounts, chroots, device creation or package scripts in these fixtures."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('debootstrap_adapter', Path(__file__).resolve().parents[1] / 'build/debootstrap-adapter.py')
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.pins = {}
        self.data = {}
        for name in ADAPTER.SOURCES:
            data = ('# fixture ' + name + '\n').encode()
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.pins[name] = len(data), hashlib.sha256(data).hexdigest()
            self.data[name] = data

    def prepare(self, output=None):
        with patch.object(ADAPTER, 'SOURCES', self.pins):
            return ADAPTER.prepare(output or self.base / 'output', self.source)

    def shell(self, body, *, shell='/bin/dash', override=None):
        result = subprocess.run([shell, '-c', (override or ADAPTER.OVERRIDE.decode()) + '\n' + body],
                                text=True, capture_output=True)
        return result

    def status_shell(self, tail='', pid='$$', nnp='1', cap='0000000000000000', extra=''):
        path = self.base / 'status'
        # Fixture only: production has no configurable status path.
        override = ADAPTER.OVERRIDE.decode().replace('< /proc/self/status', '< "' + str(path) + '"')
        data = 'Pid: ' + pid + '\nNoNewPrivs: ' + nnp + '\n'
        for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
            data += key + ': ' + cap + '\n'
        body = "cat > '" + str(path) + "' <<EOF\n" + data + extra + '\nEOF\na733_verify_status\n' + tail
        return self.shell(body, override=override)

    def test_stock_bytes_and_manifest(self):
        manifest = self.prepare()
        output = self.base / 'output'
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        self.assertEqual(os.readlink(output / 'scripts/bookworm'), 'sid')
        self.assertEqual(json.loads((output / 'manifest.json').read_text()), manifest)
        for name, record in manifest['sources'].items():
            expected = self.data[name] + (ADAPTER.OVERRIDE if name.endswith('/functions') else b'')
            actual = (output / record['output']).read_bytes()
            self.assertEqual(actual, expected)
            self.assertEqual(hashlib.sha256(actual).hexdigest(), record['output_sha256'])
        self.assertTrue(os.access(output / 'debootstrap', os.X_OK))
        self.assertEqual(set(ADAPTER.DEVICES), {'null', 'zero', 'full', 'random', 'urandom', 'tty', 'console', 'ptmx'})

    def test_source_authentication_before_output(self):
        path = self.source / next(iter(self.pins))
        path.write_bytes(b'x' * self.pins[next(iter(self.pins))][0])
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.prepare()
        self.assertFalse((self.base / 'output').exists())

    def test_source_symlink_and_fifo_rejected(self):
        path = self.source / next(iter(self.pins))
        path.unlink()
        path.symlink_to('/dev/null')
        with self.assertRaises(OSError):
            self.prepare()
        path.unlink()
        os.mkfifo(path)
        with self.assertRaisesRegex(ValueError, 'type/size'):
            self.prepare()

    def test_symlink_ancestor_rejected(self):
        (self.base / 'linked').symlink_to(self.source, target_is_directory=True)
        with patch.object(ADAPTER, 'SOURCES', self.pins), self.assertRaises(OSError):
            ADAPTER.prepare(self.base / 'output', self.base / 'linked')
        (self.base / 'parent').symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(OSError):
            self.prepare(self.base / 'parent' / 'output')

    def test_overlap_and_existing_output_rejected(self):
        for output in (self.source, self.source / 'new', self.base):
            with self.assertRaises(ValueError):
                self.prepare(output)
        output = self.base / 'output'
        output.mkdir()
        with self.assertRaises(FileExistsError):
            self.prepare(output)
        output.rmdir()
        output.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            self.prepare(output)

    def test_ambiguous_path_spellings_rejected_before_normalization(self):
        for output, source in ((Path('relative-output'), self.source),
                               (self.base / 'output', Path('relative-source')),
                               (self.base / 'nested/../output', self.source),
                               (self.base / 'output', self.source / 'nested/..')):
            with patch.object(ADAPTER, '_mountinfo') as topology:
                with self.assertRaisesRegex(ValueError, 'absolute.*parent traversal'):
                    ADAPTER.prepare(output, source)
                topology.assert_not_called()
        self.assertFalse((self.base / 'output').exists())

    def test_bind_aliases_rejected_before_output_creation(self):
        # Synthetic mountinfo only: source and output spellings differ but
        # output is a bind alias below the protected source directory.
        mounts = ('1 0 8:1 / / rw - ext4 none rw\n'
                  f'2 1 8:1 /frozen {self.source} rw - ext4 none rw\n'
                  f'3 1 8:1 /frozen/usr/share/debootstrap {self.base} rw - ext4 none rw\n')
        with patch.object(ADAPTER, '_mountinfo', return_value=mounts):
            with self.assertRaisesRegex(ValueError, 'filesystem alias'):
                self.prepare()
        self.assertFalse((self.base / 'output').exists())

    def test_default_root_protects_sources_but_allows_separate_output_bind(self):
        mounts = ('1 0 8:1 / / rw - ext4 none rw\n'
                  '2 1 8:1 /usr/share/debootstrap /dir-alias rw - ext4 none rw\n'
                  '3 1 8:1 /usr/sbin/debootstrap /file-alias rw - ext4 none rw\n'
                  '4 1 8:1 /export /a733-export-output rw - ext4 none rw\n'
                  '5 1 8:1 /usr/share/debootstrap /alias\\040space rw - ext4 none rw\n')
        with patch.object(ADAPTER, '_mountinfo', return_value=mounts):
            for output in ('/dir-alias/new', '/file-alias', '/alias space/new'):
                with self.assertRaisesRegex(ValueError, 'filesystem alias'):
                    ADAPTER._guard_overlap(Path(output), Path('/'))
            ADAPTER._guard_overlap(Path('/a733-export-output/adapter'), Path('/'))

    def test_nondefault_source_root_is_entirely_protected_across_aliases(self):
        mounts = ('1 0 8:1 / / rw - ext4 none rw\n'
                  '2 1 8:2 /frozen /source rw - ext4 none rw\n'
                  '3 1 8:2 /frozen /alias rw - ext4 none rw\n')
        with patch.object(ADAPTER, '_mountinfo', return_value=mounts):
            with self.assertRaisesRegex(ValueError, 'filesystem alias'):
                ADAPTER._guard_overlap(Path('/alias/unrelated-directory/new'), Path('/source'))

    def test_pseudo_filesystem_rejected(self):
        with self.assertRaisesRegex(ValueError, 'pseudo/memory'):
            ADAPTER._directory(Path('/proc'))

    def test_cli_has_no_pin_relaxation(self):
        result = subprocess.run(['python3', str(SPEC.origin), '--output', str(self.base / 'output'),
                                 '--source-root', str(self.source)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.base / 'output').exists())

    def test_shell_syntax(self):
        result = subprocess.run(['/bin/dash', '-n'], input=ADAPTER.OVERRIDE, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_status_original_shell_pid_and_caps(self):
        self.assertEqual(self.status_shell().returncode, 0)
        for kwargs in ({'pid': '999999999'}, {'nnp': '0'}, {'cap': '000000000a631000'},
                       {'cap': 'bad'}, {'extra': 'CapEff: 0000000000000000\n'}):
            self.assertNotEqual(self.status_shell(**kwargs).returncode, 0, kwargs)
        for bit in (12, 16, 17, 21, 22, 25, 27):
            self.assertNotEqual(self.status_shell(cap=f'{1 << bit:016x}').returncode, 0, bit)

    def test_phase_separation(self):
        phase = 'am_doing_phase () { case " $WHAT_TO_DO " in *" $1 "*) return 0;; *) return 1;; esac; }\n'
        base = phase + 'ARCH=arm64 HOST_ARCH=arm64 HOST_OS=arm64\n'
        for target, work, okay in [('/a733-export-output/arm-root', 'first_stage', True),
                                   ('/', 'second_stage', True), ('/', 'first_stage', False),
                                   ('/a733-export-output/arm-root', 'second_stage', False),
                                   ('/elsewhere', 'first_stage', False),
                                   ('/', 'first_stage second_stage', False)]:
            result = self.shell(base + f"TARGET='{target}' WHAT_TO_DO='{work}' HOST_ARCH={'arm64' if target == '/' else 'amd64'} HOST_OS={'arm64' if target == '/' else 'amd64'}\na733_phase")
            self.assertEqual(result.returncode == 0, okay, (target, work))
        self.assertNotEqual(self.shell(base + "TARGET=/ WHAT_TO_DO=second_stage ARCH=amd64\na733_phase").returncode, 0)
        self.assertNotEqual(self.shell(base + "TARGET=/ WHAT_TO_DO=second_stage VARIANT=fakechroot\na733_phase").returncode, 0)

    def mounts(self, change=None):
        records = [('/proc', 'proc'), ('/proc/sys', 'proc'), ('/sys', 'tmpfs')]
        records += [('/sys/' + name, 'sysfs') for name in ('block', 'bus', 'class', 'dev', 'devices', 'kernel')]
        records += [('/sys/firmware', 'tmpfs')]
        records += [(name, 'tmpfs') for name in ('/proc/kcore', '/proc/keys', '/proc/kallsyms', '/proc/sysrq-trigger', '/proc/timer_list') if os.path.lexists(name)]
        lines = []
        for i, (path, kind) in enumerate(records):
            opts, optional = 'ro,nosuid,nodev,noexec', ''
            if change:
                path, kind, opts, optional = change(i, path, kind, opts, optional)
            lines.append(f'{i+1} 1 0:1 / {path} {opts} {optional}- {kind} none rw\n')
        return ''.join(lines)

    def check_mounts(self, data):
        path = self.base / 'mountinfo'
        path.write_text(data)
        override = ADAPTER.OVERRIDE.decode().replace('< /proc/self/mountinfo', '< "' + str(path) + '"')
        return self.shell('a733_verify_mounts', override=override)

    def test_mount_restrictions_and_filtered_layout(self):
        self.assertEqual(self.check_mounts(self.mounts()).returncode, 0)
        for omitted in ('ro', 'nosuid', 'nodev', 'noexec'):
            data = self.mounts(lambda i, p, k, o, x: (p, k, o.replace(omitted, 'bad') if i == 1 else o, x))
            self.assertNotEqual(self.check_mounts(data).returncode, 0)
        for optional in ('shared:1 ', 'master:2 ', 'propagate_from:3 '):
            self.assertNotEqual(self.check_mounts(self.mounts(lambda i, p, k, o, x: (p, k, o, optional))).returncode, 0)
        self.assertNotEqual(self.check_mounts(self.mounts().replace('/sys/devices ', '/sys/other ')).returncode, 0)
        self.assertNotEqual(self.check_mounts(self.mounts().replace('- proc ', '- tmpfs ', 1)).returncode, 0)
        self.assertNotEqual(self.check_mounts(self.mounts() + self.mounts().splitlines()[0] + '\n').returncode, 0)

    def test_regular_files_never_count_as_devices(self):
        # Absolute production path is redirected only in this fixture.
        dev = self.base / 'dev'
        dev.mkdir()
        for name in ADAPTER.DEVICES:
            (dev / name).touch(mode=0o666)
        override = ADAPTER.OVERRIDE.decode().replace('a733_path=/dev/', 'a733_path=' + str(dev) + '/')
        self.assertNotEqual(self.shell('a733_verify_devices', override=override).returncode, 0)

    def foreign_fixture(self):
        target = self.base / 'arm-root'
        target.mkdir()
        override = ADAPTER.OVERRIDE.decode().replace('/a733-export-output/arm-root', str(target))
        # Test-only substitutions model namespace-root ownership without mounts.
        override = override.replace("'0:0:755'", f"'{os.geteuid()}:{os.getegid()}:755'")
        override = override.replace("'0:0:0:666:1'", f"'{os.geteuid()}:{os.getegid()}:0:666:1'")
        override = override.replace('[ "$(id -u)" = 0 ]', f'[ "$(id -u)" = {os.geteuid()} ]')
        override = override.replace('[ "$(id -g)" = 0 ]', f'[ "$(id -g)" = {os.getegid()} ]')
        prefix = f'TARGET={target} ARCH=arm64 HOST_ARCH=amd64 HOST_OS=amd64\nam_doing_phase () {{ [ "$1" = first_stage ]; }}\n'
        return target, override, prefix

    def test_foreign_execution_and_fail_closed_cleanup(self):
        target, override, prefix = self.foreign_fixture()
        # Status proof has separate original-shell fixtures above.
        prefix += 'a733_verify_status () { return 0; }\n'
        result = self.shell(prefix + 'check_sane_mount "$TARGET"', override=override)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(target.iterdir()), [])
        self.assertNotEqual(self.shell(prefix + 'chmod () { return 0; }; check_sane_mount "$TARGET"', override=override).returncode, 0)
        self.assertEqual(list(target.iterdir()), [])
        self.assertNotEqual(self.shell(prefix + 'rm () { return 1; }; check_sane_mount "$TARGET"', override=override).returncode, 0)
        self.assertEqual(len(list(target.iterdir())), 1)

    def test_foreign_rejects_unsafe_existing_paths_without_overwrite(self):
        target, override, prefix = self.foreign_fixture()
        dev = target / 'dev'
        dev.mkdir(mode=0o755)
        for kind in ('empty', 'nonempty', 'symlink', 'directory'):
            path = dev / 'zero'
            if kind == 'empty':
                path.touch()
            elif kind == 'nonempty':
                path.write_text('keep me')
            elif kind == 'symlink':
                path.symlink_to('/dev/null')
            else:
                path.mkdir()
            before = path.lstat()
            result = self.shell(prefix + 'setup_devices', override=override)
            self.assertNotEqual(result.returncode, 0, kind)
            self.assertEqual(path.lstat(), before)
            self.assertFalse((dev / 'null').exists())
            if kind == 'directory':
                path.rmdir()
            else:
                path.unlink()
        dev.rmdir()
        dev.symlink_to(self.base, target_is_directory=True)
        self.assertNotEqual(self.shell(prefix + 'setup_devices', override=override).returncode, 0)

    def test_wrong_character_device_numbers_and_symlinks(self):
        # Real character handle, but zero must not accept null's major/minor.
        override = ADAPTER.OVERRIDE.decode().replace('a733_path=/dev/$a733_name', 'a733_path=/dev/null')
        self.assertNotEqual(self.shell('a733_verify_devices', override=override).returncode, 0)
        if Path('/dev/ptmx').is_symlink():
            override = ADAPTER.OVERRIDE.decode().replace('a733_path=/dev/$a733_name', 'a733_path=/dev/ptmx')
            self.assertNotEqual(self.shell('a733_verify_devices', override=override).returncode, 0)

    def test_api_hidden_firmware_network_and_masks(self):
        root = self.base / 'api'
        net = root / 'sys/class/net'
        firmware = root / 'sys/firmware'
        net.mkdir(parents=True)
        firmware.mkdir()
        (net / 'lo').mkdir()
        (root / 'proc').mkdir()
        mask = root / 'proc/kcore'
        mask.touch(mode=0)
        override = ADAPTER.OVERRIDE.decode().replace('/sys/', str(root / 'sys') + '/').replace('/proc/', str(root / 'proc') + '/')
        body = 'a733_verify_mounts () { return 0; }; a733_verify_api'
        self.assertEqual(self.shell(body, override=override).returncode, 0)
        (firmware / '.hidden').touch()
        self.assertNotEqual(self.shell(body, override=override).returncode, 0)
        (firmware / '.hidden').unlink()
        (net / 'eth0').mkdir()
        self.assertNotEqual(self.shell(body, override=override).returncode, 0)
        (net / 'eth0').rmdir()
        mask.chmod(0o644)
        self.assertNotEqual(self.shell(body, override=override).returncode, 0)

    def test_second_stage_refuses_unready_devices_before_execution(self):
        body = ('TARGET=/ ARCH=arm64 HOST_ARCH=arm64 HOST_OS=arm64\n'
                'am_doing_phase () { [ "$1" = second_stage ]; }\n'
                'a733_verify_status () { return 0; }\n'
                'a733_verify_devices () { return 1; }\n'
                'mktemp () { exit 99; }\n')
        for operation in ('check_sane_mount /', 'setup_proc'):
            result = self.shell(body + operation)
            self.assertEqual(result.returncode, 1)

    def test_foreign_placeholders_exclusive_and_no_readiness_claim(self):
        target, override, prefix = self.foreign_fixture()
        result = self.shell(prefix + 'setup_devices', override=override)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ADAPTER.DEVICES:
            self.assertTrue((target / 'dev' / name).is_file())
            self.assertEqual((target / 'dev' / name).stat().st_size, 0)
            self.assertEqual((target / 'dev' / name).stat().st_mode & 0o777, 0o666)
        self.assertNotEqual(self.shell(prefix + 'setup_devices', override=override).returncode, 0)
        self.assertNotEqual(self.shell(prefix + 'a733_verify_devices', override=override).returncode, 0)


if __name__ == '__main__':
    unittest.main()

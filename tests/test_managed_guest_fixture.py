"""Managed fixture host checks; no host poweroff, ARM boot or KVM acceptance."""
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

REPO = Path(__file__).resolve().parents[1]
RECIPE = (REPO / 'build/guest-fixture.sh').read_text()
INIT = (REPO / 'configs/managed-guest-init').read_text()


def run_shell(script, *args, env=None):
    return subprocess.run(['sh', '-c', script, 'fixture-test', *args],
                          env=env, capture_output=True, text=True)


def cpio_members(path):
    """Read regular payloads directly from reproducible newc; never execute init."""
    data = gzip.decompress(path.read_bytes())
    result = {}
    offset = 0
    while True:
        header = data[offset:offset + 110]
        if header[:6] != b'070701':
            raise AssertionError('invalid newc header')
        size = int(header[54:62], 16)
        namesize = int(header[94:102], 16)
        start = offset + 110
        name = data[start:start + namesize - 1].decode().removeprefix('./')
        start = (start + namesize + 3) & ~3
        if name == 'TRAILER!!!':
            return result
        result[name] = data[start:start + size]
        offset = (start + size + 3) & ~3


class ManagedGuestFixture(unittest.TestCase):
    def test_cli_refuses_unknown_profile_and_missing_inputs_before_mutation(self):
        candidate = REPO / 'out' / ('guest-fixture-refusal-' + uuid.uuid4().hex)
        for profile in ('unknown', '', 'legacy', '../configs/managed-guest-init', 'managed-power-key-v1'):
            with self.subTest(profile=profile):
                result = subprocess.run(['bash', str(REPO / 'build/guest-fixture.sh'),
                                         '/missing-kernel', '/missing-busybox', str(candidate), profile],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(candidate.exists())
                if profile != 'managed-power-key-v1':
                    self.assertEqual(result.returncode, 2)
                    self.assertIn('Unsupported guest fixture profile', result.stderr)
                    self.assertNotIn('realpath', result.stderr)

    def test_init_refuses_host_before_actions_and_shell_syntax(self):
        result = subprocess.run(['sh', str(REPO / 'configs/managed-guest-init')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.strip(), 'Guest init requires PID 1')
        self.assertEqual(result.stdout, '')
        for interpreter, path in (('bash', 'build/guest-fixture.sh'),
                                  ('sh', 'configs/managed-guest-init'),
                                  ('bash', 'configs/managed-guest-init')):
            subprocess.run([interpreter, '-n', str(REPO / path)], check=True)
        self.assertLess(INIT.index('[ "$$" -eq 1 ]'), INIT.index('/bin/busybox --install'))

    def test_actual_pure_decoder(self):
        start = INIT.index('managed_power_event() {')
        end = INIT.index('\n# Each od invocation', start)
        pure = INIT[start:end] + '\nmanaged_power_event "$@"\n'
        time = ['0', '0', '1', '0', '65535', '2', '3', '4']
        cases = [(time + ['1', '116', '1', '0'], 0),
                 (time + ['1', '116', '0', '0'], 1),
                 (time + ['1', '116', '2', '0'], 1),
                 (time + ['1', '116', '1', '1'], 1),
                 (time + ['0', '0', '0', '0'], 1),
                 (time + ['1', '115', '1', '0'], 1),
                 (time + ['2', '116', '1', '0'], 1),
                 ([], 2), (time, 2), (time + ['1', '116', '1'], 2),
                 (time + ['1', '116', '1', '0', '0'], 2)]
        for word in ('', '-1', '65536', '99999999999999', '1x', '1;poweroff', '$(poweroff)'):
            cases.append(([word] + time[1:] + ['1', '116', '1', '0'], 2))
        for words, expected in cases:
            with self.subTest(words=words):
                self.assertEqual(run_shell(pure, *words).returncode, expected)

    def test_network_wait_and_power_sequence(self):
        original = (REPO / 'configs/guest-init').read_text()
        # Every existing CPU/disk guard, update and unmount is identical.
        self.assertEqual(INIT[:INIT.index('# Open the sole GPIO input')],
                         original[:original.index('ip link set lo up')])
        for text in ('set -- /dev/input/event*', '[ "$#" -eq 1 ] && [ -c "$1" ]',
                     'device/name")" = gpio-keys', 'exec 3<"$1"',
                     'od -An -v -tu2 -w24 -N24 <&3', '*) fail managed-input-record'):
            self.assertIn(text, INIT)
        self.assertLess(INIT.index('exec 3<'), INIT.index('READY network'))
        self.assertLess(INIT.index('network-confirmation'), INIT.index('READY managed-power-key-v1'))
        self.assertNotIn('kill "$network_pid"', INIT)
        self.assertNotIn('wait "$network_pid"', INIT)
        self.assertEqual(INIT.count('poweroff -f'), 1)
        self.assertLess(INIT.index('managed_power_event "$@"'), INIT.index('sync || fail managed-power-key-sync'))
        self.assertLess(INIT.index('sync || fail managed-power-key-sync'), INIT.index("printf 'PASS managed-power-key-sync"))
        self.assertLess(INIT.index("printf 'PASS managed-power-key-sync"), INIT.index('poweroff -f'))

    def test_kernel_and_preflight_guards(self):
        fragment = (REPO / 'configs/managed-guest-kernel.fragment').read_text()
        for symbol in ('INPUT', 'INPUT_EVDEV', 'INPUT_KEYBOARD', 'KEYBOARD_GPIO', 'GPIOLIB', 'GPIO_PL061'):
            self.assertIn(f'CONFIG_{symbol}=y', fragment)
            self.assertIn(symbol, RECIPE[RECIPE.index('required+='):])
        self.assertIn('required+=(INPUT INPUT_EVDEV INPUT_KEYBOARD KEYBOARD_GPIO GPIOLIB GPIO_PL061 ARM_AMBA IRQ_DOMAIN GPIOLIB_IRQCHIP)', RECIPE)
        self.assertIn("packages.append('qemu-user-static')", RECIPE)
        self.assertIn("records = ((0,) * 12, words,", RECIPE)
        self.assertIn("stdin=reader, capture_output=True, timeout=10", RECIPE)
        self.assertLess(RECIPE.index("<<'PY_OD'"), RECIPE.index('mkdir -p -- "$repo/out"'))
        self.assertLess(RECIPE.index('exact-record od support'), RECIPE.index('kmake -j2 Image'))
        self.assertIn('kmake -j2 Image', RECIPE)

    def test_actual_staging_both_profiles_and_managed_manifest_closure(self):
        for tool in ('cpio', 'gzip', 'tar'):
            self.assertIsNotNone(shutil.which(tool), tool)
        start = RECIPE.index('root=$output/initramfs-root\n')
        end = RECIPE.index('# The fixed seed builder', start)
        for profile, name in (('legacy', 'guest-init'), ('managed-power-key-v1', 'managed-guest-init')):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary)
                (output / 'initramfs-root').mkdir()
                env = dict(os.environ, repo=str(REPO), output=str(output),
                           busybox=shutil.which('true'), guest_init=str(REPO / 'configs' / name),
                           SOURCE_DATE_EPOCH='1793750400', profile=profile)
                result = subprocess.run(['bash', '-euo', 'pipefail', '-c', RECIPE[start:end]],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                members = cpio_members(output / 'initramfs.cpio.gz')
                self.assertEqual(members['init'], (REPO / 'configs' / name).read_bytes())
                self.assertEqual(members['www/cgi-bin/probe'], (REPO / 'configs/guest-network-cgi').read_bytes())
                self.assertEqual(members['bin/busybox'], Path(shutil.which('true')).read_bytes())
                if profile == 'legacy':
                    continue
                # Exercise the actual source-closure, SHA256SUMS and manifest
                # section with tiny placeholder artifacts, never a kernel claim.
                for artifact in ('Image', 'kernel.config', 'guest-disk.raw'):
                    (output / artifact).write_bytes(b'host-test-only')
                for config in ('guest-kernel.fragment', 'managed-guest-kernel.fragment',
                               'diagnostic-sources.lock.json', 'builder-packages.lock.tsv'):
                    shutil.copyfile(REPO / 'configs' / config, output / config)
                import hashlib
                (output / 'logs').mkdir()
                (output / 'logs/disk-build.log').write_text(json.dumps({
                    'bytes': 14, 'sha256': hashlib.sha256(b'host-test-only').hexdigest(),
                    'source_date_epoch': 1793750400}))
                tail = RECIPE[RECIPE.index('cp -- "$repo/build/guest-fixture.sh"'):]
                # The CLI supplies pins; packaging consumes these values only.
                tail = 'pins=(kernel-pin busybox-pin compiler-pin)\n' + tail
                result = subprocess.run(['bash', '-euo', 'pipefail', '-c', tail], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                manifest = json.loads((output / 'artifact-manifest.json').read_text())
                self.assertEqual(manifest['hardware_acceptance'], 'pending')
                self.assertEqual(manifest['profile'], profile)
                self.assertEqual(manifest['selected_init'], 'configs/managed-guest-init')
                self.assertEqual(manifest['files']['guest-init']['sha256'],
                                 hashlib.sha256(INIT.encode()).hexdigest())
                self.assertIn('managed-guest-kernel.fragment', manifest['files'])
                subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=output, check=True,
                               capture_output=True)
                import tarfile
                with tarfile.open(output / 'guest-build-recipe.tar.gz') as archive:
                    for filename in ('configs/managed-guest-init', 'configs/managed-guest-kernel.fragment'):
                        self.assertEqual(archive.extractfile(filename).read(), (REPO / filename).read_bytes())


if __name__ == '__main__':
    unittest.main()

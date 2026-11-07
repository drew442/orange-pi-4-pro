"""Registered second-state firmware transport tests; no physical acceptance."""
import argparse
import contextlib
import copy
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location(path.replace('/', '_'), REPO / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


L = load('build/launch-nvme-test-sd-repeat.py')


def contents():
    names = subprocess.check_output(['git', '-C', str(REPO), 'ls-files', '-z']).decode().split('\0')
    return {name: (REPO / name).read_bytes() for name in names + [L.SELF] if name}


def record(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'git_blob': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()}


class TransportRepeatTests(unittest.TestCase):
    def setup_parent(self, stack, data=None, roots=None):
        parent = L.compose(stack, REPO / L.SELF)
        roots = load('build/launch-host-roots.py') if roots is None else roots
        data = contents() if data is None else data
        policy = parent.contract(roots, REPO, data)
        options = argparse.Namespace(userspace=Path('/missing/rootfs.tar.gz'),
            diagnostic=Path('/missing/diagnostic'), busybox=Path('/missing/busybox'),
            state_receipt=parent.STATE_PHASE / 'receipt.json',
            state_receipt_sha256=parent.STATE_RECEIPT_SHA, tag='firmware-repeat-test')
        manifest = {'revision': 'a' * 40, 'files': {name: record(raw) for name, raw in data.items()}}
        return parent, roots, policy, options, manifest

    def test_actual_registration_plan_and_payload(self):
        before = {name: (REPO / name).read_bytes() for name in
                  (L.REPEAT, L.FIRMWARE, 'build/launch-host-bootstrap.py')}
        with contextlib.ExitStack() as stack:
            parent, _, policy, options, manifest = self.setup_parent(stack)
            with mock.patch.object(Path, 'mkdir', side_effect=AssertionError('staging')):
                report = parent.plan(options, manifest, policy)
            self.assertEqual(parent.__file__, str(REPO / L.SELF))
            self.assertEqual(parent.SELF, L.SELF)
            self.assertEqual(report['state_uuid'], 'feaaee34-d782-4c18-bd04-1029540ab26a')
            self.assertEqual(report['boot_profile'], 'nvme-first-el2-test')
            self.assertEqual(report['state_receipt_sha256'],
                '01a03e382d90105863285c8625e5f9744cec8ffb535124afd6d9520c7c022c42')
            self.assertEqual(report['image_bytes'], 256 * 1024**2)
            self.assertEqual(report['actual_sd_bytes'], 1977614336)
            self.assertFalse(report['hardware_acceptance'])
            self.assertFalse(report['physical_storage_written'])
            self.assertEqual(report['firmware_repeat_controller']['firmware_adapter']['sha256'], L.FIRMWARE_SHA)
            profile = load('build/assemble-host.py').load_boot_profile('nvme-first-el2-test')
            self.assertEqual(policy['diagnostic'], {name: profile[0][name] for name in parent.BOOT_FILES})
            self.assertEqual(policy['boot_profile'], profile[1])
            repeat = load(L.REPEAT)
            with repeat.Template(REPO / repeat.TEMPLATE, repeat.TEMPLATE_SHA) as held:
                baseline = repeat.load_template(held)
                payload = baseline.payload_program(options.tag, parent.STATE_UUID)
            self.assertEqual(parent.payload_program(options.tag, parent.STATE_UUID),
                payload.replace(b"boot_profile='experimental-el2'", b"boot_profile='nvme-first-el2-test'"))
            for field, value in [('state_receipt', Path('/var/lib/a733-clean-replay-20261106/host-state-timesync-02/receipt.json')),
                                 ('state_receipt_sha256', '3abeb7ba014eae05acebfd7af3b75dab7c4c58999b96d8c752163bb96a2ccd33')]:
                bad = copy.copy(options)
                setattr(bad, field, value)
                with self.assertRaises(ValueError): parent.plan(bad, manifest, policy)
            with self.assertRaises(ValueError):
                parent.payload_program(options.tag, 'fc8bbef6-9385-4ddb-90ba-e41eaccc035b')
        self.assertEqual(before, {name: (REPO / name).read_bytes() for name in before})

    def test_adapter_closure_and_project_drift_refuse(self):
        for name in (L.REPEAT, L.FIRMWARE, 'build/launch-host-bootstrap.py'):
            data = contents()
            data[name] += b'\n'
            with self.subTest(name=name), contextlib.ExitStack() as stack:
                with self.assertRaisesRegex(ValueError, 'committed closure'):
                    self.setup_parent(stack, data=data)
        with contextlib.ExitStack() as stack:
            parent = L.compose(stack, REPO / L.SELF)
            with self.assertRaisesRegex(ValueError, 'project path'):
                parent.contract(None, REPO / 'other', {})

    def test_adapter_pin_drift_refuses_before_loading(self):
        for pin in ('REPEAT_SHA', 'FIRMWARE_SHA'):
            with self.subTest(pin=pin), mock.patch.object(L, pin, '0' * 64), contextlib.ExitStack() as stack:
                with self.assertRaisesRegex(ValueError, 'SHA256'):
                    L.compose(stack, REPO / L.SELF)

    def test_identity_binding_drift_refuses(self):
        original = L.load_adapter
        def changed(held, name, filename):
            adapter = original(held, name, filename)
            if held.path.name == Path(L.REPEAT).name:
                adapter.SUBSTITUTIONS[adapter.TEMPLATE] = 'other.py'
            return adapter
        with mock.patch.object(L, 'load_adapter', changed), contextlib.ExitStack() as stack:
            with self.assertRaisesRegex(ValueError, 'identity anchor'):
                L.compose(stack, REPO / L.SELF)

    def test_held_file_replacement_and_symlink_refuse(self):
        raw = b'bounded adapter fixture'
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'adapter.py'
            path.write_bytes(raw)
            alias = path.with_name('alias.py')
            alias.symlink_to(path)
            with self.assertRaises(OSError):
                with L.Template(alias, digest): pass
            with self.assertRaisesRegex(ValueError, 'changed'):
                with L.Template(path, digest):
                    replacement = path.with_name('replacement.py')
                    replacement.write_bytes(raw)
                    replacement.replace(path)

    def test_adapter_fences_run_before_publication(self):
        events = []
        roots = load('build/launch-host-roots.py')
        roots.execute = lambda **kwargs: (kwargs['final_check'](), events.append('success'))
        with contextlib.ExitStack() as stack:
            _, roots, _, _, _ = self.setup_parent(stack, roots=roots)
            original = L.Template.unchanged
            def check(held):
                events.append(held.path.name)
                return original(held)
            with mock.patch.object(L.Template, 'unchanged', check):
                roots.execute(final_check=lambda: events.append('original'))
            self.assertEqual(events[0], 'original')
            self.assertEqual(events[-1], 'success')
            self.assertIn(Path(L.REPEAT).name, events)
            self.assertIn(Path(L.FIRMWARE).name, events)
            for name in (L.REPEAT, L.FIRMWARE):
                def fail(held):
                    if held.path.name == Path(name).name:
                        raise ValueError('adapter changed')
                    return original(held)
                with mock.patch.object(L.Template, 'unchanged', fail):
                    with self.assertRaisesRegex(ValueError, 'adapter changed'):
                        roots.execute(final_check=lambda: events.append('original'))
            self.assertEqual(events.count('success'), 1)

    def test_cli_retains_historical_options_and_requires_isolation(self):
        result = subprocess.run([sys.executable, '-I', '-B', str(REPO / L.SELF), '--help'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--state-receipt-sha256', result.stdout)
        self.assertNotIn('--boot-profile', result.stdout)
        result = subprocess.run([sys.executable, '-B', str(REPO / L.SELF), '--help'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invoke Python with -I -B', result.stderr)


if __name__ == '__main__':
    unittest.main()

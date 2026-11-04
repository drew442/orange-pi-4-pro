"""Refuse changed captured package bytes before any ARM staging or execution."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


V = load('guest_tools_verifier_test', 'tests/verify_guest_tools_arm.py')
TOOLS = load('guest_tools_verifier_builder', 'build/maintenance-tools.py')


class PackageAuthentication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.cleanup)
        self.entries = TOOLS.entries(REPO)
        tree = self.root / 'tree'
        for name, (mode, data) in self.entries.items():
            target = tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(mode)
        for path in tree.rglob('*'):
            if path.is_dir():
                path.chmod(0o555)
        tree.chmod(0o555)
        archive = TOOLS.archive(self.entries)
        (self.root / 'tools.tar.gz').write_bytes(archive)
        self.metadata = {**json.loads(self.entries['package.json'][1]),
            'files': {name: {'mode': mode, **TOOLS.record(data)}
                      for name, (mode, data) in self.entries.items()},
            'artifacts': {'tools.tar.gz': TOOLS.record(archive)}}

    def cleanup(self):
        for path in self.root.rglob('*'):
            if path.is_dir():
                path.chmod(0o755)
        self.temp.cleanup()

    def pin(self):
        data = TOOLS.canonical(self.metadata)
        (self.root / 'manifest.json').write_bytes(data)
        return TOOLS.record(data)['sha256']

    def test_changed_capture_after_tree_authentication_never_stages(self):
        auth = load('guest_tools_capture_auth', 'tests/verify_maintenance_arm.py')
        enrollment = load('guest_tools_capture_enrollment', 'tests/verify_guest_enrollment_arm.py')
        original_read, count = auth.regular, 0
        changed = self.root / 'tree/scripts/enroll-guest.py'
        def regular(path, *args):
            nonlocal count
            data = original_read(path, *args)
            if Path(path) == changed:
                count += 1
                if count == 2:
                    return data + b'\n# changed after initial validation\n'
            return data
        original_module = V.module
        def module(name, path):
            if name == 'installed_arm_authentication':
                return auth
            if name == 'installed_arm_enrollment':
                return enrollment
            return original_module(name, path)
        with patch.object(V, 'module', side_effect=module), patch.object(auth, 'regular', side_effect=regular), \
                patch.object(enrollment, 'verify') as stage, \
                self.assertRaisesRegex(ValueError, 'captured package changed'):
            V.verify(self.root, self.pin(), '/unused-rootfs', '/unused-guest')
        self.assertEqual(count, 2)
        stage.assert_not_called()

    def test_inconsistent_outer_entrypoints_never_stages(self):
        self.metadata['entrypoints'] = {}
        with self.assertRaisesRegex(ValueError, 'outer package metadata differs'):
            V.verify(self.root, self.pin(), '/unused-rootfs', '/unused-guest')


if __name__ == '__main__':
    unittest.main()

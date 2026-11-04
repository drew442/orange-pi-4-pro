"""Cross-language package layout agreement; no mounts or storage devices."""
import importlib.util
from pathlib import Path
import subprocess
import unittest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('package_boot_contract', REPO / 'configs/package_state.py')
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


class BootContractTests(unittest.TestCase):
    def test_early_ash_layout_matches_builder_and_management(self):
        result = subprocess.run(['bash', '-c', '. "$1"; hi_package_items', 'layout-check',
                                 str(REPO / 'configs/host-init')], check=True,
                                capture_output=True, text=True)
        rows = [line.split() for line in result.stdout.splitlines()]
        self.assertEqual(rows, [[name, path, kind] for name, (path, kind) in PACKAGE.COMPONENTS.items()])

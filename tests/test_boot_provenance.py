#!/usr/bin/env python3
"""Exercise production U-Boot provenance collection without compiling firmware."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


RECIPE = Path(__file__).resolve().parents[1] / "build/diagnostic.sh"


class BootProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tree = self.root / "worktree"
        (self.tree / "board/sunxi").mkdir(parents=True)
        self.challenge = self.tree / "board/sunxi/sunxi_challenge.c"
        self.challenge.write_bytes(b"/* generated fixture */\nunsigned char challenge[] = {\n" +
                                   b", ".join(str(i).encode() for i in range(128)) + b"\n};\n")
        (self.tree / "u-boot.bin").write_bytes(bytes(range(256)) * 2)
        (self.tree / ".config").write_bytes(b"CONFIG_ENV_IS_NOWHERE=y\n")
        self.log = self.root / "uboot-build.log"
        self.log.write_bytes(b"make: configuration\narm-linux-gnueabi-gcc fixture.c\nlink u-boot\n")
        self.artifacts = self.root / "artifacts"

    def shell(self, command):
        return subprocess.run(
            ["bash", "-c", 'source "$1"; ' + command, "provenance-test",
             str(RECIPE), str(self.tree), str(self.artifacts), str(self.log)],
            text=True, capture_output=True,
        )

    def test_preserves_exact_inputs_and_manifest_checksums(self):
        expected = {
            "uboot-sunxi-challenge.c": self.challenge.read_bytes(),
            "uboot-unpacked.bin": (self.tree / "u-boot.bin").read_bytes(),
            "uboot.config": (self.tree / ".config").read_bytes(),
            "uboot-build.log": self.log.read_bytes(),
        }
        result = self.shell('collect_uboot_provenance "$2" "$3" "$4"; '
                            'cd "$3"; printf fixture > existing-artifact; '
                            'sha256sum existing-artifact uboot.config > SHA256SUMS; '
                            'append_uboot_provenance_checksums "$3"; '
                            'write_artifact_manifest "$3"')
        self.assertEqual(result.returncode, 0, result.stderr)
        # Later packing or worktree changes cannot replace the saved originals.
        (self.tree / "u-boot.bin").write_bytes(b"packed or replaced")
        self.challenge.unlink()
        for name, contents in expected.items():
            self.assertEqual((self.artifacts / name).read_bytes(), contents)
        manifest = json.loads((self.artifacts / "artifact-manifest.json").read_text())
        self.assertEqual(set(manifest["files"]), set(expected) | {
            "existing-artifact", "uboot-provenance.sha256"})
        for name, entry in manifest["files"].items():
            contents = (self.artifacts / name).read_bytes()
            self.assertEqual(entry, {"bytes": len(contents),
                                    "sha256": hashlib.sha256(contents).hexdigest()})
        for checksums in ("SHA256SUMS", "uboot-provenance.sha256"):
            check = subprocess.run(["sha256sum", "-c", checksums], cwd=self.artifacts,
                                   text=True, capture_output=True)
            self.assertEqual(check.returncode, 0, check.stderr)
        metadata = manifest["uboot_provenance"]
        self.assertIn("per-build random", metadata["challenge_generation"])
        self.assertIn("no bit reproducibility claim", metadata["reproducibility"])

    def test_missing_challenge_fails_before_any_artifacts_are_written(self):
        self.challenge.unlink()
        result = self.shell('collect_uboot_provenance "$2" "$3" "$4"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing U-Boot provenance input", result.stderr)
        self.assertFalse(self.artifacts.exists())


if __name__ == "__main__":
    unittest.main()

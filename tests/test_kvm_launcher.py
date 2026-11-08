"""Host-only launcher checks; no QEMU execution or hardware acceptance."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "kvm_launcher", Path(__file__).parents[1] / "scripts/launch-kvm-guest.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        storage = patch.object(launcher.STORAGE, 'verify_guest_storage',
                                    return_value={'fixture': 'placement mocked; no NVMe acceptance'})
        storage.start()
        self.addCleanup(storage.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cli = ["--memory-mib", "256", "--cpus", "1"]
        for name in ("kernel", "initrd", "disk"):
            path = self.root / name
            content = f"test {name}".encode()
            path.write_bytes(content)
            self.cli += [f"--{name}", str(path), f"--{name}-sha256",
                         hashlib.sha256(content).hexdigest()]
        self.args = launcher.parser().parse_args(self.cli)

    def test_kvm_only_and_explicit_limits(self):
        with launcher.prepare(self.args) as guest:
            result = guest.report()
        argv = result["argv"]
        for flag, value in (("-machine", "virt,gic-version=host"), ("-accel", "kvm"),
                            ("-cpu", "host"), ("-m", "256"), ("-smp", "1"),
                            ("-monitor", "none"), ("-serial", "stdio")):
            self.assertEqual(argv[argv.index(flag) + 1], value)
        self.assertNotIn("tcg", " ".join(argv))
        self.assertIn("-nodefaults", argv)
        self.assertIn("-nographic", argv)
        # Direct kernel boot must not depend on an optional PXE ROM package.
        self.assertIn("virtio-net-pci,netdev=guestnet,romfile=", argv)
        self.assertEqual(result["hardware_acceptance"], "pending")

    def test_no_listeners_or_passthrough(self):
        with launcher.prepare(self.args) as guest:
            argv = guest.argv()
        self.assertEqual(argv[argv.index("-netdev") + 1], "user,id=guestnet,restrict=on")
        for forbidden in ("hostfwd", "guestfwd", "-qmp", "-vnc", "vfio", "/dev/"):
            self.assertNotIn(forbidden, " ".join(argv))

    def test_raw_disk_json_preserves_comma_in_filename(self):
        renamed = self.root / "disk,driver=host_device"
        self.args.disk.rename(renamed)
        self.args.disk = renamed
        with launcher.prepare(self.args) as guest:
            argv = guest.argv()
        block = json.loads(argv[argv.index("-blockdev") + 1])
        self.assertEqual(block["driver"], "raw")
        self.assertEqual(block["file"], {"driver": "file", "filename": str(renamed)})

    def test_checksum_mismatch_for_every_input(self):
        for name in ("kernel", "initrd", "disk"):
            with self.subTest(name=name), patch.object(self.args, f"{name}_sha256", "0" * 64):
                with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                    launcher.prepare(self.args)

    def test_disk_checkpoint_must_be_updated_after_write(self):
        self.args.disk.write_bytes(b"guest changed this disk")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            launcher.prepare(self.args)
        self.args.disk_sha256 = hashlib.sha256(self.args.disk.read_bytes()).hexdigest()
        with launcher.prepare(self.args):
            pass

    def test_reject_non_regular_inputs_without_opening(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        link = self.root / "symlink"
        link.symlink_to(self.args.disk)
        for path in (self.root, Path("/dev/null"), fifo, link):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "regular file"):
                with patch.object(self.args, "disk", path):
                    launcher.prepare(self.args)

    def test_reject_disk_aliasing_boot_input(self):
        self.args.disk.unlink()
        os.link(self.args.kernel, self.args.disk)
        self.args.disk_sha256 = self.args.kernel_sha256
        with self.assertRaisesRegex(ValueError, "single-link"):
            launcher.prepare(self.args)

    def test_empty_and_invalid_digest(self):
        self.args.disk.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "empty"):
            launcher.prepare(self.args)
        with patch.object(self.args, "kernel_sha256", "not a digest"):
            with self.assertRaisesRegex(ValueError, "Invalid SHA-256"):
                launcher.prepare(self.args)

    def test_dry_run_requires_no_kvm_or_qemu(self):
        output = io.StringIO()
        with patch.object(launcher.shutil, "which", return_value=None), \
                patch.object(launcher, "require_launch_access") as access, \
                patch.object(launcher.os, "execv") as execute, contextlib.redirect_stdout(output):
            self.assertEqual(launcher.main(self.cli + ["--dry-run"]), 0)
        access.assert_not_called()
        execute.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertEqual(result["operation"], "dry-run")
        self.assertEqual(len(result["verified_inputs"]), 3)

    def test_root_is_rejected(self):
        for uid, euid in ((0, 1000), (1000, 0)):
            with patch.object(launcher.os, "getuid", return_value=uid), \
                    patch.object(launcher.os, "geteuid", return_value=euid):
                with self.assertRaisesRegex(ValueError, "non-root"):
                    launcher.require_launch_access(self.args)

    def test_missing_qemu_or_kvm_access_is_rejected(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(launcher.os, "getuid", return_value=1000))
            stack.enter_context(patch.object(launcher.os, "geteuid", return_value=1000))
            which = stack.enter_context(patch.object(launcher.shutil, "which", return_value=None))
            with self.assertRaisesRegex(ValueError, "not installed"):
                launcher.require_launch_access(self.args)
            which.return_value = "/usr/bin/qemu-system-aarch64"
            mock_stat = stack.enter_context(patch.object(launcher.os, "stat"))
            mock_stat.return_value = SimpleNamespace(st_mode=stat.S_IFREG)
            with self.assertRaisesRegex(ValueError, "character device"):
                launcher.require_launch_access(self.args)
            mock_stat.return_value = SimpleNamespace(st_mode=stat.S_IFCHR)
            access = stack.enter_context(patch.object(launcher.os, "access", return_value=False))
            with self.assertRaisesRegex(ValueError, "access to /dev/kvm"):
                launcher.require_launch_access(self.args)
            access.side_effect = [True, False]
            with self.assertRaisesRegex(ValueError, "guest disk"):
                launcher.require_launch_access(self.args)

    def test_actual_launch_uses_direct_exec_after_access_check(self):
        with patch.object(launcher.shutil, "which", return_value="/usr/bin/qemu-system-aarch64"), \
                patch.object(launcher, "require_launch_access") as access, \
                patch.object(launcher.os, "execv") as execute, \
                patch.object(launcher.PreparedGuest, "inherit_inputs"):
            self.assertEqual(launcher.main(self.cli), 0)
        access.assert_called_once()
        executable, argv = execute.call_args.args
        self.assertEqual(executable, "/usr/bin/qemu-system-aarch64")
        self.assertEqual(argv[0], executable)
        self.assertEqual(argv[argv.index("-accel") + 1], "kvm")

    def test_nonpositive_limits_rejected(self):
        for flag in ("--memory-mib", "--cpus"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                launcher.parser().parse_args(self.cli + [flag, "0"])


if __name__ == "__main__":
    unittest.main()

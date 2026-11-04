"""Host-only input lifetime checks; these do not launch QEMU or establish KVM."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/launch-kvm-guest.py"
spec = importlib.util.spec_from_file_location("guest_launcher", SCRIPT)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class GuestLauncherTests(unittest.TestCase):
    def setUp(self):
        storage = mock.patch.object(launcher.STORAGE, 'verify_guest_storage',
                                    return_value={'fixture': 'placement mocked; no NVMe acceptance'})
        storage.start()
        self.addCleanup(storage.stop)
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.values = {"kernel": b"kernel contents", "initrd": b"initrd contents",
                       "disk": b"disk contents"}
        self.args = argparse.Namespace(memory_mib=128, cpus=1, dry_run=False)
        for name, value in self.values.items():
            path = self.root / name
            path.write_bytes(value)
            setattr(self.args, name, path)
            setattr(self.args, f"{name}_sha256", hashlib.sha256(value).hexdigest())

    def command(self, dry_run=False):
        args = []
        for name in self.values:
            args += [f"--{name}", str(getattr(self.args, name)), f"--{name}-sha256",
                     getattr(self.args, f"{name}_sha256")]
        args += ["--memory-mib", "128", "--cpus", "1"]
        return args + (["--dry-run"] if dry_run else [])

    def fd_count(self):
        return len(os.listdir("/proc/self/fd"))

    def test_dry_run_has_stable_paths_and_preserves_content_without_kvm(self):
        before = {name: getattr(self.args, name).stat() for name in self.values}
        result = subprocess.run([sys.executable, str(SCRIPT), *self.command(True)],
                                capture_output=True, text=True, check=True,
                                env={**os.environ, "PATH": "/no-qemu"})
        report = json.loads(result.stdout)
        self.assertNotIn("/proc/self/fd/", result.stdout)
        self.assertEqual(report["storage_policy"], "not checked in file-only dry-run")
        self.assertEqual(report["hardware_acceptance"], "pending")
        self.assertEqual(report["argv"][report["argv"].index("-accel") + 1], "kvm")
        self.assertNotIn("hostfwd", result.stdout)
        self.assertEqual(report['argv'][report['argv'].index('-netdev') + 1],
                         'user,id=guestnet,restrict=on')
        self.assertEqual(set(report), {'operation', 'hardware_acceptance', 'verified_inputs',
                                       'argv', 'storage_policy'})
        for name, value in self.values.items():
            path = getattr(self.args, name)
            self.assertEqual(path.read_bytes(), value)
            self.assertEqual(path.stat().st_mtime_ns, before[name].st_mtime_ns)
            self.assertEqual(report["verified_inputs"][name]["path"], str(path))

    def test_loopback_forward_backend_is_exact_and_inputs_remain_held(self):
        for host, guest_port in ((1024, 1), (2222, 22), (65535, 65535)):
            with self.subTest(host=host, guest=guest_port):
                self.args.host_tcp_port = host
                self.args.guest_tcp_port = guest_port
                with launcher.prepare(self.args) as guest:
                    argv = guest.argv()
                    self.assertEqual(argv[argv.index('-netdev') + 1],
                                     f'user,id=guestnet,restrict=on,ipv6=off,'
                                     f'hostfwd=tcp:127.0.0.1:{host}-10.0.2.15:{guest_port}')
                    self.assertEqual(argv.count('-netdev'), 1)
                    self.assertEqual(argv[argv.index('-accel') + 1], 'kvm')
                    descriptor_argv = guest.argv(descriptor_paths=True)
                    self.assertEqual(descriptor_argv[descriptor_argv.index('-kernel') + 1],
                                     f'/proc/self/fd/{guest.fds["kernel"]}')
                    self.assertEqual(guest.report()['hardware_acceptance'], 'pending')
                    self.assertEqual(set(guest.fds), set(self.values))
        self.assertEqual(launcher.STORAGE.verify_guest_storage.call_count, 3)

    def test_network_rejected_before_any_input_open_or_hash(self):
        invalid = [(2222, None), (None, 22)]
        invalid += [(port, 22) for port in (True, False, 1, 1023, 65536, 2222.0, '2222')]
        invalid += [(2222, port) for port in (True, False, 0, -1, 65536, 22.0, '22')]
        for host, guest in invalid:
            with self.subTest(host=host, guest=guest):
                self.args.host_tcp_port, self.args.guest_tcp_port = host, guest
                with mock.patch.object(launcher.PreparedGuest, 'open_file') as opening, \
                        mock.patch.object(launcher.PreparedGuest, 'verify_file') as hashing:
                    with self.assertRaises(ValueError):
                        launcher.prepare(self.args)
                opening.assert_not_called()
                hashing.assert_not_called()
        launcher.STORAGE.verify_guest_storage.assert_not_called()

    def test_cli_network_validates_before_launch_checks_or_inputs(self):
        for flags in (['--host-tcp-port', '2222'], ['--guest-tcp-port', '22'],
                      ['--host-tcp-port', '1023', '--guest-tcp-port', '22'],
                      ['--host-tcp-port', '2222', '--guest-tcp-port', '65536']):
            with self.subTest(flags=flags), \
                    mock.patch.object(launcher, 'require_launch_access') as access, \
                    mock.patch.object(launcher, 'prepare') as prepare, \
                    mock.patch.object(launcher.sys, 'stderr'):
                self.assertEqual(launcher.main(self.command() + flags), 1)
                access.assert_not_called()
                prepare.assert_not_called()
        for port in ('22.0', 'true', '127.0.0.1:2222', '2222,hostfwd=udp::1-:1', '+2222', ' 2222'):
            with self.subTest(port=port), mock.patch.object(launcher.sys, 'stderr'):
                with self.assertRaises(SystemExit):
                    launcher.parser().parse_args(self.command(True) +
                                                ['--host-tcp-port', port, '--guest-tcp-port', '22'])

    def test_forwarded_cli_dry_run_keeps_kvm_required_and_fixed_listener(self):
        result = subprocess.run([sys.executable, str(SCRIPT), *self.command(True),
                                 '--host-tcp-port', '2222', '--guest-tcp-port', '22'],
                                capture_output=True, text=True, check=True,
                                env={**os.environ, 'PATH': '/no-qemu'})
        report = json.loads(result.stdout)
        self.assertEqual(report['argv'][report['argv'].index('-netdev') + 1],
                         'user,id=guestnet,restrict=on,ipv6=off,'
                         'hostfwd=tcp:127.0.0.1:2222-10.0.2.15:22')
        self.assertEqual(report['argv'][report['argv'].index('-accel') + 1], 'kvm')
        self.assertEqual(report['hardware_acceptance'], 'pending')

    def test_invalid_inputs_and_cleanup(self):
        for kind in ("bad-hash", "mismatch", "empty", "directory", "fifo", "symlink", "hardlink"):
            with self.subTest(kind=kind):
                path = self.args.disk
                original_hash = self.args.disk_sha256
                path.unlink()
                if kind == "empty":
                    path.touch()
                elif kind == "directory":
                    path.mkdir()
                elif kind == "fifo":
                    os.mkfifo(path)
                elif kind == "symlink":
                    path.symlink_to(self.args.kernel)
                elif kind == "hardlink":
                    os.link(self.args.kernel, path)
                else:
                    path.write_bytes(self.values["disk"])
                    self.args.disk_sha256 = "invalid" if kind == "bad-hash" else "0" * 64
                count = self.fd_count()
                with self.assertRaises((ValueError, OSError)):
                    launcher.prepare(self.args)
                self.assertEqual(self.fd_count(), count)
                path.rmdir() if path.is_dir() else path.unlink()
                path.write_bytes(self.values["disk"])
                self.args.disk_sha256 = original_hash

    def test_disk_cannot_share_kernel_inode(self):
        self.args.disk = self.args.kernel
        self.args.disk_sha256 = self.args.kernel_sha256
        count = self.fd_count()
        with self.assertRaisesRegex(ValueError, "separate"):
            launcher.prepare(self.args)
        self.assertEqual(self.fd_count(), count)

    def test_mutation_during_hash_is_bounded_and_refused(self):
        real_read = os.read
        for mutation in ("grow", "shrink", "overwrite", "hardlink"):
            with self.subTest(mutation=mutation):
                self.args.kernel.write_bytes(self.values["kernel"])
                # Writes in one filesystem clock tick may keep identical
                # timestamps. Start in a distinct epoch so this tests actual
                # metadata-change detection rather than scheduler timing.
                os.utime(self.args.kernel, ns=(1_000_000_000, 1_000_000_000))
                read_sizes = []
                changed = False

                def read(fd, count):
                    nonlocal changed
                    chunk = real_read(fd, count)
                    read_sizes.append(count)
                    if not changed:
                        changed = True
                        if mutation == "grow":
                            with self.args.kernel.open("ab") as stream:
                                stream.write(b"extra")
                        elif mutation == "shrink":
                            self.args.kernel.write_bytes(b"x")
                        elif mutation == "overwrite":
                            self.args.kernel.write_bytes(b"X" * len(self.values["kernel"]))
                        else:
                            os.link(self.args.kernel, self.root / "extra-link")
                    return chunk

                count = self.fd_count()
                with mock.patch.object(launcher.os, "read", side_effect=read):
                    with self.assertRaisesRegex(ValueError, "changed"):
                        with launcher.prepare(self.args):
                            pass
                self.assertEqual(read_sizes, [len(self.values["kernel"])])
                self.assertEqual(self.fd_count(), count)
                if mutation == "hardlink":
                    (self.root / "extra-link").unlink()

    def test_lock_blocks_second_process_and_releases_on_close(self):
        code = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location('launcher', sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
args=m.parser().parse_args(sys.argv[2:])
try:
    with m.prepare(args): pass
except ValueError as error:
    print(error,file=sys.stderr);sys.exit(1)
sys.exit(0)
"""
        with launcher.prepare(self.args) as guest:
            result = subprocess.run([sys.executable, "-c", code, str(SCRIPT), *self.command()],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertIn("already locked", result.stderr)
            # Kernel/initrd read-only; actual disk held read/write.
            self.assertEqual(fcntl.fcntl(guest.fds["kernel"], fcntl.F_GETFL) & os.O_ACCMODE,
                             os.O_RDONLY)
            self.assertEqual(fcntl.fcntl(guest.fds["disk"], fcntl.F_GETFL) & os.O_ACCMODE,
                             os.O_RDWR)
        with launcher.prepare(self.args):
            pass

    def test_refused_launch_releases_all_descriptors_and_lock(self):
        count = self.fd_count()
        with mock.patch.object(launcher, "require_launch_access", side_effect=ValueError("refused")):
            with mock.patch.object(launcher.sys, "stderr"):
                self.assertEqual(launcher.main(self.command()), 1)
        self.assertEqual(self.fd_count(), count)
        with launcher.prepare(self.args):
            pass

    def test_storage_refusal_precedes_payload_reads_and_releases_inputs(self):
        count = self.fd_count()
        with mock.patch.object(launcher.STORAGE, 'verify_guest_storage',
                               side_effect=ValueError('unsupported storage')), \
                mock.patch.object(launcher.os, 'read', side_effect=AssertionError('payload read')):
            with self.assertRaisesRegex(ValueError, 'unsupported storage'):
                launcher.prepare(self.args)
        self.assertEqual(self.fd_count(), count)

    def test_closed_stdin_never_becomes_an_input_descriptor(self):
        code = """
import importlib.util,os,sys
spec=importlib.util.spec_from_file_location('launcher',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
args=m.parser().parse_args(sys.argv[2:]+['--dry-run'])
os.close(0)
with m.prepare(args) as guest:
    assert all(fd>=3 for fd in guest.fds.values()),guest.fds
    try:os.fstat(0)
    except OSError:pass
    else:raise AssertionError('closed stdin was occupied')
"""
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(SCRIPT),
                                 *self.command()], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_changed_storage_after_hash_aborts_before_exec(self):
        count = self.fd_count()
        with mock.patch.object(launcher, 'require_launch_access'), \
                mock.patch.object(launcher.STORAGE, 'verify_guest_storage',
                                  side_effect=[{'fixture': True}, ValueError('changed storage')]) as storage, \
                mock.patch.object(launcher.os, 'execv') as execute, \
                mock.patch.object(launcher.sys, 'stderr'):
            self.assertEqual(launcher.main(self.command()), 1)
        self.assertEqual(storage.call_count, 2)
        execute.assert_not_called()
        self.assertEqual(self.fd_count(), count)
        with launcher.prepare(self.args):
            pass

    def test_failed_exec_releases_inputs(self):
        count = self.fd_count()
        with mock.patch.object(launcher, "require_launch_access"):
            # Do not close unittest's unrelated descriptors in this in-process test.
            with mock.patch.object(launcher.PreparedGuest, "inherit_inputs"):
                with mock.patch.object(launcher.os, "execv", side_effect=OSError("exec failed")):
                    with mock.patch.object(launcher.sys, "stderr"):
                        self.assertEqual(launcher.main(self.command()), 1)
        self.assertEqual(self.fd_count(), count)
        with launcher.prepare(self.args):
            pass

    def test_real_exec_keeps_original_inputs_and_only_input_fds(self):
        consumer = self.root / "consumer"
        consumer.write_text(f"#!{sys.executable}\n" + '''import fcntl, json, os, sys
args = sys.argv[1:]
paths = [args[args.index('-kernel') + 1], args[args.index('-initrd') + 1],
         json.loads(args[args.index('-blockdev') + 1])['file']['filename']]
held = {int(path.rsplit('/', 1)[1]) for path in paths}
for entry in os.listdir('/proc/self/fd'):
    fd = int(entry)
    if fd > 2:
        try: os.fstat(fd)
        except OSError: continue
        assert fd in held, ('unexpected inherited fd', fd)
assert [open(path, 'rb').read() for path in paths] == [b'kernel contents', b'initrd contents', b'disk contents']
# The inherited original disk descriptor still holds the cooperative lock.
fd = os.open(paths[2], os.O_RDWR)
try:
    try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: pass
    else: raise AssertionError('disk lock was not retained through exec')
finally: os.close(fd)
with open(paths[2], 'r+b') as disk: disk.write(b'DISK')
print('original descriptors consumed')
''')
        consumer.chmod(0o755)
        # This hook stands in for hardware checks, never evidence of actual KVM.
        code = """
import importlib.util, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('launcher', sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
sentinel = os.open(sys.argv[2], os.O_RDONLY)
os.set_inheritable(sentinel, True)
m.shutil.which = lambda name: sys.argv[3]
def checked(args):
    for name in ('kernel', 'initrd', 'disk'):
        path = getattr(args, name)
        path.rename(str(path) + '.original')
        path.write_bytes(b'replacement')
m.require_launch_access = lambda args: None
m.STORAGE.verify_guest_storage = lambda *a, **kw: {"fixture": "no hardware"}
inherit = m.PreparedGuest.inherit_inputs
def inherit_and_replace(guest):
    inherit(guest)
    checked(guest.args)
m.PreparedGuest.inherit_inputs = inherit_and_replace
sys.exit(m.main(sys.argv[4:]))
"""
        result = subprocess.run([sys.executable, "-c", code, str(SCRIPT),
                                 str(self.args.kernel), str(consumer), *self.command()],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("original descriptors consumed", result.stdout)
        self.assertEqual(self.args.disk.read_bytes(), b"replacement")
        self.assertEqual(Path(str(self.args.disk) + ".original").read_bytes(), b"DISK contents")


if __name__ == "__main__":
    unittest.main()

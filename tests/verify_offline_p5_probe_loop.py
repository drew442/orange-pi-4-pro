#!/usr/bin/env python3
"""Owned 64 MiB loop/kernel test, never physical NVMe or hardware acceptance.

Run as actual root. Physical board/target/GPT classification is replaced with
exact owned-loop identity. Namespace-wide target census is substituted because
this host has zombie tasks with unreadable mountinfo (EINVAL); self-MNT/holders
checks and actual all-task KVM census remain. Actual block RO, kernel ext4, private MNT child,
mount proof, marker lookup, normal unmount, ioctl/sysfs readback and cleanup run.
Test tools mke2fs/debugfs/losetup are fixture dependencies, not portable runtime
requirements. No project build or SD/physical device operation occurs.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import time
import shutil
import unittest
from unittest.mock import patch

import test_offline_guest_admission as OA
W = OA.W
UUID = '298b281a-d10e-44eb-bdb9-285e27eaafad'
SIZE = 64 * 1024**2


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'requires actual root, loop and MNT privileges')
class OwnedLoopTests(unittest.TestCase):
    guard = OA.OfflineTests.guard

    def setUp(self):
        OA.OfflineTests.setUp(self)
        self.admission = self.guard()
        self.work = Path(tempfile.mkdtemp(prefix='a733-p5-loop-', dir='/run'))
        self.loop = None
        self.addCleanup(self.cleanup_storage)
        self.backing = self.work / 'owned.ext4'
        with self.backing.open('xb') as target:
            os.fchmod(target.fileno(), 0o600)
            target.truncate(SIZE)
        self.blank_identity = W.identity(self.backing.lstat())
        self.seed = self.work / 'seed'
        self.seed.mkdir(mode=0o700)

    def fixture(self, marker=False):
        if marker:
            (self.seed / '.a733-lifecycle').symlink_to('missing-target')
        self.loop = subprocess.check_output(['losetup', '--find', '--show', str(self.backing)],
                                            text=True, timeout=10).strip()
        info = os.lstat(self.loop)
        self.node_identity = info.st_dev, info.st_ino, info.st_rdev
        self.device = os.major(info.st_rdev), os.minor(info.st_rdev)
        self.before = {'nvme0n1p5': self.node_identity}
        self.sys = Path('/sys/class/block') / Path(self.loop).name
        # A reused free loop minor may retain its previous block RO flag. This
        # is a fresh disposable blank backing, owned by this fixture; establish
        # its original writable baseline only after exact identity/idle proof.
        self.prepare_blank_loop()
        subprocess.run(['mke2fs', '-q', '-F', '-t', 'ext4', '-b', '4096',
                        '-L', 'A733GUESTS', '-U', UUID, '-d', str(self.seed),
                        '-E', 'lazy_itable_init=0,lazy_journal_init=0', str(self.backing)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
        subprocess.run(['debugfs', '-w', '-R', 'set_inode_field / mode 040700', str(self.backing)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)
        for p in (patch.object(W, 'DISK', self.loop), patch.object(W, 'P5', self.loop),
                  patch.object(W, 'P5_BYTES', SIZE),
                  patch.object(W, '_offline_inventory', side_effect=self.inventory),
                  patch.object(W, '_p5_fd', side_effect=self.validate),
                  patch.object(W, 'validate_open_target', side_effect=lambda fd, *args: self.validate(fd, None, UUID, 0)),
                  patch.object(W, 'verify_gpt', side_effect=lambda fd: self.exact_node(fd)),
                  patch.object(W, 'idle_target', side_effect=self.idle)):
            p.start()
            self.addCleanup(p.stop)
        # Snapshot after fixture tools have completed. Successful protected probe
        # must leave every byte of this small test backing image unchanged.
        self.digest = hashlib.sha256(self.backing.read_bytes()).hexdigest()

    def prepare_blank_loop(self):
        backing = os.open(self.backing, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        loop = None
        try:
            info = os.fstat(backing)
            W.require(W.identity(info) == self.blank_identity == W.identity(self.backing.lstat())
                      and stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
                      and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
                      and info.st_size == SIZE, 'fresh owned blank backing changed')
            loop = os.open(self.loop, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_EXCL)
            self.exact_node(loop)
            node = os.fstat(loop)
            W.require(stat.S_ISBLK(node.st_mode) and
                      W.read_text(self.sys / 'dev') == '%d:%d' % self.device,
                      'owned loop dev_t/sysfs differs')
            W.require(W.block_integer(loop, W.BLKGETSIZE64, '=Q') == SIZE and
                      W.block_integer(loop, W.BLKSSZGET, '=I') == 512,
                      'fresh owned loop geometry differs')
            self.idle({self.device})
            original = W.block_integer(loop, W.BLKROGET, '=I')
            W.require(original in (0, 1) and W.read_text(self.sys / 'ro') == str(original),
                      'fresh owned loop initial RO unknown')
            fcntl.ioctl(loop, W.BLKROSET, struct.pack('=I', 0))
            self.exact_node(loop)
            W.require(W.block_integer(loop, W.BLKROGET, '=I') == 0 and
                      W.read_text(self.sys / 'ro') == '0', 'fresh owned loop RO reset failed')
        finally:
            if loop is not None:
                os.close(loop)
            os.close(backing)


    def exact_node(self, fd):
        held, named = os.fstat(fd), os.lstat(self.loop)
        W.require((held.st_dev, held.st_ino, held.st_rdev) == self.node_identity ==
                  (named.st_dev, named.st_ino, named.st_rdev), 'owned loop identity changed')
        W.require(W.read_text(self.sys / 'loop/backing_file') == str(self.backing),
                  'owned loop backing changed')

    def validate(self, fd, before, expected_uuid, expected_ro):
        self.exact_node(fd)
        for command, fmt, expected in ((W.BLKGETSIZE64, '=Q', SIZE), (W.BLKSSZGET, '=I', 512),
                                       (W.BLKROGET, '=I', expected_ro)):
            W.require(W.block_integer(fd, command, fmt) == expected, 'owned loop ioctl differs')
        W.require(W.read_text(self.sys / 'ro') == str(expected_ro), 'owned loop sysfs RO differs')
        W._p5_superblock(os.pread(fd, 1024, 1024), expected_uuid)

    def inventory(self, ro):
        fd = os.open(self.loop, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            self.validate(fd, None, UUID, ro)
        finally:
            os.close(fd)
        return self.before, {self.device}

    def idle(self, devices, own_fds=()):
        # Explicitly scoped kernel-loop evidence, not all-namespace acceptance.
        W.require(not any(row['dev'] in devices for row in W.mountinfo()), 'loop still mounted')
        W.require(not list((self.sys / 'holders').iterdir()), 'loop has holders')


    def detach(self):
        # Detach only our retained exact loop/backing pair; refuse foreign reuse.
        if self.loop is not None:
            info = os.lstat(self.loop)
            self.assertEqual((info.st_dev, info.st_ino, info.st_rdev), self.node_identity)
            backing = W.read_text(self.sys / 'loop/backing_file')
            self.assertEqual(backing, str(self.backing))
            self.idle({self.device})
            subprocess.run(['losetup', '--detach', self.loop], check=True, timeout=10)
            # LOOP_CLR_FD may only schedule lazy destruction. Keep the backing
            # and ownership receipt until the kernel confirms disassociation.
            for unused in range(50):
                if not (self.sys / 'loop/backing_file').exists():
                    self.loop = None
                    return
                time.sleep(0.1)
            raise RuntimeError('owned loop detach uncertain; retained ' + str(self.work))

    def cleanup_storage(self):
        # No TemporaryDirectory finalizer may unlink an uncertain live backing.
        self.detach()
        if self.loop is None:
            shutil.rmtree(self.work)

    def test_clean_absence_real_block_ro_private_kernel_mount(self):
        self.fixture()
        result = W.refuse_managed_p5(UUID, self.admission)
        self.assertEqual(result['status'], 'clean-marker-absence')
        self.assertEqual(result['block_ro'], 'restored-original-zero')
        self.assertIsNone(result['point'])
        self.assertEqual(hashlib.sha256(self.backing.read_bytes()).hexdigest(), self.digest)
        self.assertEqual(self.inventory(0), (self.before, {self.device}))
        self.admission.check()

    def test_dangling_marker_real_kernel_lookup_refuses_unchanged_bytes(self):
        self.fixture(marker=True)
        with self.assertRaisesRegex(W.Refusal, 'managed p5 marker') as caught:
            W.refuse_managed_p5(UUID, self.admission)
        self.assertTrue(caught.exception.receipt['marker_present'])
        self.assertTrue(caught.exception.receipt['unmounted'])
        self.assertEqual(caught.exception.receipt['block_ro'], 'restored-original-zero')
        self.assertEqual(hashlib.sha256(self.backing.read_bytes()).hexdigest(), self.digest)
        self.inventory(0)


if __name__ == '__main__':
    unittest.main()

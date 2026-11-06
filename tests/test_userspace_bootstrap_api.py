#!/usr/bin/env python3
"""Guard/ABI regressions only; deliberately performs no mount operation."""
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

spec = importlib.util.spec_from_file_location('helper', str(Path(__file__).resolve().parents[1] / 'build/userspace-bootstrap-api.py'))
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


class Guards(unittest.TestCase):
    def request(self):
        return dict(nonce='a' * 64, namespace_inodes={n: i + 10 for i, n in enumerate(h.NAMES)},
                    host_userns_inode=90, bootstrap_root=str(h.ROOT), host_driver_pid=12345, repo_manifest_sha256='b' * 64)

    def test_request_namespace_and_identity_guards(self):
        request = self.request()
        current = dict(request['namespace_inodes'], user=90)
        with patch.object(h, 'namespaces', return_value=current), patch.object(os, 'getuid', return_value=0), patch.object(os, 'geteuid', return_value=0):
            self.assertEqual(h.validate_request(request), current)
            for bad in (dict(request, bootstrap_root='/tmp/root'), dict(request, nonce='bad'), dict(request, unexpected=1),
                        dict(request, host_driver_pid=True), dict(request, host_userns_inode=13)):
                with self.assertRaises(ValueError):
                    h.validate_request(bad)
            for namespace in ('net', 'cgroup'):
                before = current[namespace]
                current[namespace] = 1
                with self.assertRaisesRegex(ValueError, 'wrong entered namespaces'):
                    h.validate_request(request)
                current[namespace] = before

    def test_authentication_nofollow_hash_regular_and_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'request'
            h.write_new(path, {'a': 1})
            original = path.read_bytes()
            digest = hashlib.sha256(original).hexdigest()
            self.assertEqual(h.authenticated(path, digest)[0], {'a': 1})
            self.assertEqual(path.stat().st_mode & 0o777, 0o444)
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertEqual(set(Path(directory).iterdir()), {path})
            with self.assertRaises(ValueError):
                h.authenticated(path, '0' * 64)
            link = Path(directory) / 'symlink'
            link.symlink_to(path)
            with self.assertRaises(OSError):
                h.authenticated(link)
            with self.assertRaises(FileExistsError):
                h.write_new(path, {})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(set(Path(directory).iterdir()), {path, link})
            hardlink = Path(directory) / 'hardlink'
            os.link(path, hardlink)
            with self.assertRaises(ValueError):
                h.authenticated(path)
            with self.assertRaises(ValueError):
                h.authenticated(Path(directory))

    def test_atomic_publication_failure_cleans_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipt'
            with patch.object(h.json, 'dump', side_effect=ValueError('serialization failed')):
                with self.assertRaises(ValueError):
                    h.write_new(path, {})
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_filtered_sysfs_layout_and_raw_sysfs_rejection(self):
        def record(path, kind):
            return dict(path=path, type=kind, options=['ro', 'nosuid', 'nodev', 'noexec'], optional=[])
        records = [record('/proc', 'proc'), record('/sys', 'tmpfs')]
        records += [record('/sys/' + name, 'sysfs') for name in ('block', 'bus', 'class', 'dev', 'devices', 'kernel')]
        records += [record(path, 'tmpfs') for path in h.MASKS]
        with patch.object(h, 'proc_proof', return_value={'private_pid_inode': 99}), patch.object(h, 'mounts', return_value=records), patch.object(h.os, 'stat', return_value=Mock(st_ino=99)), \
             patch.object(h.os, 'listdir', side_effect=lambda path: ['lo'] if path.endswith('/class/net') else []), \
             patch.object(h, 'identity', return_value=[1, 2, 3]), \
             patch.object(h.os, 'lstat', return_value=Mock(st_mode=0o100000, st_size=0, st_nlink=1)):
            self.assertEqual(h.check_api()['private_pid_inode'], 99)
            records[1]['type'] = 'sysfs'
            with self.assertRaisesRegex(ValueError, 'filesystem type'):
                h.check_api()
            records[1]['type'] = 'tmpfs'
            records.pop(2)
            with self.assertRaisesRegex(ValueError, 'filtered sysfs layout'):
                h.check_api()

    def test_proc_mask_absence_unmasked_and_permissions(self):
        records = [dict(path='/proc', type='proc', options=['ro']), dict(path='/sys', type='tmpfs', options=['ro'])]
        records += [dict(path='/sys/' + name, type='sysfs', options=['ro']) for name in ('block', 'bus', 'class', 'dev', 'devices', 'kernel')]
        records += [dict(path=p, type='tmpfs', options=sorted(h.FLAGS)) for p in h.MASKS]
        meta = Mock(st_mode=0o100000, st_size=0, st_nlink=1)
        absent_path = h.MASKS[0]
        def lstat(path):
            if str(path) == absent_path:
                raise FileNotFoundError()
            return meta
        with patch.object(h, 'proc_proof', return_value={'private_pid_inode': 99}), patch.object(h, 'mounts', return_value=records), patch.object(h.os, 'stat', return_value=Mock(st_ino=99)), \
             patch.object(h.os, 'listdir', side_effect=lambda p: ['lo'] if p.endswith('/class/net') else []), \
             patch.object(h, 'identity', return_value=[1, 2, 3]), patch.object(h.os, 'lstat', side_effect=lstat):
            records[:] = [r for r in records if r['path'] != absent_path]
            self.assertTrue(h.check_api(absent=[absent_path])['masks'][absent_path]['absent'])
            with self.assertRaisesRegex(ValueError, 'unexpected absent'):
                h.check_api()
            with patch.object(h.os, 'lstat', return_value=meta):
                with self.assertRaisesRegex(ValueError, 'became visible'):
                    h.check_api(absent=[absent_path])
                with self.assertRaisesRegex(ValueError, 'missing read-only'):
                    h.check_api()
            with patch.object(h.os, 'lstat', side_effect=PermissionError()):
                with self.assertRaises(PermissionError):
                    h.check_api(absent=[absent_path])
            records.append(dict(path=absent_path, type='tmpfs', options=sorted(h.FLAGS)))
            with patch.object(h.os, 'lstat', return_value=Mock(st_mode=0o100644, st_size=0, st_nlink=1)):
                with self.assertRaisesRegex(ValueError, 'known-empty'):
                    h.check_api()

    def test_proc_mask_setup_abi_and_target_guards(self):
        baseline = {path: dict(absent=True) for path in h.MASKS[:-1]}
        baseline[h.MASKS[0]] = dict(absent=False, mode=0o100444)
        class Libc:
            calls = []
            def mount(self, source, target, fs, flags, data):
                self.calls.append(('mount', source, target, flags.value))
                return 0
            def syscall(self, nr, dirfd, target, flags, attr, size):
                value = ctypes.cast(attr, ctypes.POINTER(h.MountAttr)).contents
                self.calls.append(('setattr', nr.value, dirfd.value, target, flags.value, size.value, value.attr_set))
                return 0
        libc = Libc()
        with tempfile.TemporaryDirectory() as directory, patch.object(h, 'proc_baseline', return_value=baseline), \
             patch.object(h.os, 'fstat', return_value=Mock(st_uid=0, st_gid=0, st_size=0, st_mode=0o100000)):
            known = Path(directory) / 'empty'
            with patch.object(h, 'INACCESSIBLE', known):
                absent = h.setup_masks(baseline, libc)
                self.assertEqual(absent, list(h.MASKS[1:-1]))
                self.assertEqual(known.lstat().st_mode & 0o777, 0)
                self.assertEqual(libc.calls, [('mount', os.fsencode(known), b'/proc/kcore', 4096),
                                             ('mount', None, b'/proc/kcore', 262144),
                                             ('setattr', 442, -100, b'/proc/kcore', 0, 32, 15)])
                baseline[h.MASKS[0]]['mode'] = 0o120777
                with self.assertRaisesRegex(ValueError, 'not real regular'):
                    h.setup_masks(baseline, libc)
                self.assertEqual(len(libc.calls), 3)
                baseline[h.MASKS[0]]['mode'] = 0o100444
                class Denied(Libc):
                    calls = []
                    def mount(self, *args):
                        self.calls.append('denied')
                        ctypes.set_errno(1)
                        return -1
                denied = Denied()
                with patch.object(h, 'INACCESSIBLE', Path(directory) / 'empty-failure'):
                    with self.assertRaisesRegex(ValueError, 'errno=1'):
                        h.setup_masks(baseline, denied)
                self.assertEqual(denied.calls, ['denied'])

    def test_proc_proof_requires_self_pid_namespace_and_authenticated_anchor(self):
        seen = []
        def stat_proc(path):
            seen.append(path)
            if path.endswith('/1/ns/pid'):
                raise PermissionError('restricted PID1 namespace')
            return Mock(st_ino=99)
        identity = [1, 2, h.stat.S_IFDIR]
        anchor = dict(private_pid_inode=99, proc_root_identity=identity, pid1_namespace_check='verified_by_trusted_helper')
        with patch.object(h.os, 'stat', side_effect=stat_proc), patch.object(h, 'identity', return_value=identity), \
             patch.object(h.os, 'readlink', return_value='123'), patch.object(h.os, 'getpid', return_value=123), \
             patch.object(h.Path, 'read_text', return_value='Name: python\nPid:\t123\n'):
            result = h.proc_proof('/fixture', anchor)
            self.assertEqual(result['pid1_namespace_check'], 'not_queried_by_restricted_payload')
            self.assertNotIn('/fixture/proc/1/ns/pid', seen)
            with self.assertRaises(PermissionError):
                h.proc_proof('/fixture')
            for bad in (dict(anchor, private_pid_inode=88), dict(anchor, proc_root_identity=[8, 9, h.stat.S_IFDIR]),
                        dict(anchor, pid1_namespace_check='not_queried_by_restricted_payload')):
                with self.assertRaisesRegex(ValueError, 'anchor mismatch'):
                    h.proc_proof('/fixture', bad)
            with patch.object(h.os, 'readlink', return_value='456'):
                with self.assertRaisesRegex(ValueError, 'self link PID mismatch'):
                    h.proc_proof('/fixture', anchor)
            for text in ('Pid: 456\n', 'Pid: 123\nPid: 123\n', 'Name: python\n'):
                with patch.object(h.Path, 'read_text', return_value=text):
                    with self.assertRaisesRegex(ValueError, 'self status PID mismatch'):
                        h.proc_proof('/fixture', anchor)
            with patch.object(h.os, 'stat', side_effect=lambda path: Mock(st_ino=88 if path.startswith('/fixture') else 99)):
                with self.assertRaisesRegex(ValueError, 'self namespace mismatch'):
                    h.proc_proof('/fixture', anchor)
            with patch.object(h.os, 'stat', return_value=Mock(st_ino=99)):
                self.assertEqual(h.proc_proof('/fixture')['pid1_namespace_check'], 'verified_by_trusted_helper')
            with patch.object(h.Path, 'read_text', side_effect=PermissionError()):
                with self.assertRaises(PermissionError):
                    h.proc_proof('/fixture', anchor)

    def test_host_pid_absence_never_swallows_permission_failure(self):
        with patch.object(h.os, 'lstat', side_effect=FileNotFoundError()):
            h.require_absent('/fixed/proc/123')
        with patch.object(h.os, 'lstat', side_effect=PermissionError()):
            with self.assertRaises(PermissionError):
                h.require_absent('/fixed/proc/123')
        with patch.object(h.os, 'lstat', return_value=Mock()):
            with self.assertRaisesRegex(ValueError, 'visible'):
                h.require_absent('/fixed/proc/123')

    def test_mountattr_exact_abi_and_recursive_sequence(self):
        class Libc:
            def __init__(self):
                self.calls = []
            def mount(self, source, target, fs, flags, data):
                self.calls.append(('mount', source, target, flags.value))
                return 0
            def syscall(self, nr, dirfd, target, flags, attr, size):
                values = ctypes.cast(attr, ctypes.POINTER(h.MountAttr)).contents
                self.calls.append(('setattr', nr.value, dirfd.value, target, flags.value, size.value,
                                   values.attr_set, values.attr_clr, values.propagation, values.userns_fd))
                return 0
        libc = Libc()
        with patch.object(os, 'uname') as uname:
            uname.return_value.machine = 'x86_64'
            h.setup_api(libc)
        self.assertEqual(ctypes.sizeof(h.MountAttr), 32)
        self.assertEqual(len(libc.calls), 6)
        for index, leaf in ((0, '/proc'), (3, '/sys')):
            target = os.fsencode(str(h.ROOT) + leaf)
            self.assertEqual(libc.calls[index], ('mount', os.fsencode(leaf), target, 20480))
            self.assertEqual(libc.calls[index + 1], ('mount', None, target, 278528))
            self.assertEqual(libc.calls[index + 2], ('setattr', 442, -100, target, 32768, 32, 15, 0, 0, 0))

    def test_syscall_failure_stops_without_fallback(self):
        class Denied:
            calls = 0
            def mount(self, *args):
                self.calls += 1
                ctypes.set_errno(1)
                return -1
        libc = Denied()
        with patch.object(os, 'uname') as uname:
            uname.return_value.machine = 'x86_64'
            with self.assertRaisesRegex(ValueError, 'errno=1'):
                h.setup_api(libc)
        self.assertEqual(libc.calls, 1)

    def test_enosys_is_failure(self):
        def unsupported(*args):
            ctypes.set_errno(38)
            return -1
        with self.assertRaisesRegex(ValueError, 'errno=38'):
            h.syscall_checked(None, unsupported)

    def test_target_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'root'
            root.symlink_to(directory)
            with patch.object(h, 'ROOT', root), patch.object(h, 'mounts', return_value=[]):
                with self.assertRaisesRegex(ValueError, 'ancestor'):
                    h.check_targets()

    def test_repository_complete_authenticated_bom(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / 'repo'
            repo.mkdir()
            bom = []
            for number in range(669):
                file = repo / ('file-' + str(number))
                file.write_bytes(b'pin')
                file.chmod(0o444)
                bom.append(dict(path=file.name, bytes=3, sha256=hashlib.sha256(b'pin').hexdigest()))
            manifest = dict(schema=1, status='verified', package_count=220, files=bom,
                            bom_sha256=hashlib.sha256(json.dumps(bom, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
            def publish():
                file = repo / 'manifest.json'
                file.chmod(0o644) if file.exists() else None
                file.write_text(json.dumps(manifest))
                file.chmod(0o444)
                return hashlib.sha256(file.read_bytes()).hexdigest()
            sha = publish()
            with patch.object(h, 'REPO', repo), patch.object(h, 'mounts', return_value=[dict(path='/', type='ext4')]):
                check = lambda: h.check_repository(sha, owner=os.getuid())
                self.assertEqual(check()['file_count'], 670)
                with self.assertRaisesRegex(ValueError, 'authentication'):
                    h.check_repository('0' * 64, owner=os.getuid())
                extra = repo / 'extra'
                extra.write_text('extra')
                with self.assertRaisesRegex(ValueError, 'unexpected/missing'):
                    check()
                extra.unlink()
                extra.symlink_to('file-0')
                with self.assertRaisesRegex(ValueError, 'symlink/special'):
                    check()
                extra.unlink()
                os.mkfifo(extra)
                with self.assertRaisesRegex(ValueError, 'symlink/special'):
                    check()
                extra.unlink()
                first = repo / 'file-0'
                first.chmod(0o644)
                with self.assertRaisesRegex(ValueError, 'unsafe repository file'):
                    check()
                first.write_bytes(b'bad')
                first.chmod(0o444)
                with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                    check()
                first.chmod(0o644)
                first.write_bytes(b'pin')
                first.chmod(0o444)
                os.link(first, extra)
                with self.assertRaisesRegex(ValueError, 'unsafe repository file'):
                    check()
                extra.unlink()
                for unsafe in ('../escape', '/absolute', 'a//b', 'a/./b', 'manifest.json', 'file-1'):
                    bom[0]['path'] = unsafe
                    manifest['bom_sha256'] = hashlib.sha256(json.dumps(bom, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                    sha = publish()
                    with self.assertRaises(ValueError):
                        check()

    def test_targets_require_exact_inert_devices_and_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'root'
            root.mkdir()
            for name in ('proc', 'sys', 'dev', 'dev/pts', 'dev/shm', 'a733-repo'):
                (root / name).mkdir()
            for name in h.DEVICES:
                (root / 'dev' / name).touch(mode=0o666)
                (root / 'dev' / name).chmod(0o666)
            for name, dest in h.ALIASES.items():
                (root / 'dev' / name).symlink_to(dest)
            with patch.object(h, 'ROOT', root), patch.object(h, 'MAPPED_ROOT', os.getuid()), patch.object(h, 'mounts', return_value=[]):
                h.check_targets()
                console = root / 'dev/console'
                console.write_text('unsafe')
                with self.assertRaisesRegex(ValueError, 'placeholder'):
                    h.check_targets()
                console.write_bytes(b'')
                alias = root / 'dev/stdin'
                alias.unlink()
                alias.symlink_to('/etc/passwd')
                with self.assertRaisesRegex(ValueError, 'alias'):
                    h.check_targets()
                alias.unlink()
                alias.symlink_to(h.ALIASES['stdin'])
                (root / 'dev/unexpected').touch()
                with self.assertRaisesRegex(ValueError, 'entry'):
                    h.check_targets()

    def test_fixed_device_and_repo_mount_sequence_and_attributes(self):
        calls = []
        with patch.object(h, 'api_libc', return_value=object()), patch.object(h, 'bind_restrict', side_effect=lambda *args: calls.append(args[1:])):
            h.setup_repository()
            h.setup_devices()
        self.assertEqual(calls[:2], [(str(h.REPO), str(h.REPO), True, 15),
                                    (str(h.REPO), str(h.ROOT / 'a733-repo'), True, 15)])
        self.assertEqual(len(calls), 11)
        for source, target, recurse, attr in calls[2:9]:
            self.assertEqual((recurse, attr), (False, 11))
            self.assertNotIn('console', source)
        self.assertIn(('/dev/pts/ptmx', str(h.ROOT / 'dev/ptmx'), False, 11), calls)
        self.assertEqual(calls[-2:], [('/dev/pts', str(h.ROOT / 'dev/pts'), True, 10),
                                      ('/dev/shm', str(h.ROOT / 'dev/shm'), True, 14)])
        for unsafe in (0, 7, 16, True):
            with self.assertRaisesRegex(ValueError, 'unreviewed'):
                h.bind_restrict(None, '/dev', '/arbitrary', True, unsafe)

    def test_all_mount_attribute_abis_and_no_setattr_fallback(self):
        class Libc:
            def __init__(self, fail=False):
                self.attrs = []
                self.count = 0
                self.fail = fail
            def mount(self, *args):
                self.count += 1
                return 0
            def syscall(self, nr, fd, path, flags, attr, size):
                self.attrs.append((nr.value, flags.value, ctypes.cast(attr, ctypes.POINTER(h.MountAttr)).contents.attr_set))
                ctypes.set_errno(38)
                return -1 if self.fail else 0
        for attr, recursive in ((15, True), (11, False), (10, True), (14, True)):
            libc = Libc()
            h.bind_restrict(libc, '/fixed', '/fixed-copy', recursive, attr)
            self.assertEqual(libc.count, 2)
            self.assertEqual(libc.attrs, [(442, 32768 if recursive else 0, attr)])
        libc = Libc(fail=True)
        with self.assertRaisesRegex(ValueError, 'errno=38'):
            h.bind_restrict(libc, '/fixed', '/fixed-copy', True, 15)
        self.assertEqual(libc.count, 2)
        self.assertEqual(len(libc.attrs), 1)

    def test_source_device_filesystem_guards(self):
        records = []
        for name, kind, supers in (('', 'tmpfs', ['uid=1048576', 'gid=1048576']),
                                   ('pts', 'devpts', ['gid=1048581', 'mode=620', 'ptmxmode=666']),
                                   ('shm', 'tmpfs', ['uid=1048576', 'gid=1048576'])):
            records.append(dict(path='/dev' + ('/' + name if name else ''), type=kind, source=kind,
                                root='/', options=['rw', 'nosuid'], super=supers))
        with patch.object(h, 'real_directory'), patch.object(h, 'mounts', return_value=records):
            for index, key, bad in ((0, 'type', 'ext4'), (1, 'root', '/foreign'), (1, 'super', ['mode=777']), (2, 'options', ['ro'])):
                previous = records[index][key]
                records[index][key] = bad
                with self.assertRaises(ValueError):
                    h.check_devices()
                records[index][key] = previous
            with patch.object(h.os, 'walk', return_value=[('/dev', [], ['sda'])]), patch.object(h.Path, 'lstat', return_value=Mock(st_mode=h.stat.S_IFBLK)):
                with self.assertRaisesRegex(ValueError, 'block/KVM'):
                    h.check_devices()
            with patch.object(h.os, 'walk', return_value=[('/dev', [], ['kvm'])]), patch.object(h.Path, 'lstat', return_value=Mock(st_mode=h.stat.S_IFCHR, st_rdev=os.makedev(10, 232))):
                with self.assertRaisesRegex(ValueError, 'block/KVM'):
                    h.check_devices()

    def test_mount_policy_enforces_private_rw_and_device_access(self):
        record = dict(path='/fixture', options=['ro', 'nosuid', 'noexec'], optional=[])
        flags = os.ST_RDONLY | os.ST_NOSUID | os.ST_NOEXEC
        with patch.object(h.os, 'statvfs', return_value=Mock(f_flag=flags)):
            h.mount_policy([record], '/fixture', 11)
            record['options'].append('nodev')
            with self.assertRaisesRegex(ValueError, 'policy mismatch'):
                h.mount_policy([record], '/fixture', 11)
            record['options'].remove('nodev')
            record['optional'] = ['shared:3']
            with self.assertRaisesRegex(ValueError, 'not private'):
                h.mount_policy([record], '/fixture', 11)

    def test_target_device_numbers_and_authenticated_anchor(self):
        prefix = '/fixture'
        directory = Path(prefix) / 'dev'
        records = []
        for name in h.DEVICES:
            if name != 'console':
                records.append(dict(path=str(directory / name), type='tmpfs', root='/',
                                    options=['ro', 'nosuid', 'noexec'], optional=[]))
        records += [dict(path=str(directory / name), type=kind, root='/',
                         options=options, optional=[]) for name, kind, options in
                    (('pts', 'devpts', ['rw', 'nosuid', 'noexec']),
                     ('shm', 'tmpfs', ['rw', 'nosuid', 'nodev', 'noexec']))]
        def meta(path):
            if Path(path).name in ('pts', 'shm'):
                return Mock(st_mode=h.stat.S_IFDIR)
            numbers = h.DEVICES[Path(path).name]
            return Mock(st_mode=h.stat.S_IFCHR, st_rdev=os.makedev(*numbers))
        def vfs(path):
            name = Path(path).name
            return Mock(f_flag=os.ST_NOSUID | os.ST_NOEXEC |
                        (os.ST_NODEV if name == 'shm' else 0) |
                        (0 if name in ('pts', 'shm') else os.ST_RDONLY))
        with patch.object(h, 'real_directory'), patch.object(h, 'placeholder') as placeholder, patch.object(h, 'check_aliases'), \
             patch.object(h, 'mounts', return_value=records), patch.object(h.os, 'listdir', return_value=list(h.DEVICES) + list(h.ALIASES) + ['pts', 'shm']), \
             patch.object(h.Path, 'lstat', autospec=True, side_effect=meta), patch.object(h.os, 'statvfs', side_effect=vfs), patch.object(h, 'identity', return_value=[1, 2, 3]):
            result = h.check_devices(prefix)
            placeholder.assert_called_with(directory / 'console', h.MAPPED_ROOT)
            self.assertEqual(h.check_devices(prefix, result), result)
            placeholder.assert_called_with(directory / 'console', 0)
            bad = dict(result, devices={})
            with self.assertRaisesRegex(ValueError, 'anchor mismatch'):
                h.check_devices(prefix, bad)
            with patch.object(h.Path, 'lstat', return_value=Mock(st_mode=h.stat.S_IFCHR, st_rdev=os.makedev(1, 99))):
                with self.assertRaisesRegex(ValueError, 'type/numbers'):
                    h.check_devices(prefix)
            records.append(dict(path=str(directory / 'console'), type='tmpfs'))
            with self.assertRaisesRegex(ValueError, 'unexpected target device mount'):
                h.check_devices(prefix)

    def test_walk_errors_are_not_ignored(self):
        with self.assertRaises(PermissionError):
            h.walk_error(PermissionError('unreadable subtree'))


if __name__ == '__main__':
    unittest.main()

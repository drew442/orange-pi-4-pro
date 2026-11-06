"""Freeze fixtures only: never enter a namespace or access the actual builder."""
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import signal
import subprocess
from contextlib import ExitStack
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('freeze_builder', REPO / 'build/freeze-builder.py')
freeze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(freeze)


def make_archive(path, members):
    with tarfile.open(path, 'w', format=tarfile.PAX_FORMAT) as archive:
        for name, kind, uid, gid, target in members:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.uid, member.gid = uid, gid
            member.linkname = target
            member.mode = 0o755 if kind == tarfile.DIRTYPE else 0o644
            archive.addfile(member)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.mapping = patch.object(freeze.seed, 'in_range', return_value=True)
        self.mapping.start()
        self.addCleanup(self.mapping.stop)

    def shifted(self, path):
        pass

    def root_scaffold(self, root, create=False):
        original = Path.lstat
        def owned(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            values = list(info)
            values[4] = values[5] = 0
            return os.stat_result(values)
        with patch.object(Path, 'lstat', owned):
            return freeze.scaffold(root, create=create)

    def test_secret_and_api_headers_kept_without_opening_private_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('dev', 'proc', 'sys', 'run', 'tmp', 'var/tmp', 'root/.ssh',
                         'root/.gnupg', 'etc/ssl/private', 'etc/credstore', 'etc/credstore.encrypted',
                         'etc/ssh', 'var/lib/shim-signed/mok', 'nonexistent/.gnupg'):
                (root / name).mkdir(parents=True, exist_ok=True)
                (root / name / 'secret').write_bytes(b'PRIVATE BYTE FIXTURE')
            (root / 'etc/ssh/ssh_host_rsa_key').write_bytes(b'PRIVATE BYTE FIXTURE')
            (root / 'var/lib/shim-signed/mok/MOK.priv').write_bytes(b'PRIVATE BYTE FIXTURE')
            (root / 'ordinary').write_bytes(b'included')
            self.shifted(root)
            real_open = os.open
            opened = []
            def checked_open(path, *args, **kwargs):
                name = Path(path).relative_to(root).as_posix()
                self.assertFalse(freeze.excluded(name), name)
                opened.append(name)
                return real_open(path, *args, **kwargs)
            with patch.object(freeze.os, 'open', side_effect=checked_open):
                names, data = freeze.snapshot(root)
            self.assertIn('ordinary', opened)
            self.assertIn('root/.ssh', names)
            self.assertIn('etc/ssl/private', names)
            self.assertNotIn('root/.ssh/secret', names)
            self.assertNotIn(b'PRIVATE BYTE FIXTURE', data)
            self.assertNotIn('etc/ssh/ssh_host_rsa_key', names)
            self.assertNotIn('var/lib/shim-signed/mok/MOK.priv', names)

    def test_private_hardlink_alias_refused_before_any_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'etc/ssl/private').mkdir(parents=True)
            (root / 'etc/ssl/private/key').write_bytes(b'never read')
            os.link(root / 'etc/ssl/private/key', root / 'ordinary')
            self.shifted(root)
            with patch.object(freeze.os, 'open') as opened:
                with self.assertRaisesRegex(ValueError, 'aliases excluded private'):
                    freeze.snapshot(root)
                opened.assert_not_called()

    def test_private_symlinks_and_ancestors_refused_before_hash(self):
        for alias in ('header', 'ancestor', 'ssh-key', 'mok-key', 'regular-header', 'private-tree-leaf', 'private-key-directory'):
            with self.subTest(alias=alias), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / 'secret-tree').mkdir()
                (root / 'secret-tree/key').write_bytes(b'never read')
                if alias == 'header':
                    (root / 'etc/ssl').mkdir(parents=True)
                    (root / 'etc/ssl/private').symlink_to('../../secret-tree')
                elif alias == 'ancestor':
                    (root / 'etc').mkdir()
                    (root / 'etc/ssl').symlink_to('../secret-tree')
                elif alias == 'regular-header':
                    (root / 'root').mkdir()
                    (root / 'root/.ssh').write_bytes(b'never read')
                elif alias == 'private-tree-leaf':
                    (root / 'root/.ssh').mkdir(parents=True)
                    (root / 'root/.ssh/key').symlink_to('../../secret-tree/key')
                elif alias == 'private-key-directory':
                    (root / 'etc/ssh/ssh_host_fixture').mkdir(parents=True)
                    (root / 'etc/ssh/ssh_host_fixture/key').write_bytes(b'never read')
                elif alias == 'ssh-key':
                    (root / 'etc/ssh').mkdir(parents=True)
                    (root / 'etc/ssh/ssh_host_rsa_key').symlink_to('../../secret-tree/key')
                else:
                    (root / 'var/lib/shim-signed/mok').mkdir(parents=True)
                    (root / 'var/lib/shim-signed/mok/MOK.priv').symlink_to('../../../../secret-tree/key')
                with patch.object(freeze.os, 'open') as opened:
                    with self.assertRaisesRegex(ValueError, 'Protected directory|Private symlink|Private key leaf'):
                        freeze.snapshot(root)
                    opened.assert_not_called()

    def test_special_nodes_and_outside_mapping_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'ordinary').write_bytes(b'x')
            self.shifted(root)
            with patch.object(freeze.seed, 'in_range', return_value=False):
                with self.assertRaisesRegex(ValueError, 'Unmapped'):
                    freeze.inventory_paths(root)
            os.mkfifo(root / 'fifo')
            with self.assertRaisesRegex(ValueError, 'special'):
                freeze.inventory_paths(root)

    def test_scaffolds_only_create_three_declared_empty_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, directory in freeze.ANCHORS.items():
                if name in freeze.NEW_ANCHORS:
                    continue
                path = root / name
                if directory:
                    path.mkdir(mode=0o755)
                else:
                    path.write_bytes(b'')
                    path.chmod(0o644)
            self.root_scaffold(root, create=True)
            self.assertEqual(set(p.name for p in root.iterdir()), set(freeze.ANCHORS))
            for name in freeze.NEW_ANCHORS:
                self.assertEqual((root / name).stat().st_uid, os.getuid())
            (root / 'a733-install.sh').write_bytes(b'existing data')
            with self.assertRaisesRegex(ValueError, 'Unsafe/nonempty'):
                self.root_scaffold(root, create=True)
            self.assertEqual((root / 'a733-install.sh').read_bytes(), b'existing data')

    def test_scaffold_symlink_or_mapped_owner_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, directory in freeze.ANCHORS.items():
                if directory:
                    (root / name).mkdir(mode=0o755)
                else:
                    (root / name).write_bytes(b'')
                    (root / name).chmod(0o644)
            (root / 'a733-probe.sh').unlink()
            (root / 'a733-probe.sh').symlink_to('a733-install.sh')
            with self.assertRaisesRegex(ValueError, 'runtime anchor'):
                self.root_scaffold(root)


class MetadataTests(unittest.TestCase):
    def test_metadata_preserves_ns_precision_xattrs_and_hardlink_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a').write_bytes(b'bytes')
            os.link(root / 'a', root / 'b')
            (root / 'link').symlink_to('a')
            os.utime(root / 'a', ns=(1793750400123456789, 1793750400123456789))
            os.setxattr(root / 'a', 'user.fixture', b'\x00\xffbinary')
            manifest = freeze.metadata(root, ['.', 'a', 'b', 'link'])
            self.assertEqual(manifest['a']['mtime_ns'], 1793750400123456789)
            self.assertEqual(manifest['a']['hardlinks'], ['a', 'b'])
            self.assertEqual(base64.b64decode(manifest['a']['xattrs']['user.fixture']), b'\x00\xffbinary')
            self.assertNotIn('size', manifest['.'])
            self.assertEqual(manifest['link']['target'], 'a')
            self.assertNotIn('inode', json.dumps(manifest))

    def test_acl_and_capability_mismatches_change_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a').write_bytes(b'bytes')
            def encoded(attrs):
                with patch.object(freeze.os, 'listxattr', return_value=list(attrs)), \
                        patch.object(freeze.os, 'getxattr', side_effect=lambda p, key, **kw: attrs[key]):
                    return freeze.encoded_manifest(root, ['a'])
            attrs = {'system.posix_acl_access': b'acl1', 'security.capability': b'cap1'}
            first = encoded(attrs)
            for key in attrs:
                self.assertNotEqual(first, encoded({**attrs, key: b'different'}))

    def test_manifest_log_refuses_missing_duplicate_and_corrupt_base64(self):
        data = b'{"fixture":{}}\n'
        log = b'A733_BEGIN manifest\n' + base64.b64encode(data) + b'\nA733_END manifest\n'
        self.assertEqual(freeze.parsed_manifest(log), data)
        for bad in (b'', log + log, log.replace(base64.b64encode(data), b'***')):
            with self.assertRaises((ValueError, base64.binascii.Error)):
                freeze.parsed_manifest(bad)


class ArchiveTests(unittest.TestCase):
    def test_canonical_append_and_two_export_equality(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / n for n in ('one.tar', 'two.tar')]
            for path in paths:
                make_archive(path, [('.', tarfile.DIRTYPE, 0, 0, ''),
                                    ('file', tarfile.REGTYPE, 65535, 65535, '')])
                freeze.audit_archive(path, ['.', 'file'], synthetic=False)
                freeze.canonical_append(path)
                self.assertEqual(freeze.audit_archive(path, ['.', 'file'])['members'], 10)
            freeze.verify_exports(*paths)
            with paths[1].open('ab') as stream:
                stream.write(b'corruption')
            with self.assertRaisesRegex(ValueError, 'exports.*differ'):
                freeze.verify_exports(*paths)

    def test_archive_refuses_private_nodes_owners_duplicates_and_links(self):
        cases = [
            [('etc/ssl/private/key', tarfile.REGTYPE, 0, 0, '')],
            [('dev/node', tarfile.REGTYPE, 0, 0, '')],
            [('file', tarfile.REGTYPE, 1048576, 0, '')],
            [('file', tarfile.REGTYPE, 0, 65536, '')],
            [('fifo', tarfile.FIFOTYPE, 0, 0, '')],
            [('../escape', tarfile.REGTYPE, 0, 0, '')],
            [('file', tarfile.REGTYPE, 0, 0, ''), ('file', tarfile.REGTYPE, 0, 0, '')],
            [('link', tarfile.LNKTYPE, 0, 0, '../../escape')],
            [('link', tarfile.SYMTYPE, 0, 0, '../../escape')],
            [('link', tarfile.SYMTYPE, 0, 0, '/etc'), ('link/file', tarfile.REGTYPE, 0, 0, '')],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'archive.tar'
            for members in cases:
                with self.subTest(members=members):
                    make_archive(path, members)
                    with self.assertRaises(ValueError):
                        freeze.audit_archive(path, [m[0] for m in members], synthetic=False)

    def test_canonical_anchor_tampering_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'archive.tar'
            make_archive(path, [('.', tarfile.DIRTYPE, 0, 0, ''),
                                ('a733-export.sh', tarfile.REGTYPE, 0, 0, '')])
            with self.assertRaisesRegex(ValueError, 'member set'):
                freeze.audit_archive(path, ['.'])
            freeze.canonical_append(path)
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                freeze.audit_archive(path, ['.'])


class GuardTests(unittest.TestCase):
    def test_bootstrap_covers_both_siblings_before_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('freeze-builder.py', 'clean-builder.py', 'install-builder-packages.py'):
                (root / name).write_bytes(b'')
            # /tmp itself is writable, so every bootstrap path must fail.
            with self.assertRaisesRegex(ValueError, 'bootstrap'):
                freeze.bootstrap_guard(root / 'freeze-builder.py')
        with patch.object(freeze, 'bootstrap_guard', side_effect=ValueError('untrusted')), \
                patch.object(freeze.importlib.util, 'spec_from_file_location') as load, \
                patch.object(freeze.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(ValueError, 'untrusted'):
                freeze.load_producer()
            load.assert_not_called()

    def test_command_has_fixed_readonly_nonrecursive_source_bind(self):
        source = Path('/var/lib/a733-clean-builder/seed-probe-fixture')
        phase = Path('/var/lib/a733-frozen-builders/freeze-fixture')
        with patch.object(freeze.seed, 'shifted_root_owner'):
            args = freeze.command(source, phase, phase / 'export.sh', phase / 'run1', 'export')
        for flag in ('--read-only', '--private-network', '--no-new-privileges=yes',
                     '--private-users=1048576:65536', '--settings=no'):
            self.assertIn(flag, args)
        self.assertIn('--bind-ro=' + str(source / 'rootfs') + ':/a733-debs:norbind', args)
        self.assertIn('--bind=' + str(phase / 'run1') + ':/a733-export-output', args)
        for action in ('export', 'restore', 'compare', 'inventory'):
            payload = freeze.payload(action)
            self.assertTrue(payload.startswith(freeze.seed.PROBE))
            self.assertLess(payload.index(b'A733_BOUNDARY_PASSED'),
                            payload.index(b'tar --') if action != 'inventory' else payload.index(b'dpkg --audit'))
        self.assertIn(b'--directory=/a733-debs', freeze.payload('export'))
        self.assertIn(b'--null --verbatim-files-from', freeze.payload('export'))
        self.assertNotIn(b'debconf', freeze.boundary())

    def test_repository_guard_precedes_git(self):
        with patch.object(freeze.seed, 'repository_guard', side_effect=ValueError('private')), \
                patch.object(freeze.seed, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'private'):
                freeze.binding(REPO)
            run.assert_not_called()

    def test_output_rejects_overwrite_nested_symlink_and_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / 'workspace'
            workspace.mkdir(mode=0o700)
            source, repo = root / 'source', root / 'repo'
            source.mkdir()
            repo.mkdir()
            with patch.object(freeze, 'WORKSPACE', workspace), \
                    patch.object(freeze.seed, 'root_owned_path'), \
                    patch.object(freeze.producer, 'private_directory'), \
                    patch.object(freeze.seed, 'check_no_mounts'):
                freeze.output_guard(workspace / 'freeze-ok', source, repo)
                (workspace / 'freeze-existing').mkdir()
                (workspace / 'freeze-link').symlink_to(source)
                for path in (workspace / 'freeze-existing', workspace / 'freeze-link',
                             workspace / 'nested/freeze-no', workspace / 'wrongname'):
                    with self.assertRaises(ValueError):
                        freeze.output_guard(path, source, repo)
                with self.assertRaisesRegex(ValueError, 'overlap'):
                    freeze.output_guard(workspace / 'freeze-overlap', workspace, repo)

    def test_failed_or_cancelled_postcheck_revokes_success(self):
        for exception in (ValueError('mapped process'), KeyboardInterrupt()):
            report = {'success': True, 'builder_frozen': True}
            def fail():
                raise exception
            freeze.checks(report, [('post', fail)])
            self.assertFalse(report['success'])
            self.assertFalse(report['builder_frozen'])
            self.assertFalse(report['post_checks']['success'])


class MonitorTests(unittest.TestCase):
    def test_cancellation_thresholds_and_errors_always_reap_process_group(self):
        ample = {'free_bytes': 10 * 1024**3, 'available_memory_bytes': 1024**3}
        for failure in ('disk', 'memory', 'exit', 'interrupt'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                phase = Path(tmp)
                sample = dict(ample)
                if failure == 'disk':
                    sample['free_bytes'] = 1
                if failure == 'memory':
                    sample['available_memory_bytes'] = 1
                process = Mock(pid=12345)
                if failure == 'interrupt':
                    process.wait.side_effect = [KeyboardInterrupt(), 0, 0]
                else:
                    process.wait.return_value = 42 if failure == 'exit' else 0
                with (phase / 'log').open('wb') as log, \
                        patch.object(freeze.producer, 'capacity', return_value=ample), \
                        patch.object(freeze.seed, 'resources', return_value=sample), \
                        patch.object(freeze.subprocess, 'Popen', return_value=process) as popen, \
                        patch.object(freeze.os, 'killpg') as kill:
                    with self.assertRaises((ValueError, KeyboardInterrupt)):
                        freeze.monitor(['fixed'], log, phase, {'resource_samples': []})
                    self.assertTrue(popen.call_args.kwargs['start_new_session'])
                    self.assertEqual([call.args for call in kill.call_args_list],
                                     [(12345, signal.SIGTERM), (12345, signal.SIGKILL)])
                    self.assertGreaterEqual(process.wait.call_count, 2)


class ReceiptTests(unittest.TestCase):
    def exercise(self, root, failure=None, post_failure=False, restore_mismatch=False,
                 inventory_failure=False):
        workspace = root / 'workspace'
        workspace.mkdir()
        source = root / 'source'
        source.mkdir()
        (source / 'rootfs').mkdir(mode=0o700)
        output = workspace / 'freeze-fixture'
        host = {'.': {'mode': 0o700, 'uid': freeze.seed.UID_BASE,
                      'gid': freeze.seed.UID_BASE, 'mtime_ns': 0, 'xattrs': {}, 'type': 'directory'}}
        child = {'.': {**host['.'], 'uid': 0, 'gid': 0}}
        host_bytes = json.dumps(host).encode()
        child_bytes = json.dumps(child).encode()
        def run(args, phase, label, report):
            if failure:
                raise failure
            if label in ('run1', 'run2'):
                make_archive(phase / label / 'builder-root.tar', [('.', tarfile.DIRTYPE, 0, 0, '')])
            data = child_bytes
            if label == 'compare' and restore_mismatch:
                data = json.dumps({'.': {**child['.'], 'xattrs': {'system.posix_acl_access': 'changed'}}}).encode()
            return b'A733_BEGIN manifest\n' + base64.b64encode(data) + b'\nA733_END manifest\n'
        def compress(raw, dest, phase, report):
            dest.write_bytes(b'gzip fixture')
            return freeze.seed.facts(dest)
        original_fstat = os.fstat
        def root_fstat(fd):
            info = original_fstat(fd)
            values = list(info)
            values[4] = 0
            return os.stat_result(values)
        helper = Mock()
        helper.check_builder.return_value = {'profile': 'same'}
        replacements = [
            (freeze, 'WORKSPACE', workspace),
            (freeze, 'binding', Mock(return_value={'files': {}})),
            (freeze, 'validate_producer', Mock(return_value={})),
            (freeze, 'output_guard', Mock()),
            (freeze, 'scaffold', Mock(return_value={})),
            (freeze, 'snapshot', Mock(return_value=(['.'], host_bytes))),
            (freeze, 'raw_estimate', Mock(return_value=10240)),
            (freeze, 'namespace_run', Mock(side_effect=run)),
            (freeze, 'compress', Mock(side_effect=compress)),
            (freeze, 'inventory_paths', Mock(return_value=['.'])),
            (freeze.producer, 'validate_seed', Mock()),
            (freeze.producer, 'inputs', Mock(return_value=({}, set(), b'inventory'))),
            (freeze.producer, 'capture_inventory', Mock(return_value=b'inventory')),
            (freeze.producer, 'host_state', Mock(side_effect=[{'same': True}, {'same': not post_failure}])),
            (freeze.producer, 'capacity', Mock(return_value={})),
            (freeze.producer, 'validate_installed', Mock(return_value=b'installed',
                side_effect=ValueError('Exact 842 inventory rejected') if inventory_failure else None)),
            (freeze.seed, 'load_helper', Mock(return_value=helper)),
            (freeze.seed, 'check_range_unused', Mock()),
            (freeze.seed, 'check_no_mounts', Mock()),
            (freeze.seed, 'check_process_ranges', Mock()),
            (freeze.seed, 'shifted_root_owner', Mock()),
            (freeze.os, 'geteuid', Mock(return_value=0)),
            (freeze.os, 'chown', Mock()),
            (freeze.os, 'fstat', root_fstat),
        ]
        with ExitStack() as stack:
            for obj, name, value in replacements:
                stack.enter_context(patch.object(obj, name, value))
            error = None
            try:
                freeze.freeze(source, output)
            except BaseException as exc:
                error = exc
        return error, json.loads((output / 'receipt.json').read_bytes()), output

    def test_success_is_limited_to_frozen_builder(self):
        with tempfile.TemporaryDirectory() as tmp:
            error, report, output = self.exercise(Path(tmp))
            self.assertIsNone(error)
            self.assertTrue(report['success'])
            self.assertTrue(report['builder_frozen'])
            self.assertFalse(report['artifact_rebuilds'])
            self.assertFalse(report['hardware_acceptance'])
            self.assertTrue((output / 'run1/builder-root.tar').exists())
            self.assertTrue((output / 'restored-rootfs').is_dir())

    def test_failures_preserve_output_and_revoke_success(self):
        cases = [dict(failure=KeyboardInterrupt()), dict(failure=ValueError('export failed')),
                 dict(post_failure=True), dict(restore_mismatch=True), dict(inventory_failure=True)]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as tmp:
                error, report, output = self.exercise(Path(tmp), **kwargs)
                self.assertIsNotNone(error)
                self.assertFalse(report['success'])
                self.assertFalse(report['builder_frozen'])
                self.assertTrue((output / 'files.list').exists())
                if kwargs.get('post_failure'):
                    self.assertFalse(report['post_checks']['success'])
                if kwargs.get('restore_mismatch'):
                    self.assertIn('ACL/xattr mismatch', report['error'])
                if kwargs.get('inventory_failure'):
                    self.assertIn('842 inventory rejected', report['error'])


if __name__ == '__main__':
    unittest.main()

"""Fixture-only checks: never invoke nspawn, install packages, or touch devices."""
import importlib.util
import io
import json
from pathlib import Path
import signal
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('clean_builder', REPO / 'build/clean-builder.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def probe_output(**changes):
    sections = {
        'uid_map': '         0    1048576      65536\n',
        'gid_map': '         0    1048576      65536\n',
        'status': 'CapEff:\t0000000000000000\nCapPrm:\t0000000000000000\n'
                  'CapBnd:\t0000000000000000\nNoNewPrivs:\t1\n',
        'interfaces': 'Inter-| Receive | Transmit\n face |bytes\n lo: 0 0 0\n',
        'routes': 'Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n',
        'routes6': '0' * 32 + ' 00 ' + '0' * 32 + ' 00 ' + '0' * 32 +
                   ' ffffffff 00000001 00000000 00200200 lo\n',
        'devices': '', 'firmware': '', 'mknod': 'denied\n', 'sentinel': 'passed\n',
    }
    sections.update(changes)
    return ''.join('A733_BEGIN ' + key + '\n' + value + 'A733_END ' + key + '\n'
                   for key, value in sections.items()).encode()


class ArchiveTests(unittest.TestCase):
    def audit(self, entries):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'seed.tar.gz'
            with tarfile.open(path, 'w:gz') as stream:
                for name, kind, target in entries:
                    item = tarfile.TarInfo(name)
                    item.type = kind
                    if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                        item.linkname = target
                    stream.addfile(item, io.BytesIO())
            return builder.audit_archive(path)

    def test_regular_absolute_symlink_and_hardlink_are_safe(self):
        result = self.audit([('usr', tarfile.DIRTYPE, ''), ('usr/bin', tarfile.DIRTYPE, ''),
                             ('usr/bin/sh', tarfile.REGTYPE, ''),
                             ('bin', tarfile.SYMTYPE, 'usr/bin'),
                             ('shell', tarfile.SYMTYPE, '/usr/bin/sh'),
                             ('hard', tarfile.LNKTYPE, './usr/bin/sh')])
        self.assertEqual(result['members'], 6)

    def test_archive_escapes_and_special_nodes_refused(self):
        cases = [('../escape', tarfile.REGTYPE, ''), ('/absolute', tarfile.REGTYPE, ''),
                 ('escape', tarfile.SYMTYPE, '../outside'), ('dev/x', tarfile.CHRTYPE, ''),
                 ('dev/x', tarfile.BLKTYPE, ''), ('fifo', tarfile.FIFOTYPE, ''),
                 ('hard', tarfile.LNKTYPE, '/etc/passwd'), ('hard', tarfile.LNKTYPE, '../secret')]
        for entry in cases:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                self.audit([entry])

    def test_symlink_ancestors_refused_in_both_orders(self):
        entries = [('dir', tarfile.SYMTYPE, '/tmp'), ('dir/file', tarfile.REGTYPE, '')]
        for ordered in (entries, entries[::-1]):
            with self.assertRaisesRegex(ValueError, 'non-directory parent'):
                self.audit(ordered)

    def test_hardlink_to_symlink_and_duplicate_refused(self):
        for entries in ([('a', tarfile.SYMTYPE, 'target'), ('b', tarfile.LNKTYPE, 'a')],
                        [('a', tarfile.REGTYPE, ''), ('./a', tarfile.REGTYPE, '')]):
            with self.assertRaises(ValueError):
                self.audit(entries)


class ProbeTests(unittest.TestCase):
    def test_good_isolation_with_loopback_unreachable_route(self):
        result = builder.validate_probe(probe_output())
        self.assertEqual(result['uid_base'], 1048576)
        self.assertEqual(result['network_interfaces'], ['lo'])

    def test_namespace_network_devices_and_write_failures(self):
        cases = [{'uid_map': '0 0 4294967295\n'}, {'gid_map': '0 1048576 65535\n'},
                 {'interfaces': 'lo: 0\neth0: 0\n'},
                 {'routes': 'Iface Destination\neth0 00000000\n'},
                 {'routes6': '0' * 32 + ' 00 ' + '0' * 32 + ' 00 ' + '0' * 32 +
                             ' 00000000 00000001 00000000 00000000 eth0\n'},
                 {'devices': '/dev/kvm\n'}, {'devices': '/dev/loop0\n'},
                 {'firmware': 'EFI present\n'}, {'mknod': 'allowed\n'}, {'sentinel': 'failed\n'}]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError):
                builder.validate_probe(probe_output(**change))

    def test_every_prohibited_bit_in_every_set_is_refused(self):
        for key in ('CapEff', 'CapPrm', 'CapBnd'):
            for bit in (21, 16, 17, 22, 25, 27, 12):
                status = ''.join(k + ': ' + format(1 << bit if k == key else 0, 'x') + '\n'
                                 for k in ('CapEff', 'CapPrm', 'CapBnd')) + 'NoNewPrivs: 1\n'
                with self.subTest(key=key, bit=bit), self.assertRaises(ValueError):
                    builder.validate_probe(probe_output(status=status))

    def test_missing_duplicate_and_no_new_privs_refused(self):
        data = probe_output()
        for bad in (data.replace(b'A733_END uid_map', b'A733_END unknown'),
                    data + b'A733_BEGIN devices\nA733_END devices\n',
                    data.replace(b'NoNewPrivs:\t1', b'NoNewPrivs:\t0')):
            with self.assertRaises(ValueError):
                builder.validate_probe(bad)

    def test_fixed_command_and_readonly_single_bind(self):
        command = builder.nspawn_command(Path('/private/rootfs'), Path('/private/probe.sh'), True)
        self.assertIn('--private-users=1048576:65536', command)
        self.assertIn('--private-users-ownership=chown', command)
        self.assertIn('--private-network', command)
        self.assertIn('--settings=no', command)
        self.assertIn('--inaccessible=/sys/firmware', command)
        self.assertEqual([x for x in command if x.startswith('--bind')],
                         ['--bind-ro=/private/probe.sh:/a733-probe.sh'])
        self.assertEqual(command[-2:], ['/bin/sh', '/a733-probe.sh'])


class HostGuardsTests(unittest.TestCase):
    def test_range_allocations_and_overlap(self):
        builder.validate_identity_tables('root:x:0:0::/:/bin/sh\n', 'root:x:0:\n',
                                         'drew:100000:65536\n', 'drew:100000:65536\n')
        cases = [('x:x:1048576:0::/:/bin/sh', ''), ('x:x:0:1114111::/:/bin/sh', ''),
                 ('', 'x:x:1048576:')]
        for passwd, groups in cases:
            with self.assertRaises(ValueError):
                builder.validate_identity_tables(passwd, groups)
        for start, count in ((1048575, 2), (1048576, 65536), (1114111, 2)):
            with self.assertRaises(ValueError):
                builder.validate_identity_tables('', '', 'user:' + str(start) + ':' + str(count))

    def test_process_effective_saved_and_supplementary_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            (proc / '123').mkdir()
            path = proc / '123/status'
            for value in ('Uid: 0 1048576 0 0\n', 'Gid: 0 0 1114111 0\n', 'Groups: 0 1048576\n'):
                path.write_text(value)
                with self.assertRaises(ValueError):
                    builder.check_process_ranges(proc)
            path.write_text('Uid: 0 0 0 0\nGid: 0 0 0 0\nGroups: 0\n')
            builder.check_process_ranges(proc)

    def test_mounts_include_descendants_and_escaped_paths(self):
        data = '1 2 0:1 / /var/lib/a733-clean-builder/seed-probe-x/rootfs/dev rw - tmpfs tmpfs rw\n'
        self.assertEqual(len(builder.mounts_under(Path('/var/lib/a733-clean-builder/seed-probe-x'), data)), 1)
        self.assertFalse(builder.mounts_under(Path('/var/lib/a733-clean-builder/seed-probe-y'), data))
        self.assertEqual(builder.mounts_under(Path('/tmp/a b'), '1 2 0:1 / /tmp/a\\040b rw - tmpfs tmpfs rw'), ['/tmp/a b'])

    def test_symlink_ancestors_pseudofs_and_nonregular_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'real').mkdir()
            (root / 'link').symlink_to(root / 'real')
            for path in (root / 'link/new', Path('/dev/null'), Path('relative'), root / '../outside'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    builder.safe_path(path)
            with self.assertRaises(ValueError):
                builder.facts(root / 'real')

    def test_arbitrary_output_existing_output_and_seed_overlap_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(builder, 'WORKSPACE', root / 'workspace'):
                for output, seed in ((root / 'other', root / 'seed'),
                                     (root / 'workspace/seed-probe-x', root / 'workspace/seed-probe-x/seed')):
                    with self.assertRaises(ValueError):
                        builder.validate_output(output, seed, root / 'repo')
                (root / 'workspace').mkdir()
                existing = root / 'workspace/seed-probe-existing'
                existing.mkdir()
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    builder.validate_output(existing, root / 'seed', root / 'repo')


class SourceBindingTests(unittest.TestCase):
    def test_repository_guard_precedes_git_commands(self):
        with patch.object(builder, 'repository_guard', side_effect=ValueError('unsafe project')), \
                patch.object(builder, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'unsafe project'):
                builder.binding(Path('/untrusted/repo'))
            run.assert_not_called()

    def test_git_metadata_includes_symlinks_and_alternates_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo / '.git/objects/info').mkdir(parents=True)
            config = repo / '.git/config'
            with patch.object(builder, 'root_owned_path'):
                for text in ('[include]\n path=/untrusted/config\n',
                             '[includeIf "gitdir:/tmp/"]\n path=/untrusted/config\n'):
                    config.write_text(text)
                    with self.assertRaisesRegex(ValueError, 'includes forbidden'):
                        builder.repository_guard(repo)
                config.write_text('[core]\n repositoryformatversion = 0\n')
                (repo / '.git/objects/info/alternates').write_text('/borrowed/objects\n')
                with self.assertRaisesRegex(ValueError, 'Borrowed Git objects'):
                    builder.repository_guard(repo)
            (repo / '.git/objects/info/alternates').unlink()
            config.unlink()
            config.symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):
                builder.repository_guard(repo)

    def test_committed_bytes_checked_even_if_git_status_hides_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            lock = repo / 'lock'
            lock.write_bytes(b'committed seed lock')
            def git(argv):
                operation = argv[argv.index('-C') + 2:]
                if operation == ['rev-parse', '--show-toplevel']:
                    return str(repo).encode() + b'\n'
                if operation == ['rev-parse', 'HEAD']:
                    return b'a' * 40 + b'\n'
                if operation[0] == 'status':
                    return b''
                if operation[0] == 'show':
                    return b'committed seed lock'
                raise RuntimeError('Unexpected Git invocation')
            with patch.object(builder, 'FILES', ('lock',)), \
                    patch.object(builder, 'repository_guard'), patch.object(builder, 'root_owned_path'), \
                    patch.object(builder, 'run', side_effect=git) as run:
                record = builder.binding(repo)
                self.assertEqual(record['files']['lock'], builder.facts(lock))
                self.assertTrue(all('core.fsmonitor=false' in call.args[0] for call in run.call_args_list))
                lock.write_bytes(b'modified but hidden by index flags')
                with self.assertRaisesRegex(ValueError, 'differs from commit'):
                    builder.binding(repo)


class ManagedCommandTests(unittest.TestCase):
    def test_failed_command_and_interrupt_wait_before_return(self):
        for failure in (False, True):
            process = Mock(pid=123, returncode=2)
            process.wait.side_effect = [KeyboardInterrupt(), 2, 2] if failure else [2, 2, 2]
            with patch.object(builder.subprocess, 'Popen', return_value=process) as popen, \
                    patch.object(builder.os, 'killpg') as killpg, \
                    tempfile.TemporaryFile() as log:
                with self.assertRaises(KeyboardInterrupt if failure else ValueError):
                    builder.managed_command(['/fixed/command'], log)
                self.assertTrue(popen.call_args.kwargs['start_new_session'])
                self.assertEqual(killpg.call_args_list[-1].args, (123, signal.SIGKILL))
                self.assertGreaterEqual(process.wait.call_count, 2)


class ReceiptTests(unittest.TestCase):
    def construct_fixture(self, mode):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / 'workspace'
            workspace.mkdir()
            repo = root / 'repo'
            (repo / 'configs').mkdir(parents=True)
            seed = root / 'seed.tar.gz'
            seed.write_bytes(b'fixture seed')
            seed_facts = builder.facts(seed)
            (repo / builder.LOCK).write_text(json.dumps({'schema': 1, 'architecture': 'amd64', 'archive': seed_facts}))
            (repo / 'configs/builder-replay-packages-20261106.lock.tsv').write_bytes(b'fixture inventory')
            output = workspace / 'seed-probe-fixture'
            helper = types.SimpleNamespace(check_builder=Mock(return_value={'profile': 'replay'}),
                                           package_rows=Mock(return_value=dict.fromkeys(range(842))))
            def command(argv, log):
                if argv[0] == '/usr/bin/tar':
                    (output / 'rootfs/tmp').mkdir()
                else:
                    log.write(probe_output())
                    if mode == 'probe_failure':
                        raise ValueError('fixture probe failure')
            def check_mounts(path):
                if mode == 'mount_failure':
                    raise ValueError('fixture surviving mount')
            with patch.multiple(builder, WORKSPACE=workspace, SEED=seed_facts), \
                    patch.object(builder, 'validate_output', return_value={'free_bytes': 25000000001}), patch.object(builder, 'binding', return_value={'head': 'fixture'}), \
                    patch.object(builder, 'load_helper', return_value=helper), patch.object(builder.os, 'geteuid', return_value=0), \
                    patch.object(builder, 'check_range_unused'), patch.object(builder, 'check_process_ranges'), \
                    patch.object(builder, 'check_no_mounts', side_effect=check_mounts), \
                    patch.object(builder, 'audit_archive', return_value={'fixture': True}), \
                    patch.object(builder, 'run', side_effect=lambda argv: b'GNU tar' if '--version' in argv else b'--inaccessible='), \
                    patch.object(builder, 'managed_command', side_effect=command):
                if mode == 'success':
                    builder.construct(seed, output, repo)
                else:
                    with self.assertRaises(ValueError):
                        builder.construct(seed, output, repo)
            receipt = json.loads((output / 'receipt.json').read_text())
            self.assertTrue((output / 'rootfs/tmp').is_dir())
            self.assertTrue((output / 'probe.log').exists())
            self.assertEqual((output / 'probe.sh').stat().st_mode & 0o777, 0o444)
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertEqual((output / 'source-lock.json').read_bytes(), (repo / builder.LOCK).read_bytes())
            self.assertFalse(receipt['complete_clean_builder'])
            self.assertFalse(receipt['hardware_acceptance'])
            self.assertEqual(helper.check_builder.call_count, 2)
            return receipt

    def test_success_is_only_seed_and_probe(self):
        receipt = self.construct_fixture('success')
        self.assertTrue(receipt['success'])
        self.assertTrue(receipt['ownership_shift']['persistent'])

    def test_probe_failure_keeps_evidence_and_checks_host_inventory(self):
        receipt = self.construct_fixture('probe_failure')
        self.assertFalse(receipt['success'])
        self.assertIn('fixture probe failure', receipt['error'])
        self.assertTrue(receipt['post_checks']['success'])

    def test_postrun_mount_failure_revokes_success(self):
        receipt = self.construct_fixture('mount_failure')
        self.assertFalse(receipt['success'])
        self.assertIn('fixture surviving mount', receipt['post_checks']['errors'][0])


if __name__ == '__main__':
    unittest.main()

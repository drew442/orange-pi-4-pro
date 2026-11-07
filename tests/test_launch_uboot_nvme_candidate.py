"""Frozen component launcher guards; no actual compiler/device acceptance."""
from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import tarfile
import types
import unittest
from unittest import mock
import zlib

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('launch_nvme', REPO / 'build/launch-uboot-nvme-candidate.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def image(source):
    payload = struct.pack('>2I', len(source), 0) + source
    header = struct.pack('>7I4B32s', 0x27051956, 0, 1793750400, len(payload), 0, 0,
                         zlib.crc32(payload), 5, 2, 6, 0, bytes(32))
    return header[:4] + struct.pack('>I', zlib.crc32(header)) + header[8:] + payload


class LauncherTests(unittest.TestCase):
    def test_tag_and_resource_bounds(self):
        for tag in ('', '../old', '/old', 'a b', '-old', 'a' * 65):
            with self.assertRaises(ValueError): M.phase_path(tag)
        self.assertEqual(M.phase_path('first_01').name, 'uboot-nvme-component-first_01')
        good = {'free_bytes': 3_000_000_000, 'available_memory_bytes': 1_000_000_000}
        M.check_resources(good, M.TIMEOUT)
        for bad, elapsed in [(dict(good, free_bytes=2_999_999_999), 0),
                             (dict(good, available_memory_bytes=999_999_999), 0), (good, M.TIMEOUT + 1)]:
            with self.assertRaises(ValueError): M.check_resources(bad, elapsed)

    def test_plan_git_closure_and_no_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            for name in M.SOURCE_FILES:
                path = project / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((REPO / name).read_bytes())
            for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=Fixture', '-c',
                         'user.email=f@example.invalid', 'commit', '-qm', 'Fixture']):
                subprocess.run(['git', '-C', str(project)] + args, check=True, capture_output=True)
            before = sorted(str(p.relative_to(project)) for p in project.rglob('*'))
            args = [sys.executable, '-I', '-B', str(project / M.SELF), '--project', str(project), '--tag', 'fixture', '--plan']
            result = subprocess.run(args, check=True, capture_output=True, text=True)
            plan = json.loads(result.stdout)
            self.assertEqual(plan['compilation_jobs'], 2)
            self.assertFalse(plan['spi_image_or_programmer'])
            self.assertEqual(plan['minimum_initial_free_bytes_exclusive'], 4_000_000_000)
            self.assertEqual(before, sorted(str(p.relative_to(project)) for p in project.rglob('*')))
            ram_plan = subprocess.run(args + ['--ram-work'], check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(ram_plan.stdout)['work_mode'], 'ram')
            replay = subprocess.run(args + ['--ram-work', '--replay-target', M.REGISTERED_TARGET],
                                    check=True, capture_output=True, text=True)
            registered = json.loads(replay.stdout)
            self.assertEqual(registered['challenge_mode'], 'registered')
            self.assertEqual(registered['registered_replay']['target_id'], M.REGISTERED_TARGET)
            self.assertIn('no second full archive', registered['ram_retention'])
            self.assertEqual(before, sorted(str(p.relative_to(project)) for p in project.rglob('*')))
            (project / M.SCRIPT).write_bytes(b'changed')
            rejected = subprocess.run(args, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('source differs from Git blob', rejected.stderr)

    def test_namespace_readonly_inputs_and_fixed_inner_recipe(self):
        shared = types.SimpleNamespace(ROOT=Path('/rootfs'), SEED=Path('/seed'))
        builder = types.SimpleNamespace(nspawn_command=lambda *a: ['nspawn', '--private-users-ownership=chown', 'old', 'tail'])
        args = M.command(shared, builder, Path('/phase'))
        self.assertIn('--read-only', args)
        self.assertIn('--private-users-ownership=off', args)
        self.assertIn('--bind-ro=' + str(M.SOURCE_HOME) + ':/home', args)
        self.assertIn('--bind-ro=' + str(M.PROVENANCE) + ':' + str(M.PROVENANCE), args)
        self.assertIn('--bind=/phase/work:/a733-export-output', args)
        runner = M.inner_program('a' * 40).decode()
        compile(runner, '<inner>', 'exec')
        self.assertIn("'--builder-profile','replay-20261106'", runner)
        self.assertIn("'--output',str(output)", runner)
        self.assertNotIn('flashcp', runner)
        self.assertNotIn('/dev/mtd', runner)
        with self.assertRaises(ValueError): M.inner_program('not a revision')

    def test_bundle_head_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; source.mkdir()
            (source / 'file').write_text('source')
            for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=Fixture', '-c',
                         'user.email=f@example.invalid', 'commit', '-qm', 'Fixture']):
                subprocess.run(['git', '-C', str(source)] + args, check=True, capture_output=True)
            revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD']).decode().strip()
            bundle = root / 'source.bundle'; clone = root / 'clone'
            subprocess.run(['git', '-C', str(source), 'bundle', 'create', str(bundle), 'HEAD'], check=True, capture_output=True)
            subprocess.run(['git', 'clone', '--no-checkout', str(bundle), str(clone)], check=True, capture_output=True)
            subprocess.run(['git', '-C', str(clone), 'checkout', '--detach', revision], check=True, capture_output=True)
            self.assertEqual((clone / 'file').read_bytes(), b'source')
            self.assertEqual(subprocess.check_output(['git', '-C', str(clone), 'status', '--porcelain']), b'')

    def test_independent_script_crc_payload_epoch_and_arch_guards(self):
        source = b'echo candidate\nfalse\n'
        good = image(source)
        checked = M.verify_host_script(good, source)
        self.assertTrue(checked['crc_verified'])
        self.assertEqual(checked['script'], M.pin(good))
        for offset in (0, 4, 8, 12, 16, 20, 24, 28, 29, 30, 31, 32, 64, 68, 72):
            bad = bytearray(good); bad[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                M.verify_host_script(bytes(bad), source)
        for bad in (good[:60], good[:-1], good + b'\0'):
            with self.assertRaises(ValueError): M.verify_host_script(bad, source)
        with self.assertRaises(ValueError): M.verify_host_script(good, b'changed')


class RamWorkTests(unittest.TestCase):
    sample = {'free_bytes': 5_000_000_000, 'available_memory_bytes': 5 * 1024**3}

    def shared(self, phase):
        def atomic(path, data):
            with path.open('x') as stream:
                json.dump(data, stream)
        return types.SimpleNamespace(
            UID_BASE=1048576, INVENTORY=['fixture-inventory'],
            safe_directory=lambda *a, **kw: None,
            resource_sample=mock.Mock(return_value=dict(self.sample)),
            mapped_processes=mock.Mock(return_value=[]),
            phase_mounts=mock.Mock(return_value=[self.mountline(phase)]),
            regular_bytes=lambda path, **kw: path.read_bytes(),
            atomic_json=atomic, stop_process=mock.Mock(), interrupted=lambda *a: None)

    def mountline(self, phase):
        return '30 20 0:60 / ' + str(phase / 'work') + ' rw,nosuid,nodev - tmpfs tmpfs rw'

    def test_ram_plan_and_initial_memory_admission(self):
        plan = M.plan('ram-01', {}, True)
        self.assertEqual(plan['work_mode'], 'ram')
        self.assertEqual(plan['ram_work_limit_bytes'], 8 * 1024**3)
        self.assertEqual(plan['minimum_initial_available_memory_bytes'], 4 * 1024**3)
        self.assertEqual(M.plan('disk-01', {})['work_mode'], 'disk')
        M.check_admission(dict(self.sample, available_memory_bytes=4 * 1024**3), True)
        with self.assertRaisesRegex(ValueError, '4 GiB'):
            M.check_admission(dict(self.sample, available_memory_bytes=4 * 1024**3 - 1), True)
        M.check_admission(dict(self.sample, available_memory_bytes=0), False)
        for ram in (False, True):
            with self.assertRaisesRegex(ValueError, '4 GB'):
                M.check_admission(dict(self.sample, free_bytes=4_000_000_000), ram)

    def test_fresh_exact_mount_and_refuse_existing_work(self):
        with tempfile.TemporaryDirectory() as directory:
            phase = Path(directory); work = phase / 'work'; work.mkdir()
            shared = self.shared(phase); shared.phase_mounts.return_value = []
            with mock.patch.object(M.subprocess, 'run') as run, mock.patch.object(M, 'work_mount_identity', return_value=['id']):
                self.assertEqual(M.mount_ram_work(shared, phase), ['id'])
                self.assertEqual(run.call_args.args[0], ['mount', '-t', 'tmpfs', '-o',
                    'size=8G,uid=1048576,gid=1048576,mode=0700,nodev,nosuid', 'tmpfs', str(work)])
                (work / 'old-build').write_text('keep')
                run.reset_mock()
                with self.assertRaisesRegex(ValueError, 'fresh'):
                    M.mount_ram_work(shared, phase)
                run.assert_not_called()
                self.assertEqual((work / 'old-build').read_text(), 'keep')

    def test_auxiliary_resource_abort_stops_process(self):
        with tempfile.TemporaryDirectory() as directory:
            phase = Path(directory); shared = self.shared(phase)
            shared.resource_sample.return_value = dict(self.sample, available_memory_bytes=999_999_999)
            proc = mock.Mock()
            with mock.patch.object(M.subprocess, 'Popen', return_value=proc):
                with self.assertRaisesRegex(ValueError, 'resource floor'):
                    M.monitored(shared, ['tar'], phase, {}, 'fixture')
            shared.stop_process.assert_called_once_with(proc)
            proc.wait.assert_not_called()

    def populate_work(self, phase):
        work = phase / 'work'; work.mkdir(exist_ok=True)
        component = work / 'project/out/uboot-nvme-candidate-01'
        (component / 'artifacts').mkdir(parents=True)
        (component / 'logs').mkdir()
        (component / 'artifacts/result').write_text('review artifact')
        (component / 'candidate-manifest.json').write_text('{}')
        (work / 'host-script').mkdir()
        (work / 'host-script/script').write_text('script')
        (work / 'compiler').mkdir()
        original = work / 'compiler/original'
        original.write_bytes(b'full compiler bytes')
        os.link(original, work / 'compiler/hardlink')
        (work / 'compiler/symlink').symlink_to('original')
        os.utime(original, ns=(1_700_000_000_123456789, 1_700_000_000_123456789))
        try:
            os.setxattr(original, 'user.retention-fixture', b'preserved')
        except OSError:
            pass
        return work

    def test_verified_full_archive_and_review_copy_precede_exact_unmount(self):
        with tempfile.TemporaryDirectory() as directory:
            phase = Path(directory); work = self.populate_work(phase)
            shared = self.shared(phase); report = {'success': True, 'mounted_work_path': str(work)}
            # Exercise the real exclusive rename publication; fchown alone needs root.
            shared.atomic_json = M.module(M.SHARED, (REPO / M.SHARED).read_bytes()).atomic_json
            run = M.subprocess.run
            unmount = []
            def intercept(args, **kw):
                if args[0] == 'umount':
                    unmount.append(args)
                    self.assertTrue(report['ram_retention']['full_tar_compare_verified'])
                    self.assertIsNone(report.get('retention_error'))
                    self.assertFalse((phase / 'receipt.json').exists())
                    saved = json.loads((phase / 'retained-awaiting-unmount.json').read_text())
                    self.assertFalse(saved['success'])
                    self.assertEqual(saved['state'], 'retained-awaiting-unmount')
                    self.assertEqual(saved['ram_retention']['sha256'],
                                     M.pin((phase / 'work.tar.gz').read_bytes())['sha256'])
                    self.assertTrue((phase / 'component-retained/artifacts/result').is_file())
                    return types.SimpleNamespace(returncode=0)
                return run(args, **kw)
            with mock.patch.object(M, 'work_mount_identity', return_value=['id']), \
                 mock.patch.object(M.subprocess, 'run', side_effect=intercept), mock.patch.object(M.time, 'sleep'), \
                 (mock.patch.object(M.os, 'fchown') if os.geteuid() else nullcontext()):
                M.retain_ram_work(shared, phase, report, ['id'])
                shared.atomic_json(phase / 'receipt.json', dict(report, success=True, state='complete'))
                with self.assertRaises(FileExistsError):
                    shared.atomic_json(phase / 'receipt.json', {'must_not_replace': True})
            self.assertTrue(json.loads((phase / 'receipt.json').read_text())['success'])
            self.assertFalse(json.loads((phase / 'retained-awaiting-unmount.json').read_text())['success'])
            self.assertEqual(unmount, [['umount', '--', str(work)]])
            self.assertIsNone(report['mounted_work_path'])
            with tarfile.open(phase / 'work.tar.gz') as archive:
                original = archive.getmember('./compiler/original')
                hardlink = archive.getmember('./compiler/hardlink')
                self.assertTrue(original.islnk() or hardlink.islnk())
                self.assertTrue(archive.getmember('./compiler/symlink').issym())
                regular = hardlink if original.islnk() else original
                self.assertEqual(regular.pax_headers['mtime'], '1700000000.123456789')
                if 'user.retention-fixture' in os.listxattr(work / 'compiler/original'):
                    self.assertEqual(regular.pax_headers['SCHILY.xattr.user.retention-fixture'], 'preserved')

    def test_any_retention_command_failure_never_unmounts(self):
        for failure in ('retention-archive', 'retention-compare', 'retention-copy-0', 'retention-sha256'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                phase = Path(directory); self.populate_work(phase)
                shared = self.shared(phase); report = {'mounted_work_path': str(phase / 'work')}
                real = M.monitored
                def monitored(shared, args, phase, report, label):
                    if label == failure:
                        raise ValueError('injected ' + failure)
                    return real(shared, args, phase, report, label)
                with mock.patch.object(M, 'work_mount_identity', return_value=['id']), \
                     mock.patch.object(M, 'monitored', side_effect=monitored), \
                     mock.patch.object(M.subprocess, 'run') as run, mock.patch.object(M.time, 'sleep'):
                    with self.assertRaisesRegex(ValueError, 'injected'):
                        M.retain_ram_work(shared, phase, report, ['id'])
                    run.assert_not_called()
                self.assertEqual(report['mounted_work_path'], str(phase / 'work'))

    def test_review_fsync_postorder_skips_symlinks_and_refuses_special_files(self):
        with tempfile.TemporaryDirectory() as directory:
            phase = Path(directory); retained = phase / 'retained'; retained.mkdir()
            child = retained / 'child'; child.mkdir()
            regular = child / 'file'; regular.write_text('durable')
            outside = phase / 'outside'; outside.write_text('do not follow')
            (child / 'link').symlink_to(outside)
            order = []
            fsync = os.fsync
            def sync(fd):
                order.append(os.fstat(fd).st_ino)
                fsync(fd)
            with mock.patch.object(M.os, 'fsync', side_effect=sync):
                M.fsync_review_tree(retained)
            self.assertEqual(order, [regular.stat().st_ino, child.stat().st_ino, retained.stat().st_ino])
            os.mkfifo(child / 'fifo')
            with self.assertRaisesRegex(ValueError, 'special file'):
                M.fsync_review_tree(retained)
            with self.assertRaises(OSError):
                M.fsync_review_tree(child / 'link')

    def test_durability_failure_never_unmounts_ram(self):
        for operation in ('review', 'archive', 'phase-before-record', 'phase-after-record'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                phase = Path(directory); self.populate_work(phase)
                shared = self.shared(phase); report = {'mounted_work_path': str(phase / 'work')}
                sync_tree = M.fsync_review_tree
                sync_directory = M.fsync_directory
                sync_fd = os.fsync
                calls = []
                def tree(path):
                    if operation == 'review':
                        raise OSError('injected review fsync failure')
                    sync_tree(path)
                def directory(path):
                    calls.append(path)
                    target = 1 if operation == 'phase-before-record' else 2
                    if operation.startswith('phase-') and len(calls) == target:
                        raise OSError('injected directory fsync failure')
                    sync_directory(path)
                def file(fd):
                    if operation == 'archive' and os.readlink('/proc/self/fd/' + str(fd)) == str(phase / 'work.tar.gz'):
                        raise OSError('injected archive fsync failure')
                    sync_fd(fd)
                with mock.patch.object(M, 'work_mount_identity', return_value=['id']), \
                     mock.patch.object(M, 'fsync_review_tree', side_effect=tree), \
                     mock.patch.object(M, 'fsync_directory', side_effect=directory), \
                     mock.patch.object(M.os, 'fsync', side_effect=file), \
                     mock.patch.object(M.subprocess, 'run') as run, mock.patch.object(M.time, 'sleep'):
                    with self.assertRaisesRegex(OSError, 'fsync failure'):
                        M.retain_ram_work(shared, phase, report, ['id'])
                    run.assert_not_called()
                self.assertEqual(report['mounted_work_path'], str(phase / 'work'))
                self.assertEqual((phase / 'retained-awaiting-unmount.json').exists(), operation == 'phase-after-record')

    def successful_payload(self, work, source):
        artifact = work / 'project/out/uboot-nvme-candidate-01/artifacts'
        artifact.mkdir(parents=True)
        (artifact / 'candidate-result.json').write_text(json.dumps({
            'component_build_success': True, 'hardware_acceptance': False, 'deployment_authorized': False}))
        scripts = work / 'host-script'; scripts.mkdir()
        script = image(source)
        (scripts / 'spi-nvme-host-boot.scr').write_bytes(script)
        (scripts / 'receipt.json').write_text(json.dumps({'success': True, 'source': M.pin(source), 'script': M.pin(script)}))

    def test_failed_staging_journals_retention_error_and_restores_handlers(self):
        with tempfile.TemporaryDirectory() as directory:
            phase = Path(directory) / 'fresh'; shared = self.shared(phase)
            shared.phase_mounts.return_value = [self.mountline(phase)]
            report = {}; failure = RuntimeError('bundle staging failed')
            handlers = {}
            def signals(sig, handler):
                previous = handlers.get(sig, 'original')
                handlers[sig] = handler
                return previous
            with mock.patch.object(M.os, 'chown'), mock.patch.object(M.signal, 'signal', side_effect=signals), \
                 mock.patch.object(M, 'mount_ram_work', return_value=['id']), \
                 mock.patch.object(M.subprocess, 'run', side_effect=failure), \
                 mock.patch.object(M.subprocess, 'check_output', return_value=b'inventory'), \
                 mock.patch.object(M, 'retain_ram_work', side_effect=ValueError('archive failed')):
                with self.assertRaises(RuntimeError) as raised:
                    M.execute(shared, None, Path('/project'), phase, {'revision': 'a' * 40}, {}, b'inventory', report, True)
                self.assertIs(raised.exception, failure)
            receipt = json.loads((phase / 'receipt.json').read_text())
            self.assertFalse(receipt['success'])
            self.assertEqual(receipt['state'], 'failed')
            self.assertIn('bundle staging failed', receipt['error'])
            self.assertIn('archive failed', receipt['retention_error'])
            self.assertEqual(receipt['mounted_work_path'], str(phase / 'work'))
            self.assertTrue(all(value == 'original' for value in handlers.values()))

    def test_success_requires_post_retention_root_disk_above_four_gb(self):
        for final_free, passes in ((4_000_000_000, False), (4_000_000_001, True)):
            with self.subTest(final_free=final_free), tempfile.TemporaryDirectory() as directory:
                phase = Path(directory) / 'fresh'; shared = self.shared(phase)
                shared.phase_mounts.return_value = []
                source = b'echo fixture\n'
                report = {}
                def mount(shared, phase):
                    self.successful_payload(phase / 'work', source)
                    return ['id']
                def run(args, **kw):
                    if args[0] == 'git':
                        Path(args[-2]).write_bytes(b'bundle')
                def retain(shared, phase, report, identity):
                    report['mounted_work_path'] = None
                    shared.resource_sample.return_value = dict(self.sample, free_bytes=final_free)
                proc = mock.Mock(returncode=0)
                proc.poll.return_value = proc.wait.return_value = 0
                builder = types.SimpleNamespace(validate_probe=lambda data: {'valid': True})
                with mock.patch.object(M.os, 'chown'), mock.patch.object(M, 'mount_ram_work', side_effect=mount), \
                     mock.patch.object(M, 'command', return_value=['fixture-payload']), \
                     mock.patch.object(M.subprocess, 'run', side_effect=run), \
                     mock.patch.object(M.subprocess, 'Popen', return_value=proc), \
                     mock.patch.object(M.subprocess, 'check_output', return_value=b'inventory'), \
                     mock.patch.object(M, 'retain_ram_work', side_effect=retain):
                    if passes:
                        M.execute(shared, builder, Path('/project'), phase, {'revision': 'a' * 40},
                                  {M.SCRIPT: source}, b'inventory', report, True)
                    else:
                        with self.assertRaisesRegex(ValueError, 'final cleanup'):
                            M.execute(shared, builder, Path('/project'), phase, {'revision': 'a' * 40},
                                      {M.SCRIPT: source}, b'inventory', report, True)
                self.assertEqual(json.loads((phase / 'receipt.json').read_text())['success'], passes)

    def test_existing_phase_refused_without_journal_or_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            phase = Path(directory); (phase / 'old-result').write_text('preserve me')
            shared = self.shared(phase)
            with mock.patch.object(M, 'mount_ram_work') as mount:
                with self.assertRaises(FileExistsError):
                    M.execute(shared, None, Path('/project'), phase, {}, {}, b'', {}, True)
                mount.assert_not_called()
            self.assertEqual((phase / 'old-result').read_text(), 'preserve me')
            self.assertFalse((phase / 'receipt.json').exists())


class RegisteredReplayTests(unittest.TestCase):
    shared = RamWorkTests.shared
    mountline = RamWorkTests.mountline
    sample = RamWorkTests.sample

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.old = self.root / 'original/component-retained'
        self.old.mkdir(parents=True)
        self.archive = self.old.parent / 'work.tar.gz'
        self.archive.write_bytes(b'old full workspace authority')
        for name, value in [('ORIGINAL_ROOT', self.old), ('ORIGINAL_ARCHIVE', self.archive),
                            ('ARCHIVE_PIN', M.pin(self.archive.read_bytes()))]:
            patch = mock.patch.object(M, name, value)
            patch.start(); self.addCleanup(patch.stop)
        self.phase = self.root / 'repeat'
        self.phase.mkdir()
        self.write(self.phase / 'inputs/project.bundle', b'complete new producer Git bundle')
        self.write(self.phase / 'inputs/run.py', b'new authenticated runner')
        self.work = self.phase / 'work'
        self.component = self.work / 'project/out/uboot-nvme-candidate-01'
        self.component.mkdir(parents=True)
        self.source = {'revision': 'a' * 40, 'files': {
            name: dict(M.pin(('current ' + name).encode()), git_blob='b' * 40)
            for name in (M.COMPONENT, M.REGISTRY, M.EVIDENCE)}}
        self.original_provenance = {
            'builder_profile': {'name': 'replay-20261106'}, 'policy': {'schema': 1},
            'baseline_manifest_sha256': 'c' * 64, 'core_recipe_sha256': 'd' * 64,
            'firmware_unchanged': {'monitor.fex': M.pin(b'firmware')}}
        self.old_files = {name: ('bound ' + name).encode() for name in M.MATCHED_ARTIFACTS}
        self.old_files.update({'artifacts/original-uboot.config': b'original config',
                               'artifacts/source-provenance.json': self.json_bytes(self.original_provenance),
                               'artifacts/uboot.elf': b'old debug ELF', 'artifacts/bootm.o': b'old object',
                               'artifacts/bootm.disassembly': b'same disassembly', 'logs/make.log': b'same log'})
        for relative, data in self.old_files.items():
            self.write(self.old / relative, data)
        old_manifest = {'schema': 1, 'hardware_acceptance': False,
                        'files': {name: M.pin(data) for name, data in self.old_files.items()}}
        old_manifest_raw = self.json_bytes(old_manifest)
        self.write(self.old / 'candidate-manifest.json', old_manifest_raw)
        self.contract = {
            'target_id': M.REGISTERED_TARGET, 'registry_pin': M.pin(b'registry'),
            'original_manifest_pin': M.pin(old_manifest_raw), 'original_manifest': old_manifest,
            'required_artifacts': {name: M.pin(self.old_files[name]) for name in M.MATCHED_ARTIFACTS},
            'source_provenance': self.original_provenance,
            'target': {'original_project_inputs': {M.COMPONENT: M.pin(b'original producer')}},
            'inputs': str(self.old)}
        self.binding = {key: self.contract[key] for key in ('target_id', 'registry_pin', 'original_manifest_pin')}
        self.binding.update(exact_matches_verified=True, matched_artifacts=self.contract['required_artifacts'])
        self.result = {'component_build_success': True, 'hardware_acceptance': False,
                       'deployment_authorized': False, 'registered_replay': self.binding}
        self.new_files = dict(self.old_files)
        self.new_files.update({'artifacts/uboot.elf': b'new debug ELF', 'artifacts/bootm.o': b'new object',
                               'artifacts/uboot-challenge-replay.patch': b'new generator patch',
                               'artifacts/registered-challenge.c': self.old_files['artifacts/uboot-sunxi-challenge.c'],
                               'artifacts/registered-target.json': self.json_bytes({
                                   key: self.contract[key] for key in ('target_id', 'registry_pin', 'original_manifest_pin')}),
                               'artifacts/candidate-result.json': self.json_bytes(self.result)})
        for name in (M.COMPONENT, M.REGISTRY, M.EVIDENCE):
            self.new_files['artifacts/project-inputs/' + name] = ('current ' + name).encode()
        self.provenance = dict(self.original_provenance, registered_replay=self.binding,
            challenge_mode='registered', project={'commit': self.source['revision'], 'git_status_porcelain': '',
            'inputs': {name: M.pin(('current ' + name).encode()) for name in (M.COMPONENT, M.REGISTRY, M.EVIDENCE)}},
            configuration=self.contract['required_artifacts']['artifacts/uboot.config'],
            challenge=self.contract['required_artifacts']['artifacts/uboot-sunxi-challenge.c'],
            generator_patch=M.pin(self.new_files['artifacts/uboot-challenge-replay.patch']))
        self.new_files['artifacts/source-provenance.json'] = self.json_bytes(self.provenance)
        self.save_new()
        self.write(self.work / 'host-script/spi-nvme-host-boot.scr', b'script')
        self.write(self.work / 'host-script/receipt.json', b'{"success":true}')
        self.report = {'success': True, 'returncode': 0, 'component_result': self.result,
                       'mounted_work_path': str(self.work)}
        self.shared_api = self.shared(self.phase)

    @staticmethod
    def json_bytes(data):
        return (json.dumps(data, sort_keys=True) + '\n').encode()

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def save_new(self):
        for relative, data in self.new_files.items():
            self.write(self.component / relative, data)
        self.new_manifest = {'schema': 1, 'hardware_acceptance': False,
                             'files': {name: M.pin(data) for name, data in self.new_files.items()}}
        self.write(self.component / 'candidate-manifest.json', self.json_bytes(self.new_manifest))

    def retain(self):
        M.retain_registered_ram_work(self.shared_api, self.phase, self.report, ['id'], self.contract, self.source)

    def test_readonly_five_input_subset_and_inner_flags(self):
        inputs = self.phase / 'inputs'
        with (mock.patch.object(M.os, 'chown') if os.geteuid() else nullcontext()):
            M.stage_replay_inputs(inputs, self.contract)
        staged = inputs / 'replay-target'
        self.assertEqual({p.relative_to(staged).as_posix() for p in staged.rglob('*') if p.is_file()},
                         set(M.REPLAY_INPUTS))
        for path in (staged, *staged.rglob('*')):
            self.assertEqual(path.stat().st_mode & 0o777, 0o555 if path.is_dir() else 0o444)
            if os.geteuid() == 0:
                self.assertEqual((path.stat().st_uid, path.stat().st_gid), (0, 0))
        runner = M.inner_program('a' * 40, M.REGISTERED_TARGET).decode()
        compile(runner, '<registered-inner>', 'exec')
        self.assertIn("'--replay-target', '" + M.REGISTERED_TARGET + "'", runner)
        self.assertIn("'--replay-inputs', '/a733-debs/replay-target'", runner)
        self.assertNotIn('--replay-target', M.inner_program('a' * 40).decode())
        with self.assertRaises(ValueError):
            M.inner_program('a' * 40, 'unregistered')

    def test_preflight_rehashes_full_manifest_and_archive_with_authenticated_loader(self):
        contents = {M.COMPONENT: b'authenticated component', M.REGISTRY: self.json_bytes({
            'schema': 1, 'targets': {M.REGISTERED_TARGET: {'manifest': self.contract['original_manifest_pin']}}})}
        self.contract['registry_pin'] = M.pin(contents[M.REGISTRY])
        loader = mock.Mock(return_value=self.contract)
        with mock.patch.object(M, 'module', return_value=types.SimpleNamespace(load_registered_target=loader)) as module:
            self.assertIs(M.load_replay_contract(self.root, contents, M.REGISTERED_TARGET), self.contract)
            module.assert_called_once_with(str(self.root / M.COMPONENT), contents[M.COMPONENT])
            loader.assert_called_once_with(self.root, M.REGISTERED_TARGET, self.old)
            # This file is outside the five executable match pins and staged subset.
            (self.old / 'artifacts/uboot.elf').write_bytes(b'bad debug ELF')
            with self.assertRaisesRegex(ValueError, 'file (size|hash) differs'):
                M.load_replay_contract(self.root, contents, M.REGISTERED_TARGET)

    def test_wrong_result_provenance_or_actual_bytes_refuses_before_retention(self):
        for mutation in ('target', 'matches', 'verified', 'provenance', 'actual', 'manifest', 'unlisted'):
            with self.subTest(mutation=mutation):
                result = json.loads(json.dumps(self.result))
                if mutation == 'target': result['registered_replay']['target_id'] = 'wrong'
                if mutation == 'matches': result['registered_replay']['matched_artifacts'].pop(M.MATCHED_ARTIFACTS[0])
                if mutation == 'verified': result['registered_replay']['exact_matches_verified'] = False
                if mutation == 'provenance':
                    bad = dict(self.provenance, project={'commit': 'e' * 40})
                    self.new_files['artifacts/source-provenance.json'] = self.json_bytes(bad)
                    self.save_new()
                if mutation == 'actual':
                    self.write(self.component / M.MATCHED_ARTIFACTS[0], b'bad actual bytes')
                if mutation == 'manifest':
                    self.write(self.component / 'candidate-manifest.json', b'{}')
                if mutation == 'unlisted': self.write(self.component / 'logs/unlisted', b'keep this log')
                self.report['component_result'] = result
                with mock.patch.object(M, 'work_mount_identity', return_value=['id']), \
                     mock.patch.object(M.subprocess, 'run') as run:
                    with self.assertRaises((ValueError, KeyError)):
                        self.retain()
                    run.assert_not_called()
                self.assertFalse((self.phase / 'component-retained').exists())
                self.assertEqual(self.report['mounted_work_path'], str(self.work))
                (self.component / 'logs/unlisted').unlink(missing_ok=True)
                self.new_files['artifacts/source-provenance.json'] = self.json_bytes(self.provenance)
                self.save_new()

    def test_mixed_reference_unique_logs_producers_and_durable_recovery_before_unmount(self):
        self.shared_api.atomic_json = M.module(M.SHARED, (REPO / M.SHARED).read_bytes()).atomic_json
        retained = self.phase / 'component-retained'
        events = []
        real_tree, real_directory = M.fsync_review_tree, M.fsync_directory
        def tree(path):
            real_tree(path); events.append('tree-fsynced')
        def directory(path):
            real_directory(path); events.append('directory:' + str(path))
        def unmount(args, **kw):
            self.assertEqual(args, ['umount', '--', str(self.work)])
            self.assertIn('tree-fsynced', events)
            self.assertEqual(events[-1], 'directory:' + str(self.phase.parent))
            recovery = json.loads((self.phase / 'retained-awaiting-unmount.json').read_text())
            self.assertFalse(recovery['success'])
            self.assertEqual(recovery['state'], 'retained-awaiting-unmount')
            self.assertEqual(recovery['ram_retention']['mode'], 'registered-reference')
            with self.assertRaises(FileExistsError):
                self.shared_api.atomic_json(self.phase / 'retained-awaiting-unmount.json', {})
            return types.SimpleNamespace(returncode=0)
        with mock.patch.object(M, 'work_mount_identity', return_value=['id']), \
             mock.patch.object(M.subprocess, 'run', side_effect=unmount) as run, \
             mock.patch.object(M, 'fsync_review_tree', side_effect=tree), \
             mock.patch.object(M, 'fsync_directory', side_effect=directory), \
             (mock.patch.object(M.os, 'fchown') if os.geteuid() else nullcontext()):
            self.retain()
            run.assert_called_once()
            self.shared_api.atomic_json(self.phase / 'receipt.json', dict(self.report, success=True, state='complete'))
            with self.assertRaises(FileExistsError):
                self.shared_api.atomic_json(self.phase / 'receipt.json', {})
        mapping = json.loads((retained / 'reference-map.json').read_text())
        self.assertEqual(set(mapping['entries']), set(self.new_manifest['files']))
        for relative, entry in mapping['entries'].items():
            self.assertEqual(len(entry), 1)
            if 'copied_local' in entry:
                self.assertEqual((retained / relative).read_bytes(), self.new_files[relative])
            else:
                self.assertEqual(set(entry), {'existing_reference'})
                self.assertEqual(entry['existing_reference']['root'], str(self.old))
                self.assertEqual(entry['existing_reference']['manifest'], self.contract['original_manifest_pin'])
                self.assertEqual(entry['existing_reference']['pin'], self.new_manifest['files'][relative])
                self.assertFalse((retained / relative).exists())
        for relative in ('logs/make.log', 'artifacts/uboot.elf', 'artifacts/bootm.o',
                         'artifacts/source-provenance.json', 'artifacts/registered-challenge.c',
                         'artifacts/uboot-challenge-replay.patch', 'artifacts/project-inputs/' + M.COMPONENT):
            self.assertIn('copied_local', mapping['entries'][relative])
        self.assertIn('existing_reference', mapping['entries']['artifacts/bootm.disassembly'])
        self.assertEqual((retained / 'host-script/spi-nvme-host-boot.scr').read_bytes(), b'script')
        self.assertEqual((retained / 'host-script/receipt.json').read_bytes(), b'{"success":true}')
        self.assertEqual(M.pin((retained / 'candidate-manifest.json').read_bytes()), mapping['new_manifest_pin'])
        self.assertFalse((self.phase / 'work.tar.gz').exists())
        self.assertIsNone(self.report['mounted_work_path'])

    def test_reference_hash_manifest_archive_budget_and_fsync_failures_keep_ram(self):
        for operation in ('reference', 'manifest', 'archive', 'budget', 'tree-fsync', 'record-fsync', 'mount'):
            with self.subTest(operation=operation):
                original_hash = M.authenticated_file
                def authenticate(path, *args, **kw):
                    if operation == 'reference' and Path(path) == self.old / 'artifacts/bootm.disassembly':
                        raise ValueError('injected old reference hash mismatch')
                    if operation == 'manifest' and Path(path) == self.old / 'candidate-manifest.json':
                        raise ValueError('injected old manifest hash mismatch')
                    if operation == 'archive' and Path(path) == self.archive:
                        raise ValueError('injected old archive hash mismatch')
                    return original_hash(path, *args, **kw)
                self.shared_api.resource_sample.return_value = dict(self.sample,
                    free_bytes=4_000_000_001 if operation == 'budget' else 5_000_000_000)
                def sync_tree(path):
                    if operation == 'tree-fsync': raise OSError('injected tree fsync')
                def sync_directory(path):
                    if operation == 'record-fsync' and (self.phase / 'retained-awaiting-unmount.json').exists():
                        raise OSError('injected recovery fsync')
                identities = [['id'], ['changed']] if operation == 'mount' else [['id'], ['id']]
                with mock.patch.object(M, 'authenticated_file', side_effect=authenticate), \
                     mock.patch.object(M, 'work_mount_identity', side_effect=identities), \
                     mock.patch.object(M, 'fsync_review_tree', side_effect=sync_tree), \
                     mock.patch.object(M, 'fsync_directory', side_effect=sync_directory), \
                     mock.patch.object(M.subprocess, 'run') as run:
                    with self.assertRaises((ValueError, OSError)):
                        self.retain()
                    run.assert_not_called()
                self.assertEqual(self.report['mounted_work_path'], str(self.work))
                if operation == 'budget':
                    self.assertFalse((self.phase / 'component-retained').exists())
                    budget = self.report['registered_retention_budget']
                    self.assertGreater(budget['allocated_upper_bound_bytes'], M.RETENTION_MARGIN)
                    self.assertEqual(budget['required_free_bytes_exclusive'],
                                     4_000_000_000 + budget['allocated_upper_bound_bytes'])
                import shutil
                shutil.rmtree(self.phase / 'component-retained', ignore_errors=True)
                (self.phase / 'retained-awaiting-unmount.json').unlink(missing_ok=True)

    def test_held_file_refuses_links_and_metadata_mutation(self):
        data = self.old / 'artifacts/uboot.elf'
        expected = M.pin(data.read_bytes())
        linked = self.root / 'hardlink'; os.link(data, linked)
        with self.assertRaisesRegex(ValueError, 'single-link'):
            M.authenticated_file(data, expected)
        linked.unlink()
        linked.symlink_to(data)
        with self.assertRaises(OSError): M.authenticated_file(linked, expected)
        indirect = self.root / 'indirect'; indirect.symlink_to(self.old, target_is_directory=True)
        with self.assertRaises(OSError): M.authenticated_file(indirect / 'artifacts/uboot.elf', expected)
        original = M.hashlib.sha256
        def checksum():
            value = original()
            def update(chunk):
                value.update(chunk)
                os.utime(data, ns=(data.stat().st_atime_ns, data.stat().st_mtime_ns + 1))
            return types.SimpleNamespace(update=update, hexdigest=value.hexdigest)
        with mock.patch.object(M.hashlib, 'sha256', side_effect=checksum):
            with self.assertRaisesRegex(ValueError, 'changed during read'):
                M.authenticated_file(data, expected)

    def test_registered_payload_failure_journals_primary_and_never_archives_or_unmounts(self):
        import shutil
        source = b'echo host script\n'
        self.write(self.work / 'host-script/spi-nvme-host-boot.scr', image(source))
        self.write(self.work / 'host-script/receipt.json', self.json_bytes({
            'success': True, 'source': M.pin(source), 'script': M.pin(image(source))}))
        for failure in ('exit', 'verification'):
            with self.subTest(failure=failure):
                phase = self.root / ('failed-' + failure)
                shared = self.shared(phase)
                report = {}
                def mount(shared, phase):
                    shutil.copytree(self.work, phase / 'work', dirs_exist_ok=True)
                    return ['id']
                def run(args, **kw):
                    self.assertEqual(args[0], 'git')
                    Path(args[-2]).write_bytes(b'producer bundle')
                proc = mock.Mock()
                proc.poll.return_value = proc.wait.return_value = 7 if failure == 'exit' else 0
                builder = types.SimpleNamespace(validate_probe=lambda raw: {'valid': True})
                with mock.patch.object(M.os, 'chown'), \
                     mock.patch.object(M, 'mount_ram_work', side_effect=mount), \
                     mock.patch.object(M, 'command', return_value=['fixture-payload']), \
                     mock.patch.object(M.subprocess, 'run', side_effect=run) as run_mock, \
                     mock.patch.object(M.subprocess, 'Popen', return_value=proc), \
                     mock.patch.object(M.subprocess, 'check_output', return_value=b'inventory'), \
                     mock.patch.object(M, 'retain_ram_work') as full_archive, \
                     mock.patch.object(M, 'verify_registered_result', side_effect=ValueError('bad registered proof')):
                    with self.assertRaisesRegex(ValueError, 'payload failed' if failure == 'exit' else 'bad registered proof'):
                        M.execute(shared, builder, self.root, phase, self.source, {M.SCRIPT: source},
                                  b'inventory', report, True, self.contract)
                    full_archive.assert_not_called()
                    self.assertTrue(all(call.args[0][0] == 'git' for call in run_mock.call_args_list))
                saved = json.loads((phase / 'receipt.json').read_text())
                self.assertFalse(saved['success'])
                self.assertEqual(saved['mounted_work_path'], str(phase / 'work'))
                self.assertIn('requires successful payload', saved['retention_error'])
                self.assertIn('payload failed' if failure == 'exit' else 'bad registered proof', saved['error'])
                self.assertFalse((phase / 'work.tar.gz').exists())
                self.assertTrue((phase / 'work/project/out/uboot-nvme-candidate-01/artifacts/uboot.elf').is_file())


if __name__ == '__main__':
    unittest.main()

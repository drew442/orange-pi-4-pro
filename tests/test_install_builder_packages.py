"""Offline-install fixtures only: no nspawn, APT, devices or host mutations."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('install_builder', REPO / 'build/install-builder-packages.py')
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)
probe_spec = importlib.util.spec_from_file_location('seed_test_fixture', REPO / 'tests/test_clean_builder.py')
probe_fixture = importlib.util.module_from_spec(probe_spec)
probe_spec.loader.exec_module(probe_fixture)


def facts(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def expected_inventory():
    return {('pkg' + str(i), '1.0', 'amd64') for i in range(842)}


def installed_log(expected=None):
    expected = expected or expected_inventory()
    return (probe_fixture.probe_output() + b'A733_BOUNDARY_PASSED\nA733_BEGIN installed\n' +
            ''.join('\t'.join(row) + '\tinstall ok installed\n' for row in sorted(expected)).encode() +
            b'A733_END installed\n')


class InputTests(unittest.TestCase):
    def test_real_lock_exact_coverage(self):
        records, expected, inventory = install.inputs(REPO)
        self.assertEqual(len(records), 842)
        self.assertEqual(len(expected), 842)
        self.assertEqual(sum(x['bytes'] for x in records.values()), 983768778)
        self.assertEqual(inventory, (REPO / install.INVENTORY).read_bytes())

    def test_bad_lock_tuple_path_hash_and_inventory_refused(self):
        original = json.loads((REPO / install.LOCK).read_bytes())
        inventory = (REPO / install.INVENTORY).read_bytes()
        changes = [lambda lock: lock['packages'][0].update(filename='../escape.deb'),
                   lambda lock: lock['packages'][0].update(sha256='bad'),
                   lambda lock: lock['packages'][0].update(version='different'),
                   lambda lock: lock['packages'][0].update(bytes=True),
                   lambda lock: lock['inventory_lock'].update(sha256='0' * 64),
                   lambda lock: lock['packages'].append(lock['packages'][0])]
        for change in changes:
            lock = json.loads(json.dumps(original))
            change(lock)
            with self.subTest(change=change), patch.object(install, 'read', side_effect=lambda path:
                    inventory if str(path).endswith('.tsv') else json.dumps(lock).encode()):
                with self.assertRaises(ValueError):
                    install.inputs(REPO)

    def test_cache_verified_copy_independent_and_readable_under_umask077(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'cache/pool/main/a/a/a.deb'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'archive')
            records = {'pool/main/a/a/a.deb': facts(b'archive')}
            self.assertEqual(install.cache_facts(root / 'cache', records), records)
            previous = os.umask(0o077)
            try:
                install.copy_cache(root / 'cache', root / 'private', records)
                install.write_private(root / 'list', b'/a733-debs/a.deb\n', 0o444)
                install.write_private(root / 'script', install.install_script(), 0o444)
            finally:
                os.umask(previous)
            self.assertEqual((root / 'private').stat().st_mode & 0o777, 0o555)
            for path in (root / 'private/a.deb', root / 'list', root / 'script'):
                self.assertEqual(path.stat().st_mode & 0o777, 0o444)
            source.write_bytes(b'mutated')
            self.assertEqual((root / 'private/a.deb').read_bytes(), b'archive')
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                install.cache_facts(root / 'cache', records)
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                install.copy_cache(root / 'cache', root / 'bad-copy', records)

    def test_symlink_cache_file_and_parent_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'real').mkdir()
            (root / 'real/a.deb').write_bytes(b'a')
            (root / 'link').symlink_to(root / 'real', target_is_directory=True)
            (root / 'real/link.deb').symlink_to(root / 'real/a.deb')
            for cache, name in ((root / 'link', 'a.deb'), (root / 'real', 'link.deb')):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Symlink'):
                    install.cache_facts(cache, {name: facts(b'a')})


class BoundaryTests(unittest.TestCase):
    def test_root_bootstrap_refuses_untrusted_sibling_before_import(self):
        good_file = types.SimpleNamespace(st_uid=0, st_mode=0o100644)
        good_directory = types.SimpleNamespace(st_uid=0, st_mode=0o40755)
        def info(path):
            if path.name == 'clean-builder.py':
                return types.SimpleNamespace(st_uid=1000, st_mode=0o100644)
            return good_file if path.name.endswith('.py') else good_directory
        with patch.object(install.os, 'geteuid', return_value=0), \
                patch.object(Path, 'lstat', info), \
                patch.object(install.importlib.util, 'spec_from_file_location') as loader:
            with self.assertRaisesRegex(ValueError, 'bootstrap'):
                install.load_seed()
            loader.assert_not_called()

    def test_staging_headroom_refused(self):
        sample = {'free_bytes': 3 * 1024**3 + 100, 'available_memory_bytes': 1024**3}
        with patch.object(install.seed, 'resources', return_value=sample):
            with self.assertRaisesRegex(ValueError, 'staging headroom'):
                install.capacity(Path('/private'), extra_bytes=983768778)
            install.capacity(Path('/private'), extra_bytes=0)

    def test_exact_original_flags_plus_only_fixed_readonly_binds(self):
        output = Path('/private/seed')
        phase = output / 'package-install'
        command = install.command(output, phase)
        original = install.seed.nspawn_command(output / 'rootfs', output / 'probe.sh')[:-2]
        self.assertEqual(command[:len(original)], original)
        self.assertEqual(command[-2:], ['/bin/sh', '/a733-install.sh'])
        self.assertEqual([arg for arg in command[len(original):] if arg.startswith('--bind')],
                         ['--bind-ro=/private/seed/package-install/debs:/a733-debs',
                          '--bind-ro=/private/seed/package-install/packages.list:/a733-packages.list',
                          '--bind-ro=/private/seed/package-install/install.sh:/a733-install.sh',
                          '--bind-ro=/private/seed/package-install/sources:/a733-sources'])
        self.assertFalse(any(arg.startswith('--capability=') for arg in command))

    def test_script_probe_before_maintainers_and_single_apt_install(self):
        script = install.install_script()
        self.assertTrue(script.startswith(install.seed.PROBE))
        self.assertLess(script.index(b'A733_BOUNDARY_PASSED'), script.index(b'debconf-set-selections'))
        self.assertLess(script.index(b'debconf-set-selections'), script.index(b' --yes install'))
        self.assertEqual(script.count(b' --yes install "$@"'), 1)
        self.assertIn(b'[ "$#" -eq 842 ]', script)
        install_line = next(line for line in script.splitlines() if b' --yes install' in line)
        self.assertNotIn(b'--no-download', install_line)
        self.assertIn(b'--no-install-recommends --no-remove --allow-downgrades --reinstall', install_line)
        self.assertIn(b'Dir::Etc::sourcelist=/a733-sources/sources.list', install_line)
        self.assertIn(b'Dir::Etc::sourceparts=/a733-sources/parts', install_line)
        self.assertIn(b'--no-download check', script)
        self.assertNotIn(b'Dir::Etc::sourcelist=/dev/null', script)
        self.assertNotIn(b'Dir::Etc::sourceparts=-', script)
        self.assertNotIn(b'apt-get update', script)
        self.assertNotIn(b'--force', script)
        self.assertIn(b'grub2/update_nvram boolean false', script)
        self.assertIn(b'grub-pc/install_devices_empty boolean true', script)
        self.assertIn(b'if [ -e "$p" ] || [ -L "$p" ]; then exit 1; fi', script)
        subprocess.run(['/bin/sh', '-n'], input=script, check=True)

    def test_inventory_exact_status_and_unique_markers(self):
        log = installed_log()
        result = install.validate_installed(log, expected_inventory())
        self.assertEqual(len(result.splitlines()), 842)
        bad = [log.replace(b'install ok installed', b'install ok unpacked', 1),
               log.replace(b'pkg0\t1.0', b'pkg0\t2.0', 1),
               log.replace(b'A733_END installed', b'pkgEXTRA\t1.0\tamd64\tinstall ok installed\nA733_END installed'),
               log.replace(b'pkg0\t1.0\tamd64\tinstall ok installed\n', b'', 1),
               log + b'A733_BEGIN installed\nA733_END installed\n',
               log + b'A733_BOUNDARY_PASSED\n',
               log.replace(b'NoNewPrivs:\t1', b'NoNewPrivs:\t0'),
               log.replace(b'pkg0\t1.0\tamd64\tinstall ok installed', b'bad', 1)]
        for data in bad:
            with self.subTest(data=data[-90:]), self.assertRaises(ValueError):
                install.validate_installed(data, expected_inventory())

    def test_empty_sources_readable_under_umask077_and_tampering_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            phase = Path(tmp)
            previous = os.umask(0o077)
            try:
                record = install.stage_sources(phase)
            finally:
                os.umask(previous)
            directory = phase / 'sources'
            self.assertEqual(record['sources.list'], facts(b''))
            self.assertEqual(directory.stat().st_mode & 0o777, 0o555)
            self.assertEqual((directory / 'parts').stat().st_mode & 0o777, 0o555)
            source = directory / 'sources.list'
            self.assertEqual(source.stat().st_mode & 0o777, 0o444)
            self.assertEqual(source.read_bytes(), b'')
            source.chmod(0o644)
            source.write_bytes(b'deb https://example.invalid jammy main\n')
            source.chmod(0o444)
            with self.assertRaisesRegex(ValueError, 'remain empty'):
                install.source_facts(directory)

    def test_policy_refuses_existing_and_assigns_mapped_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'usr/sbin').mkdir(parents=True)
            with patch.object(install.os, 'chown') as chown:
                install.policy(root)
            target = root / 'usr/sbin/policy-rc.d'
            self.assertEqual(target.read_bytes(), b'#!/bin/sh\nexit 101\n')
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)
            chown.assert_called_once_with(target, install.seed.UID_BASE, install.seed.UID_BASE,
                                          follow_symlinks=False)
            with self.assertRaisesRegex(ValueError, 'Existing'):
                install.policy(root)


class SeedTests(unittest.TestCase):
    def fixture(self, root):
        output = root / 'seed-probe-fixture'
        output.mkdir(mode=0o700)
        (output / 'rootfs').mkdir()
        (output / 'firmware-empty').mkdir(mode=0o555)
        (output / 'firmware-empty').chmod(0o555)
        (output / 'probe.sh').write_bytes(install.seed.PROBE)
        (output / 'probe.log').write_bytes(probe_fixture.probe_output())
        (output / 'source-lock.json').write_bytes(b'lock')
        (output / 'host-inventory-before.tsv').write_bytes(b'inventory')
        files = {name: facts(name.encode()) for name in install.FILES}
        files[install.seed.LOCK] = facts(b'lock')
        files[install.INVENTORY] = facts(b'inventory')
        receipt = {'schema': 1, 'phase': 'seed-and-isolation-probe', 'success': True,
                   'post_checks': {'success': True, 'errors': []}, 'seed': install.seed.SEED,
                   'project': {'head': 'old-head', 'files': {k: files[k] for k in install.seed.FILES}},
                   'probe_payload': facts(install.seed.PROBE),
                   'probe_log': facts(probe_fixture.probe_output()),
                   'command': install.seed.nspawn_command(output / 'rootfs', output / 'probe.sh')}
        (output / 'receipt.json').write_text(json.dumps(receipt))
        return output, json.loads(json.dumps({'head': 'advanced-head', 'files': files})), receipt

    def test_seed_receipt_binding_and_exact_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o700)
            output, project, receipt = self.fixture(root)
            with patch.object(install.seed, 'WORKSPACE', root), \
                    patch.object(install.seed, 'root_owned_path'), \
                    patch.object(install, 'private_directory'), \
                    patch.object(install.seed, 'shifted_root_owner'), \
                    patch.object(install.seed, 'check_no_mounts'), \
                    patch.object(install.seed, 'check_range_unused'):
                self.assertIn('receipt.json', install.validate_seed(output, project))
                receipt['success'] = False
                (output / 'receipt.json').write_text(json.dumps(receipt))
                with self.assertRaisesRegex(ValueError, 'did not pass'):
                    install.validate_seed(output, project)
                receipt['success'] = True
                receipt['project']['files']['build/clean-builder.py']['sha256'] = '0' * 64
                (output / 'receipt.json').write_text(json.dumps(receipt))
                with self.assertRaisesRegex(ValueError, 'source binding'):
                    install.validate_seed(output, project)
                with self.assertRaisesRegex(ValueError, 'directly under'):
                    install.validate_seed(output / 'nested', project)

    def test_untrusted_repository_refused_before_git(self):
        with patch.object(install.seed, 'repository_guard', side_effect=ValueError('private project')), \
                patch.object(install.seed, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'private project'):
                install.binding(REPO)
            run.assert_not_called()


class MonitorTests(unittest.TestCase):
    def run_monitor(self, sample, result=0, wait_error=None, after_launch=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        phase = Path(temporary.name)
        process = Mock(pid=12345, returncode=result)
        if wait_error:
            process.wait.side_effect = [wait_error, result, result]
        else:
            process.wait.return_value = result
        report = {'resource_samples': []}
        with (phase / 'log').open('wb') as log, \
                patch.object(install.subprocess, 'Popen', return_value=process) as popen, \
                patch.object(install.seed, 'resources', return_value=sample,
                             side_effect=[sample, after_launch] if after_launch else None), \
                patch.object(install.os, 'killpg') as kill:
            error = None
            try:
                install.monitored(['fixed-command'], log, phase, report)
            except BaseException as exc:
                error = exc
        if sample['free_bytes'] < 3 * 1024**3 or sample['available_memory_bytes'] < 512 * 1024**2:
            popen.assert_not_called()
        if after_launch is not None:
            popen.assert_called_once()
            self.assertGreaterEqual(process.wait.call_count, 2)
            self.assertEqual([call.args for call in kill.call_args_list],
                             [(12345, signal.SIGTERM), (12345, signal.SIGKILL)])
        if popen.called:
            self.assertTrue(popen.call_args.kwargs['start_new_session'])
            self.assertEqual(kill.call_args_list[-1].args, (12345, signal.SIGKILL))
            self.assertTrue((phase / 'resources.jsonl').read_bytes())
        else:
            kill.assert_not_called()
        return error, report

    def test_maintainer_failure_and_thresholds_stop_reap(self):
        ample = {'free_bytes': 10 * 1024**3, 'available_memory_bytes': 1024**3}
        error, report = self.run_monitor(ample, result=42)
        self.assertIn('exit 42', str(error))
        self.assertEqual(report['install_exit'], 42)
        for sample, message in (({**ample, 'free_bytes': 1}, 'Disk cancellation'),
                                ({**ample, 'available_memory_bytes': 1}, 'RAM cancellation')):
            error, _ = self.run_monitor(sample)
            self.assertIn(message, str(error))
            error, report = self.run_monitor(ample, after_launch=sample)
            self.assertIn(message, str(error))
            self.assertEqual(report['resource_samples'][0]['free_bytes'], sample['free_bytes'])
            self.assertEqual(report['resource_samples'][0]['available_memory_bytes'],
                             sample['available_memory_bytes'])
        error, _ = self.run_monitor(ample, wait_error=KeyboardInterrupt())
        self.assertIsInstance(error, KeyboardInterrupt)


class ReceiptTests(unittest.TestCase):
    def exercise(self, root, failure=None, post_failure=False, success_before_failure=False, mutate_sources=False):
        output = root / 'seed-probe-fixture'
        output.mkdir(mode=0o700)
        cache = root / 'cache'
        cache.mkdir()
        records = {'pool/main/a/a/a.deb': facts(b'archive')}
        def copy(source, target, records):
            target.mkdir()
            (target / 'a.deb').write_bytes(b'archive')
        def monitor(args, log, phase, report):
            log.write(installed_log())
            if mutate_sources:
                source = phase / 'sources/sources.list'
                source.chmod(0o644)
                source.write_bytes(b'changed')
                source.chmod(0o444)
            if failure:
                if success_before_failure:
                    report['success'] = True
                raise failure
        original_fstat = os.fstat
        def root_fstat(fd):
            info = original_fstat(fd)
            values = {k: getattr(info, k) for k in dir(info) if k.startswith('st_')}
            values['st_uid'] = 0
            return types.SimpleNamespace(**values)
        project = {'files': {}}
        helper = Mock()
        helper.check_builder.return_value = {'profile': 'fixed'}
        with patch.object(install, 'binding', return_value=project), \
                patch.object(install, 'validate_seed', return_value={}), \
                patch.object(install, 'inputs', return_value=(records, expected_inventory(), b'inventory')), \
                patch.object(install, 'host_state', return_value={'host': 'same'}), \
                patch.object(install, 'capture_inventory', return_value=b'inventory'), \
                patch.object(install, 'cache_facts', return_value=records), \
                patch.object(install, 'copy_cache', side_effect=copy), \
                patch.object(install, 'capacity', return_value={'free_bytes': 10 * 1024**3}), \
                patch.object(install, 'policy', return_value=facts(install.POLICY)), \
                patch.object(install, 'monitored', side_effect=monitor), \
                patch.object(install.seed, 'load_helper', return_value=helper), \
                patch.object(install.seed, 'check_range_unused'), \
                patch.object(install.seed, 'check_no_mounts'), \
                patch.object(install.seed, 'check_process_ranges',
                             side_effect=ValueError('living mapped process') if post_failure else None), \
                patch.object(install.seed, 'run', return_value=b'help'), \
                patch.object(install.os, 'geteuid', return_value=0), \
                patch.object(install.os, 'fstat', side_effect=root_fstat):
            error = None
            try:
                install.install(output, cache)
            except BaseException as exc:
                error = exc
        return error, json.loads((output / 'package-install/receipt.json').read_bytes())

    def test_maintainer_failure_preserves_receipt_log_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            error, receipt = self.exercise(root, failure=ValueError('maintainer failed'))
            self.assertIn('maintainer failed', str(error))
            self.assertFalse(receipt['success'])
            self.assertIn('install_log', receipt)
            phase = root / 'seed-probe-fixture/package-install'
            self.assertEqual((phase / 'debs/a.deb').read_bytes(), b'archive')
            self.assertEqual((phase / 'sources/sources.list').read_bytes(), b'')
            self.assertEqual(receipt['sources']['sources.list'], facts(b''))

    def test_cancellation_retains_failure_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            error, receipt = self.exercise(Path(tmp), failure=KeyboardInterrupt())
            self.assertIsInstance(error, KeyboardInterrupt)
            self.assertFalse(receipt['success'])
            self.assertIn('KeyboardInterrupt', receipt['error'])
            self.assertIn('install_log', receipt)

    def test_late_cancellation_revokes_previously_set_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            error, receipt = self.exercise(Path(tmp), failure=KeyboardInterrupt(),
                                           success_before_failure=True)
            self.assertIsInstance(error, KeyboardInterrupt)
            self.assertFalse(receipt['success'])
            self.assertTrue(receipt['post_checks']['success'])
            self.assertIn('KeyboardInterrupt', receipt['error'])

    def test_postcheck_failure_revokes_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            error, receipt = self.exercise(Path(tmp), post_failure=True)
            self.assertIn('Postchecks', str(error))
            self.assertFalse(receipt['success'])
            self.assertFalse(receipt['post_checks']['success'])

    def test_source_mutation_revokes_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            error, receipt = self.exercise(Path(tmp), mutate_sources=True)
            self.assertIn('Postchecks', str(error))
            self.assertFalse(receipt['success'])
            self.assertTrue(any('sources:' in error for error in receipt['post_checks']['errors']))

    def test_success_limited_to_mutable_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            error, receipt = self.exercise(Path(tmp))
            self.assertIsNone(error)
            self.assertTrue(receipt['success'])
            self.assertFalse(receipt['immutable_export'])
            self.assertFalse(receipt['complete_clean_builder'])
            self.assertFalse(receipt['hardware_acceptance'])


class RefusalTests(unittest.TestCase):
    def test_receipt_lock_and_existing_phase_prevent_execution(self):
        for failure_stage in ('seed', 'locks', 'existing'):
            with self.subTest(stage=failure_stage), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                output, cache = root / 'seed-probe-test', root / 'cache'
                output.mkdir()
                cache.mkdir()
                if failure_stage == 'existing':
                    (output / 'package-install').mkdir()
                with patch.object(install.os, 'geteuid', return_value=0), \
                        patch.object(install, 'binding', return_value={}), \
                        patch.object(install, 'validate_seed', side_effect=
                                     ValueError('seed rejected') if failure_stage == 'seed' else None), \
                        patch.object(install, 'inputs', side_effect=
                                     ValueError('lock rejected') if failure_stage == 'locks' else None,
                                     return_value=({}, set(), b'')), \
                        patch.object(install, 'monitored') as execute:
                    with self.assertRaises(ValueError):
                        install.install(output, cache)
                    execute.assert_not_called()



if __name__ == '__main__':
    unittest.main()

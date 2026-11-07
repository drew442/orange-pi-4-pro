"""Host guard/simulation coverage. No vendor compiler or hardware is exercised."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('nvme_candidate', ROOT / 'build/uboot-nvme-candidate.py')
candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(candidate)
core = candidate.core


def config():
    return candidate.BASE_COMMAND + b'''CONFIG_ENV_IS_NOWHERE=y
# CONFIG_CMD_SAVEENV is not set
CONFIG_BOOTCMD_SKIP_MISC=y
# CONFIG_SUNXI_SWITCH_SYSTEM is not set
# CONFIG_BOOTCOUNT_LIMIT is not set
# CONFIG_ENV_IS_IN_EXT4 is not set
'''


def challenge():
    rows = ['  ' + ', '.join(f'0x{byte:02x}' for byte in range(row * 8, row * 8 + 8)) +
            (',' if row < 15 else '') for row in range(16)]
    return ('__attribute__((__used__))\nunsigned char sunxi_challenge[] = {\n' + '\n'.join(rows) +
            '\n};\n__attribute__((__used__))\nunsigned int sunxi_challenge_len = 128;\n').encode()


class CandidateGuards(unittest.TestCase):
    def test_exact_single_line_change(self):
        original = config()
        modified = candidate.candidate_config(original)
        self.assertEqual(modified.replace(candidate.NEW_COMMAND, candidate.BASE_COMMAND), original)
        candidate.validate_config(modified, original)
        for bad in (modified + b'CONFIG_OTHER=y\n', modified.replace(b'NOWHERE=y', b'NOWHERE=n'), original):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                candidate.validate_config(bad, original)

    def test_retained_config_with_ota_absent_or_explicitly_disabled(self):
        # The actual authenticated retained config omits the unknown OTA symbol.
        absent = config()
        self.assertNotIn(b'CONFIG_SUNXI_OTA_TURNNING', absent)
        for retained in (absent, absent + b'# CONFIG_SUNXI_OTA_TURNNING is not set\n'):
            with self.subTest(retained=retained):
                expected = retained.replace(candidate.BASE_COMMAND, candidate.NEW_COMMAND)
                self.assertEqual(candidate.candidate_config(retained), expected)
                candidate.validate_config(expected, retained)
                # Adding/removing even a disabled OTA comment is still an extra
                # config change when it differs from the authenticated baseline.
                altered = expected + b'# CONFIG_SUNXI_OTA_TURNNING is not set\n'
                with self.assertRaisesRegex(ValueError, 'differs beyond'):
                    candidate.validate_config(altered, retained)

    def test_ota_value_assignments_are_all_refused(self):
        for assignment in (b'CONFIG_SUNXI_OTA_TURNNING=y', b'CONFIG_SUNXI_OTA_TURNNING=m',
                           b'CONFIG_SUNXI_OTA_TURNNING=n', b'CONFIG_SUNXI_OTA_TURNNING=invalid',
                           b'CONFIG_SUNXI_OTA_TURNNING=', b' CONFIG_SUNXI_OTA_TURNNING = y'):
            with self.subTest(assignment=assignment), self.assertRaisesRegex(ValueError, 'OTA value assignment'):
                candidate.candidate_config(config() + assignment + b'\n')

    def test_reject_missing_and_duplicate_safeguards(self):
        for bad in (config() + candidate.BASE_COMMAND, config().replace(candidate.BASE_COMMAND, b''),
                    config().replace(b'# CONFIG_CMD_SAVEENV is not set', b'CONFIG_CMD_SAVEENV=y'),
                    config() + b'CONFIG_ENV_IS_IN_EXT4=y\n'):
            with self.assertRaises(ValueError):
                candidate.candidate_config(bad)

    def test_pinned_core_and_policy(self):
        self.assertEqual(core.facts(ROOT / 'build/uboot-replay.py')['sha256'], candidate.CORE_SHA)
        policy = candidate.validate_policy(ROOT)
        self.assertEqual(policy['spi_boot0'], candidate.SPI_BOOT0)
        self.assertIn('not a closed', policy['search_behavior'])
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / 'configs').mkdir()
            path = repo / candidate.POLICY
            policy['bootcommand'] = 'run bootcmd_mmc0'
            path.write_text(json.dumps(policy))
            with self.assertRaisesRegex(ValueError, 'Unregistered candidate'):
                candidate.validate_policy(repo)
            (repo / 'build').mkdir()
            (repo / 'build/uboot-replay.py').write_text('raise Exception("must not execute")')
            with self.assertRaisesRegex(ValueError, 'Untrusted replay core'):
                candidate.load_core(repo)

    def test_controlled_environment_and_two_jobs(self):
        with patch.dict(candidate.os.environ, {'MAKEFLAGS': '-j100', 'A733_REPLAY_CHALLENGE': '/evil',
                                               'LD_PRELOAD': '/evil', 'GIT_CONFIG_COUNT': '1'}):
            env = candidate.controlled_env()
        for name in ('MAKEFLAGS', 'A733_REPLAY_CHALLENGE', 'LD_PRELOAD', 'GIT_CONFIG_COUNT'):
            self.assertNotIn(name, env)
        self.assertEqual(env['SOURCE_DATE_EPOCH'], '1772630733')
        make = candidate.make_command(Path('/private/tree'), '/private/gcc-')
        self.assertEqual(make.count('-j2'), 1)
        self.assertFalse(any('CHALLENGE' in item for item in make))
        self.assertIn('DIRTY=dirty', make)

    def test_cli_rejects_arbitrary_challenge_profile_and_unpaired_replay(self):
        for options in (['--replay-challenge', '/tmp/old'], ['--builder-profile', 'original-20261104'],
                        ['--replay-target', 'unknown'], ['--replay-target', candidate.REGISTERED_TARGET],
                        ['--replay-inputs', '/tmp/inputs']):
            result = subprocess.run(['python3', candidate.__file__, '--workspace', '/tmp/w',
                                     '--provenance', '/tmp/p', '--output', '/tmp/o'] + options,
                                    capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_output_physical_paths_and_new_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, workspace, provenance = root / 'repo', root / 'w', root / 'p'
            (repo / 'out').mkdir(parents=True)
            workspace.mkdir()
            provenance.mkdir()
            output = repo / 'out/uboot-nvme-candidate-test'
            candidate.validate_output(repo, workspace, provenance, output)
            alias = root / 'alias'
            alias.symlink_to(workspace)
            for bad in (repo / 'out/uboot-replay-test', repo / 'out/uboot-nvme-candidate-',
                        repo / 'out/uboot-nvme-candidate-$(id)', root / 'uboot-nvme-candidate-other'):
                with self.assertRaises(ValueError):
                    candidate.validate_output(repo, workspace, provenance, bad)
            with self.assertRaises(ValueError):
                candidate.validate_output(repo, alias, provenance, output)
            output.mkdir()
            with self.assertRaises(ValueError):
                candidate.validate_output(repo, workspace, provenance, output)

    def test_project_authenticates_own_git_blobs(self):
        base = {'commit': 'a' * 40, 'inputs': {'build/uboot-replay.py': {'sha256': candidate.CORE_SHA}}}
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for relative in candidate.OWN_INPUTS:
                path = repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'committed')
            with patch.object(core, 'project_source', return_value=base), patch.object(core, 'run', return_value=b'committed'):
                record = candidate.project_source(repo)
                self.assertTrue(set(candidate.OWN_INPUTS).issubset(record['inputs']))
                (repo / candidate.POLICY).write_bytes(b'hidden mutation')
                with self.assertRaisesRegex(ValueError, 'differs from commit'):
                    candidate.project_source(repo)

    def test_snapshot_checks_shared_inputs_and_full_builder_inventory(self):
        before = {'builder_profile': {'name': candidate.PROFILE}}
        with patch.object(core, 'validate_inputs', return_value=(before, {'lock': True})) as shared, \
                patch.object(candidate, 'project_source', return_value={'commit': 'x'}), \
                patch.object(candidate, 'validate_policy', return_value={'policy': True}), \
                patch.object(core, 'check_builder', return_value={'name': 'wrong'}):
            with self.assertRaisesRegex(ValueError, 'inventory mismatch'):
                candidate.snapshot(ROOT, Path('/w'), Path('/p'), {'manifest': True})
            self.assertEqual(shared.call_args.args[-1], candidate.PROFILE)

    def test_resource_floor_is_recorded_before_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output / 'logs').mkdir()
            runner = candidate.Runner(output, output)
            with patch.object(candidate.shutil, 'disk_usage', return_value=SimpleNamespace(free=2_999_999_999)):
                with self.assertRaisesRegex(ValueError, 'disk below 3 GB'):
                    runner.sample()
            rows = (output / 'logs/resource-samples.jsonl').read_text().splitlines()
            self.assertEqual(len(rows), 1)
            self.assertLess(min(json.loads(rows[0])['disk_free_bytes'].values()), 3_000_000_000)

    def test_pack_protocol_has_no_spi_programmer_or_merge(self):
        commands = candidate.pack_commands()
        self.assertIn(['update_uboot', '-no_merge', 'u-boot.fex', 'sys_config.bin'], commands)
        self.assertEqual(commands[-1], ['dragonsecboot', '-pack', 'boot_package.cfg'])
        self.assertFalse(any(command[0] in ('dd', 'flashcp', 'merge_full_img', 'update_boot0') for command in commands))

    def test_all_26_pack_inputs_are_private_authenticated_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, artifacts, output = root / 'workspace', root / 'artifacts', root / 'output'
            artifacts.mkdir()
            output.mkdir()
            prefix = 'external/packages/pack-uboot/'
            names = ['sun60iw2/bin/boot0_spinor_a733.fex', 'sun60iw2/bin/monitor.fex',
                     'sun60iw2/bin/scp.fex'] + [f'tools/tool-{index}' for index in range(23)]
            lines = []
            for name in names:
                path = workspace / 'vendor' / prefix / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode())
                lines.append(hashlib.sha256(name.encode()).hexdigest() + '  ' + prefix + name)
            (artifacts / 'vendor-boot-inputs.sha256').write_text('\n'.join(lines) + '\n')
            expected = core.facts(workspace / 'vendor' / prefix / names[0])
            with patch.object(candidate, 'SPI_BOOT0', expected):
                pack, tools = candidate.private_pack(workspace, artifacts, output)
                self.assertEqual(len(list(pack.rglob('*.fex'))) + len(list(tools.iterdir())), 26)
                (pack / 'monitor.fex').write_bytes(b'private alteration')
                self.assertEqual((workspace / 'vendor' / prefix / names[1]).read_bytes(), names[1].encode())
                (workspace / 'vendor' / prefix / names[2]).write_bytes(b'mutated input')
                with self.assertRaises(ValueError):
                    # New output dirs allow the input hash guard to be exercised.
                    other = root / 'other'
                    other.mkdir()
                    other_artifacts = root / 'other-artifacts'
                    other_artifacts.mkdir()
                    (other_artifacts / 'vendor-boot-inputs.sha256').write_text('\n'.join(lines) + '\n')
                    candidate.private_pack(workspace, other_artifacts, other)

    def test_firmware_post_pack_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, artifacts = root / 'pack', root / 'artifacts'
            pack.mkdir()
            artifacts.mkdir()
            for name in ('monitor.fex', 'scp.fex', 'boot0_spinor_a733.fex'):
                (pack / name).write_bytes(name.encode())
            expected = {name: core.facts(pack / name) for name in ('monitor.fex', 'scp.fex', 'boot0_spinor_a733.fex')}
            with patch.object(candidate, 'SPI_BOOT0', expected['boot0_spinor_a733.fex']):
                candidate.verify_firmware(pack, artifacts, expected)
                self.assertEqual((artifacts / 'boot0_spinor_a733.fex').read_bytes(), b'boot0_spinor_a733.fex')
                (pack / 'monitor.fex').write_bytes(b'altered')
                with self.assertRaisesRegex(ValueError, 'Untrusted input'):
                    candidate.verify_firmware(pack, artifacts, expected)

    def test_capacity_reserves_construction_only_on_writable_output_filesystem(self):
        workspace = Path('/read-only-source/workspace')
        output = Path('/ram-output/out/uboot-nvme-candidate-test')
        budget = {'required_free_bytes': 5_666_000_001}
        def usage(source_free, output_free):
            return lambda path: SimpleNamespace(free=source_free if path == workspace else output_free)
        with patch.object(candidate.shutil, 'disk_usage', side_effect=usage(4_100_000_000, 6_000_000_000)):
            candidate.check_capacity(workspace, output, budget)
        for source_free in (4_000_000_000, 3_999_999_999):
            with self.subTest(source_free=source_free), \
                    patch.object(candidate.shutil, 'disk_usage', side_effect=usage(source_free, 6_000_000_000)), \
                    self.assertRaisesRegex(ValueError, 'Read-only source workspace'):
                candidate.check_capacity(workspace, output, budget)
        for output_free in (budget['required_free_bytes'], budget['required_free_bytes'] - 1):
            with self.subTest(output_free=output_free), \
                    patch.object(candidate.shutil, 'disk_usage', side_effect=usage(4_100_000_000, output_free)), \
                    self.assertRaisesRegex(ValueError, 'Candidate construction'):
                candidate.check_capacity(workspace, output, budget)
        # A shared filesystem still needs the complete construction budget.
        with patch.object(candidate.shutil, 'disk_usage', return_value=SimpleNamespace(free=4_100_000_000)), \
                self.assertRaisesRegex(ValueError, 'Candidate construction'):
            candidate.check_capacity(workspace, output, budget)

    def test_capacity_counts_authenticated_archive_and_refuses_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / 'downloads').mkdir()
            archive = workspace / 'downloads/compiler.tar.xz'
            with tarfile.open(archive, 'w:xz') as stream:
                member = tarfile.TarInfo('compiler/bin/gcc')
                member.size = 5
                stream.addfile(member, io.BytesIO(b'hello'))
            lock = {'uboot_toolchain': {'filename': archive.name, 'sha256': core.facts(archive)['sha256']}}
            listing = b'100644 blob ' + b'a' * 40 + b' 10\tsource.c\0'
            real_facts = core.facts
            def facts(path):
                return real_facts(path) if path == archive else {'bytes': 10, 'sha256': 'x'}
            with patch.object(core, 'run', return_value=listing), patch.object(core, 'facts', side_effect=facts):
                budget = candidate.capacity_budget(workspace, lock)
            self.assertEqual(budget['source_members'], 1)
            self.assertEqual(budget['compiler_members'], 1)
            self.assertEqual(budget['required_free_bytes'],
                             4_000_000_001 + budget['estimated_construction_bytes'])
            self.assertEqual(budget['final_validation_disk_reserve_bytes'], 4_000_000_001)
            self.assertEqual(budget['runtime_disk_floor_bytes'], 3_000_000_000)
            self.assertEqual(budget['runtime_ram_floor_bytes'], 1_000_000_000)
            output = workspace / 'uboot-nvme-candidate-test'
            with patch.object(candidate.shutil, 'disk_usage', return_value=SimpleNamespace(free=4_000_000_000)), \
                    self.assertRaises(ValueError):
                candidate.check_capacity(workspace, output, budget)
            self.assertFalse(output.exists())
            # Satisfying only the live 3 GB floor cannot admit a construction
            # whose unchanged final core validation requires strictly >4 GB.
            old_admission = 3_000_000_000 + budget['estimated_construction_bytes'] + 1
            with patch.object(candidate.shutil, 'disk_usage', return_value=SimpleNamespace(free=old_admission)), \
                    self.assertRaises(ValueError):
                candidate.check_capacity(workspace, output, budget)
            with patch.object(candidate.shutil, 'disk_usage',
                              return_value=SimpleNamespace(free=budget['required_free_bytes'] + 1)):
                candidate.check_capacity(workspace, output, budget)
            lock['uboot_toolchain']['sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'Untrusted compiler archive'):
                candidate.capacity_budget(workspace, lock)


class RegisteredTargetGuards(unittest.TestCase):
    """Synthetic pinned data exercises input guards, never a real compiler."""
    def fixture(self, root, malformed_challenge=False):
        repo, inputs = root / 'repo', root / 'inputs'
        inputs.mkdir()
        policy = candidate.validate_policy(ROOT)
        def pin(data):
            return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        def write(relative, data, base=inputs):
            path = base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return pin(data)
        write(candidate.POLICY, json.dumps(policy).encode(), repo)
        core_bytes = (ROOT / 'build/uboot-replay.py').read_bytes()
        original_inputs = {'build/uboot-replay.py': write('build/uboot-replay.py', core_bytes, repo),
                           'build/uboot-nvme-candidate.py': pin(b'original producer')}
        original = {'project': {'commit': '1' * 40, 'inputs': original_inputs},
                    'builder_profile': {'name': candidate.PROFILE},
                    'core_recipe_sha256': candidate.CORE_SHA,
                    'baseline_manifest_sha256': core.MANIFEST_SHA, 'policy': policy}
        files = {}
        nonce = challenge().replace(b'0x00', b'0x80', 1)
        if malformed_challenge:
            nonce = nonce.replace(b'0x80', b'0X80', 1)
        for relative, data in {
                'artifacts/uboot-sunxi-challenge.c': nonce,
                'artifacts/uboot.config': candidate.candidate_config(config()),
                'artifacts/original-uboot.config': config(),
                'artifacts/source-provenance.json': json.dumps(original).encode()}.items():
            files[relative] = write(relative, data)
        # A full manifest may reference large original artifacts that the outer
        # launcher does not copy into its small namespace input snapshot.
        files['artifacts/uboot-unpacked.bin'] = pin(b'original binary')
        required = {name: files[name] for name in ('artifacts/uboot-sunxi-challenge.c',
                                                 'artifacts/uboot.config', 'artifacts/uboot-unpacked.bin')}
        manifest = {'schema': 1, 'hardware_acceptance': False, 'files': files}
        target = {'parent_source_revision': '1' * 40,
                  'original_producer': 'build/uboot-nvme-candidate.py',
                  'manifest': write('candidate-manifest.json', json.dumps(manifest).encode()),
                  'required_artifacts': required, 'original_project_inputs': original_inputs,
                  'source_provenance': files['artifacts/source-provenance.json']}
        evidence_path = 'docs/evidence/original.json'
        evidence = {'success': True, 'source_revision': '1' * 40, 'components': required}
        target['evidence'] = {'path': evidence_path,
                              **write(evidence_path, json.dumps(evidence).encode(), repo)}
        registry = {'schema': 1, 'targets': {candidate.REGISTERED_TARGET: target}}
        registry_pin = write(candidate.REGISTRY, json.dumps(registry).encode(), repo)
        def git(command, **kwargs):
            self.assertEqual(command[-2], 'show')
            return (repo / command[-1].removeprefix('HEAD:')).read_bytes()
        return repo, inputs, registry_pin, git

    def test_registry_matches_actual_candidate_evidence(self):
        # These tracked pins refer to the built 01372ec candidate, not the SD replay.
        registry_bytes = (ROOT / candidate.REGISTRY).read_bytes()
        self.assertEqual({'bytes': len(registry_bytes), 'sha256': hashlib.sha256(registry_bytes).hexdigest()},
                         candidate.REGISTRY_PIN)
        registry = json.loads(registry_bytes)
        self.assertEqual(set(registry['targets']), {candidate.REGISTERED_TARGET})
        target = registry['targets'][candidate.REGISTERED_TARGET]
        evidence = json.loads((ROOT / target['evidence']['path']).read_bytes())
        self.assertEqual(target['required_artifacts'], evidence['components'])
        self.assertEqual(target['manifest'], evidence['raw_pins']['component-retained/candidate-manifest.json'])
        self.assertEqual(target['parent_source_revision'], evidence['source_revision'])

    def test_small_snapshot_consumes_pinned_bytes_and_returns_outer_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, inputs, pin, git = self.fixture(Path(tmp))
            with patch.object(candidate, 'REGISTRY_PIN', pin), patch.object(core, 'run', side_effect=git):
                contract = candidate.load_registered_target(repo, candidate.REGISTERED_TARGET, inputs)
            self.assertEqual(contract['challenge_bytes'], challenge().replace(b'0x00', b'0x80', 1))
            self.assertEqual(contract['required_artifacts'], contract['target']['required_artifacts'])
            self.assertEqual(contract['source_provenance']['project']['commit'], '1' * 40)
            self.assertIn('artifacts/uboot-unpacked.bin', contract['original_manifest']['files'])
            self.assertFalse((inputs / 'artifacts/uboot-unpacked.bin').exists())

    def test_unregistered_target_rejected_before_input_or_build(self):
        with patch.object(core, 'run') as run, patch.object(core, 'patch_generator') as generator:
            with self.assertRaisesRegex(ValueError, 'Unregistered NVMe'):
                candidate.load_registered_target(ROOT, 'arbitrary', Path('/missing'))
            run.assert_not_called()
            generator.assert_not_called()

    def test_wrong_manifest_challenge_provenance_and_current_core_refused(self):
        for relative in ('candidate-manifest.json', 'artifacts/uboot-sunxi-challenge.c',
                         'artifacts/source-provenance.json', 'artifacts/uboot.config',
                         'artifacts/original-uboot.config', 'build/uboot-replay.py'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as tmp:
                repo, inputs, pin, git = self.fixture(Path(tmp))
                path = (repo if relative.startswith('build/') else inputs) / relative
                path.write_bytes(path.read_bytes() + b'altered')
                with patch.object(candidate, 'REGISTRY_PIN', pin), patch.object(core, 'run', side_effect=git), \
                        patch.object(core, 'patch_generator') as generator, self.assertRaises(ValueError):
                    candidate.load_registered_target(repo, candidate.REGISTERED_TARGET, inputs)
                generator.assert_not_called()

    def test_canonical_format_required_even_when_challenge_hash_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, inputs, pin, git = self.fixture(Path(tmp), malformed_challenge=True)
            with patch.object(candidate, 'REGISTRY_PIN', pin), patch.object(core, 'run', side_effect=git), \
                    self.assertRaisesRegex(ValueError, 'Noncanonical'):
                candidate.load_registered_target(repo, candidate.REGISTERED_TARGET, inputs)

    def test_git_and_registry_pin_are_both_required(self):
        for defect in ('git', 'registry'):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as tmp:
                repo, inputs, pin, git = self.fixture(Path(tmp))
                if defect == 'registry':
                    (repo / candidate.REGISTRY).write_bytes(b'untrusted')
                with patch.object(candidate, 'REGISTRY_PIN', pin), \
                        patch.object(core, 'run', side_effect=git if defect == 'registry' else lambda *a, **k: b'wrong'), \
                        self.assertRaises(ValueError):
                    candidate.load_registered_target(repo, candidate.REGISTERED_TARGET, inputs)

    def test_each_exact_output_and_retained_input_mismatch_is_refused(self):
        names = ('uboot-unpacked.bin', 'boot_package.fex', 'uboot.config',
                 'uboot-sunxi-challenge.c', 'boot0_spinor_a733.fex', 'registered-challenge.c')
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            for name in names:
                (artifacts / name).write_bytes(b'expected')
            expected = core.facts(artifacts / names[0])
            contract = {'target': {'required_artifacts': {
                'artifacts/' + name: expected for name in names[:-1]}}}
            candidate.verify_replay_outputs(artifacts, contract)
            for name in names:
                with self.subTest(name=name):
                    (artifacts / name).write_bytes(b'mismatch')
                    with self.assertRaises(ValueError):
                        candidate.verify_replay_outputs(artifacts, contract)
                    (artifacts / name).write_bytes(b'expected')

    def test_safe_held_reads_refuse_symlinks_hardlinks_and_parent_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            source.write_bytes(b'input')
            pin = core.facts(source)
            self.assertEqual(candidate.pinned_read(source, pin), b'input')
            alias = root / 'alias'
            alias.symlink_to(source)
            with self.assertRaises(ValueError):
                candidate.pinned_read(alias, pin)
            alias.unlink()
            alias.hardlink_to(source)
            with self.assertRaises(ValueError):
                candidate.pinned_read(source, pin)
            alias.unlink()
            directory = root / 'dir-alias'
            directory.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                candidate.pinned_read(directory / 'source', pin)


class SimulatedBuild(unittest.TestCase):
    """Exercise candidate orchestration with simulated compiler/vendor tools."""
    def test_failed_and_successful_component_records(self):
        for scenario in ('compile', 'shared-change', 'fresh', 'replay', 'replay-mismatch'):
            replaying = scenario.startswith('replay')
            fail = scenario if scenario in ('compile', 'shared-change', 'replay-mismatch') else None
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo, workspace, provenance = root / 'repo', root / 'workspace', root / 'provenance'
                (repo / 'out').mkdir(parents=True)
                (repo / 'configs').mkdir()
                (repo / 'build/patches').mkdir(parents=True)
                (workspace / 'downloads').mkdir(parents=True)
                provenance.mkdir()
                output = repo / 'out/uboot-nvme-candidate-test'
                manifest = {'files': {}}
                for name in core.RETAINED:
                    content = config() if name == 'uboot.config' else challenge() if name.endswith('challenge.c') else b'original'
                    (provenance / name).write_bytes(content)
                    manifest['files'][name] = core.facts(provenance / name)
                manifest_bytes = json.dumps(manifest).encode()
                (provenance / 'artifact-manifest.json').write_bytes(manifest_bytes)
                for name in ('uboot-env-nowhere-size.patch', 'uboot-a733-el2-handoff.patch'):
                    (repo / 'build/patches' / name).write_bytes(b'original')
                before = {'project_source': {'inputs': {}}, 'candidate_policy': {}, 'builder_profile': {}}
                for name in ('diagnostic-sources.lock.json', 'vendor-boot-inputs.sha256', *core.BUILDER_PROFILES.values()):
                    path = repo / 'configs' / name
                    path.write_bytes(b'lock')
                    before[str(path)] = core.facts(path)
                archive = workspace / 'downloads/compiler.tar.xz'
                archive.write_bytes(b'compiler')
                before[str(archive)] = core.facts(archive)
                lock = {'uboot_toolchain': {'filename': archive.name}}
                registered_challenge = challenge().replace(b'0x00', b'0x80', 1)
                def pin(data):
                    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                contract = {'target_id': candidate.REGISTERED_TARGET, 'inputs': '/fixture/inputs',
                            'registry_pin': {'sha256': 'fixture-registry'},
                            'original_manifest_pin': {'sha256': 'fixture-manifest'},
                            'original_provenance': {'builder_profile': before['builder_profile']},
                            'challenge_bytes': registered_challenge,
                            'target': {'required_artifacts': {
                                'artifacts/uboot-unpacked.bin': pin(b'other-candidate' if fail == 'replay-mismatch' else b'new-candidate'),
                                'artifacts/boot_package.fex': pin(b'new-package'),
                                'artifacts/uboot.config': pin(candidate.candidate_config(config())),
                                'artifacts/boot0_spinor_a733.fex': pin(b'firmware'),
                                'artifacts/uboot-sunxi-challenge.c': pin(registered_challenge)}}}
                commands = []
                def run(command, cwd=None, env=None, log=None):
                    command = list(map(str, command))
                    commands.append(command)
                    tree = output / 'uboot-source'
                    if command[:2] == ['tar', '-xf']:
                        for directory in ('board/sunxi', 'arch/arm/lib'):
                            (tree / directory).mkdir(parents=True)
                        (tree / 'Makefile').write_text(core.GENERATOR)
                        (tree / 'arch/arm/lib/bootm.c').write_text('0x8000ff40')
                    if command[-1] == '-dumpfullversion':
                        return b'7.4.1\n'
                    if command[-1] == '--version':
                        return b'simulated compiler\n'
                    if command[0] == 'make' and command[-1] == '-j2':
                        if fail == 'compile':
                            raise subprocess.CalledProcessError(1, command)
                        for relative in ('u-boot.bin', 'u-boot', 'u-boot.map', 'arch/arm/lib/bootm.o'):
                            (tree / relative).write_bytes(b'new-candidate')
                        (tree / 'board/sunxi/sunxi_challenge.c').write_bytes(
                            (output / 'artifacts/registered-challenge.c').read_bytes() if replaying else challenge())
                    if command[0] == 'dragonsecboot':
                        (cwd / 'boot_package.fex').write_bytes(b'new-package')
                    return b''
                def private_pack(workspace, artifacts, output):
                    pack, tools = artifacts / 'pack', output / 'pack-tools'
                    pack.mkdir()
                    tools.mkdir()
                    for relative in ('monitor.fex', 'scp.fex', 'boot0_spinor_a733.fex',
                                     'boot0_sdcard_a733.fex', 'sys_config/sys_config.fex', 'dts/u-boot-current.dts'):
                        path = pack / relative
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(b'firmware')
                    return pack, tools
                with patch.object(candidate, 'capacity_budget', return_value={'required_free_bytes': 0}), \
                        patch.object(candidate, 'check_capacity'), \
                        patch.object(candidate, 'Runner', return_value=SimpleNamespace(run=run)), \
                        patch.object(candidate, 'load_registered_target', return_value=contract), \
                        patch.object(candidate, 'private_pack', side_effect=private_pack), \
                        patch.object(candidate, 'snapshot', return_value=({'mutated': True} if fail == 'shared-change' else before, lock)), \
                        patch.object(candidate, 'SPI_BOOT0', {'bytes': 8, 'sha256': hashlib.sha256(b'firmware').hexdigest()}), \
                        patch.object(core, 'MANIFEST_SHA', hashlib.sha256(manifest_bytes).hexdigest()):
                    if fail:
                        error = subprocess.CalledProcessError if fail == 'compile' else ValueError
                        with self.assertRaises(error):
                            candidate.build(repo, workspace, provenance, output, manifest, before, lock,
                                            replay_contract=contract if replaying else None)
                    else:
                        candidate.build(repo, workspace, provenance, output, manifest, before, lock,
                                            replay_contract=contract if replaying else None)
                result = json.loads((output / 'artifacts/candidate-result.json').read_text())
                self.assertEqual(result['component_build_success'], not fail)
                self.assertFalse(result['hardware_acceptance'])
                self.assertFalse(result['deployment_authorized'])
                self.assertTrue((output / 'candidate-manifest.json').is_file())
                self.assertTrue((output / 'uboot-source/Makefile').exists())
                self.assertEqual((output / 'uboot-source/Makefile').read_text(),
                                 core.REPLAY_GENERATOR if replaying else core.GENERATOR)
                makes = [command for command in commands if command[0] == 'make']
                self.assertEqual([command[-1] for command in makes], ['sun60iw2p1_t736_defconfig', 'olddefconfig', '-j2'])
                self.assertTrue(all('-j2' in command for command in makes))
                self.assertEqual(any('A733_REPLAY_CHALLENGE' in item for command in commands for item in command), replaying)
                if fail:
                    self.assertNotIn('registered_replay', result)
                self.assertEqual((output / 'artifacts/uboot-challenge-replay.patch').exists(), replaying)
                if replaying:
                    self.assertEqual((output / 'artifacts/registered-challenge.c').read_bytes(), registered_challenge)
                    self.assertIn(core.GENERATOR.split('\n')[3], (output / 'uboot-source/Makefile').read_text())
                if not fail:
                    provenance_result = json.loads((output / 'artifacts/source-provenance.json').read_text())
                    self.assertEqual(provenance_result['challenge_mode'], 'registered' if replaying else 'fresh')
                    self.assertNotIn('fresh_generated_challenge', provenance_result)
                    if replaying:
                        self.assertTrue(result['registered_replay']['exact_matches_verified'])
                        self.assertEqual(set(result['registered_replay']['matched_artifacts']),
                                         set(contract['target']['required_artifacts']))
                        self.assertEqual(provenance_result['registered_replay'], result['registered_replay'])
                    else:
                        self.assertNotIn('registered_replay', result)
                    self.assertTrue((output / 'artifacts/bootm.disassembly').exists())
                    self.assertEqual((output / 'artifacts/uboot-unpacked.bin').read_bytes(), b'new-candidate')
                    self.assertEqual((output / 'artifacts/original-uboot-unpacked.bin').read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()

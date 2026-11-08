"""Read-only synthetic runtime-corrected stream fixtures; no production/hardware claim."""
import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


A = load('runtime_fixes_stream_adapter', 'build/verify-nvme-runtime-fixes-stream.py')
F = load('original_stream_fixtures', 'tests/test_verify_nvme_stream.py')
ORIGINAL = F.V
ORIGINAL_FIXTURE = F.source_fixture


def source_fixture():
    # Rebind actual component records as data, never as a successful NVMe run.
    with mock.patch.object(F, 'V', ORIGINAL):
        receipt, manifest = ORIGINAL_FIXTURE()
    producers = {name: json.loads((ROOT / ('docs/evidence/production-host-' + name + '-runtime-fixes-20261108.json')).read_bytes())
                 for name in ('roots', 'state', 'bootstrap')}
    receipt['sources']['revision'] = V.REVISION
    receipt['phase'] = V.PHASE
    receipt['output'] = A.SUBSTITUTIONS['work/project/out/nvme-image-timesync-03']
    receipt['policy'].update(producers=producers, state_uuid=V.STATE_UUID,
                             boot_profile=copy.deepcopy(producers['bootstrap']['boot_profile']))
    manifest.update(state_uuid=V.STATE_UUID, boot_profile=copy.deepcopy(producers['bootstrap']['boot_profile']),
                    boot_selection=producers['state']['boot_selection'])
    manifest['inputs'] = {role + '_manifest': producers[name]['verification']['manifest']
                          for role, name in (('roots', 'roots'), ('state', 'state'), ('sd', 'bootstrap'))}
    manifest['partition_payloads'] = {'root-a': producers['roots']['readback']['slots']['A']['image'],
                                    'root-b': producers['roots']['readback']['slots']['B']['image'],
                                    'state': producers['state']['state_image']}
    manifest['filesystem_uuids'] = {'root-a': producers['roots']['slot_uuids']['A'],
                                  'root-b': producers['roots']['slot_uuids']['B'], 'state': V.STATE_UUID}
    manifest_pin = V.pin(json.dumps(manifest).encode())
    receipt['verification']['manifest'] = manifest_pin
    receipt['readback_expectations']['nvme.json']['value'].update(
        manifest=manifest_pin, partition_payloads=manifest['partition_payloads'])
    return receipt, manifest


with A.Template(ROOT / A.TEMPLATE) as template:
    V = A.load_template(template)


class ReusedFixtures:
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(F, 'V', V))
        stack.enter_context(mock.patch.object(F, 'source_fixture', source_fixture))


class DecoderTests(ReusedFixtures, F.DecoderTests):
    pass


class MetadataTests(ReusedFixtures, F.MetadataTests):
    pass


class AuthenticationTests(ReusedFixtures, F.AuthenticationTests):
    pass


class GptTests(ReusedFixtures, F.GptTests):
    pass


class AdapterTests(unittest.TestCase):
    def test_exact_seven_literal_changes_only_and_old_reader_unchanged(self):
        raw = (ROOT / A.TEMPLATE).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), A.TEMPLATE_SHA)
        self.assertEqual(len(A.SUBSTITUTIONS), 7)
        expected = ast.parse(raw, filename=A.TEMPLATE)
        counts = dict.fromkeys(A.SUBSTITUTIONS, 0)
        for node in ast.walk(expected):
            if isinstance(node, ast.Constant) and type(node.value) is str and node.value in A.SUBSTITUTIONS:
                old = node.value
                counts[old] += 1
                node.value = A.SUBSTITUTIONS[old]
        self.assertEqual(counts, dict.fromkeys(A.SUBSTITUTIONS, 1))
        self.assertEqual(ast.dump(expected, include_attributes=True),
                         ast.dump(A.adapted_ast(raw), include_attributes=True))
        self.assertEqual(V.__file__, str(ROOT / A.SELF))
        self.assertEqual(ORIGINAL.STATE_UUID, 'fc8bbef6-9385-4ddb-90ba-e41eaccc035b')
        self.assertEqual(V.HELPER, ORIGINAL.HELPER)
        self.assertEqual(V.SIZE, 256060514304)
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            A.adapted_ast(raw + b'\n')
        for changed in (raw.replace(b'nvme-timesync-03', b'other'),
                        raw + b'\nextra="work/project/out/nvme-image-timesync-03"\n'):
            with mock.patch.object(A, 'TEMPLATE_SHA', hashlib.sha256(changed).hexdigest()):
                with self.assertRaisesRegex(ValueError, 'anchors'):
                    A.adapted_ast(changed)

    def test_source_pins_and_binary_stdin_cli_are_preserved(self):
        self.assertEqual(V.HELPER, V.pin((ROOT / 'tests/verify_nvme_image.py').read_bytes()))
        self.assertEqual(V.SPI, V.pin((ROOT / 'configs/spi-nvme-host-boot.cmd').read_bytes()))
        self.assertEqual(V.REVISION, '6c34629c424a1bb414780b26461e06ccd2854ae9')
        self.assertEqual(V.PHASE, '/var/lib/a733-clean-replay-20261106/nvme-timesync-runtime-fixes-01')
        self.assertEqual(V.STATE_UUID, '360dfb39-f49f-487e-b307-0bb887c854c5')
        wire = io.BytesIO(b'binary stdin forwarding fixture')
        flags = types.SimpleNamespace(isolated=True, dont_write_bytecode=True)
        # In-process argument forwarding only; no CLI subprocess or real stream
        # success is represented by the stubbed verifier.
        with mock.patch.object(V.sys, 'flags', flags), \
                mock.patch.object(V.sys, 'stdin', types.SimpleNamespace(buffer=wire)), \
                mock.patch.object(V, 'verify', return_value={'fixture': True}) as verify, \
                contextlib.redirect_stdout(io.StringIO()):
            V.main(['--receipt', 'receipt.json', '--receipt-sha256', 'a' * 64,
                    '--manifest', 'manifest.json'])
        verify.assert_called_once_with(wire, Path('receipt.json'), 'a' * 64, Path('manifest.json'))

    def test_old_new_and_canonical_policy_contracts(self):
        receipt, manifest = source_fixture()
        V.authenticate(receipt, manifest)
        for name, value in receipt['policy']['producers'].items():
            self.assertEqual(hashlib.sha256(json.dumps(value, sort_keys=True,
                separators=(',', ':')).encode()).hexdigest(), V.PRODUCERS[name])
        with self.assertRaises(ValueError):
            ORIGINAL.authenticate(receipt, manifest)
        with self.assertRaises(ValueError):
            V.authenticate(*ORIGINAL_FIXTURE())
        mutations = [lambda r, m: r['sources'].update(revision=ORIGINAL.REVISION),
                     lambda r, m: r.update(phase=ORIGINAL.PHASE),
                     lambda r, m: r.update(output='work/project/out/nvme-image-timesync-03'),
                     lambda r, m: r['policy'].update(state_uuid=ORIGINAL.STATE_UUID),
                     lambda r, m: m.update(state_uuid=ORIGINAL.STATE_UUID)]
        for name in V.PRODUCERS:
            mutations.append(lambda r, m, name=name: r['policy']['producers'][name].update(extra=True))
        for mutation in mutations:
            r, m = copy.deepcopy(source_fixture())
            mutation(r, m)
            with self.assertRaises(ValueError):
                V.authenticate(r, m)

    def test_template_unsafe_paths_changed_bytes_and_ancestry(self):
        for path in ('/dev/null', '/proc/self/status', '/sys/kernel/uevent_seqnum'):
            with mock.patch.object(A.os, 'open', side_effect=AssertionError('unsafe open')):
                with self.assertRaises(ValueError):
                    A.Template(path)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            parent = base / 'parent'
            parent.mkdir()
            leaf = parent / 'template'
            leaf.write_bytes((ROOT / A.TEMPLATE).read_bytes())
            (base / 'link').symlink_to(parent, target_is_directory=True)
            (parent / 'link').symlink_to(leaf)
            os.mkfifo(parent / 'fifo')
            for path in (base / 'link/template', parent / 'link', parent / 'fifo', parent):
                with self.assertRaises((OSError, ValueError)):
                    A.Template(path)
            with A.Template(leaf) as held:
                leaf.write_bytes(b'x' * len(held.data))
                with self.assertRaisesRegex(ValueError, 'SHA256'):
                    with A.Template(leaf) as changed:
                        A.load_template(changed)
                with self.assertRaises(ValueError):
                    held.unchanged()
            leaf.write_bytes((ROOT / A.TEMPLATE).read_bytes())
            with A.Template(leaf) as held:
                parent.rename(base / 'old')
                parent.mkdir()
                leaf.write_bytes(held.data)
                with self.assertRaises(ValueError):
                    held.unchanged()
            os.link(leaf, parent / 'hard')
            with self.assertRaises(ValueError):
                A.Template(leaf)

    def test_template_fence_and_mutation_before_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'template'
            path.write_bytes((ROOT / A.TEMPLATE).read_bytes())
            with A.Template(path) as held:
                parent = A.load_template(held)
                calls = []
                parent.verify = lambda *a, **k: {'success': True}
                A.adapt(parent, held)
                report = parent.verify()
                self.assertTrue(report['runtime_fixes_stream_reader']['historical_template_identity_unchanged'])
                self.assertEqual(report['runtime_fixes_stream_reader']['registered_literals'], A.SUBSTITUTIONS)
                wire = F.packed(b'tiny stream')
                parent.decode(io.BytesIO(wire), parent.pin(wire), parent.pin(b'tiny stream'),
                              parent.Ranges(11, []), lambda: calls.append(True))
                self.assertEqual(len(calls), 2)
                path.write_bytes(b'x' * len(held.data))
                with self.assertRaises(ValueError):
                    parent.verify()
                with self.assertRaises(ValueError):
                    parent.decode(io.BytesIO(wire), parent.pin(wire), parent.pin(b'tiny stream'),
                                  parent.Ranges(11, []), lambda: None)

    def test_template_mutation_during_real_stream_fails_final_fence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'template'
            path.write_bytes((ROOT / A.TEMPLATE).read_bytes())
            with A.Template(path) as held:
                parent = A.load_template(held)
                A.adapt(parent, held)
                data = b'actual tiny gzip mutation fixture'
                wire = F.packed(data)
                class MutatingStream(io.BytesIO):
                    def read(self, count):
                        result = super().read(count)
                        if result:
                            path.write_bytes(b'x' * len(held.data))
                        return result
                with self.assertRaises(ValueError):
                    parent.decode(MutatingStream(wire), parent.pin(wire), parent.pin(data),
                                  parent.Ranges(len(data), []), lambda: None)

    def test_mutation_inside_original_verify_rejects_before_main_print(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'template'
            path.write_bytes((ROOT / A.TEMPLATE).read_bytes())
            with A.Template(path) as held:
                parent = A.load_template(held)
                def mutate(*args, **kwargs):
                    path.write_bytes(b'x' * len(held.data))
                    return {'success': True}
                parent.verify = mutate
                A.adapt(parent, held)
                flags = types.SimpleNamespace(isolated=True, dont_write_bytecode=True)
                output = io.StringIO()
                with mock.patch.object(parent.sys, 'flags', flags), contextlib.redirect_stdout(output):
                    with self.assertRaises(ValueError):
                        parent.main(['--receipt', 'fixture', '--receipt-sha256', 'a' * 64, '--manifest', 'fixture'])
                self.assertEqual(output.getvalue(), '')

    def test_registered_policy_graph_fits_unchanged_json_limits(self):
        launcher = load('runtime_fixes_nvme_launcher_fixture', 'build/launch-nvme-host-runtime-fixes.py')
        with contextlib.ExitStack() as stack:
            held = stack.enter_context(launcher.Template(ROOT / launcher.TEMPLATE, launcher.TEMPLATE_SHA))
            parent = launcher.load_template(held)
            launcher.adapt(parent, held, stack)
            roots = load('runtime_fixes_nvme_roots_fixture', 'build/launch-host-roots.py')
            # Read only contract-requested source files, without repository-wide
            # discovery. Exercise the actual nested producers with forks forbidden.
            class SourceFiles(dict):
                def __missing__(self, name):
                    value = (ROOT / name).read_bytes()
                    self[name] = value
                    return value
            data = SourceFiles()
            with mock.patch.object(subprocess, 'run', side_effect=AssertionError('subprocess')), \
                    mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('subprocess')), \
                    mock.patch.object(subprocess, 'check_output', side_effect=AssertionError('subprocess')):
                policy = parent.contract(roots, ROOT, data)
            self.assertEqual(policy['producers'], source_fixture()[0]['policy']['producers'])
            for name, registration in launcher.REGISTRATIONS.items():
                producer = policy['producers'][name]
                canonical = hashlib.sha256(json.dumps(producer, sort_keys=True,
                    separators=(',', ':')).encode()).hexdigest()
                self.assertEqual(canonical, registration['canonical_sha256'])
                self.assertEqual(canonical, V.PRODUCERS[name])
            # Historical records() requires these addressing keys. The corrected
            # launcher must add them privately, preserving the published policy.
            self.assertNotIn('builder_output', policy['producers']['roots']['readback'])
            self.assertNotIn('receipt', policy['producers']['roots']['readback'])
            before = copy.deepcopy(policy)
            records = parent.records(policy)
            self.assertEqual(policy, before)
            binding = json.loads(data[launcher.READBACK_REGISTRATION['path']])
            self.assertEqual(records['roots-readback'],
                             (Path(binding['builder_output']) / 'receipt.json', binding['receipt']))
            encoded = json.dumps(policy).encode()
            self.assertEqual(V.json_bytes(encoded), policy)
            files = {name: {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                            'git_blob': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()}
                     for name, raw in data.items()}
            planned = parent.plan('timesync-runtime-fixes-01',
                                  {'revision': V.REVISION, 'files': files}, policy)
            self.assertEqual(V.json_bytes(json.dumps(planned).encode()), planned)
            receipt, manifest = source_fixture()
            self.assertEqual(V.json_bytes(json.dumps(receipt).encode()), receipt)
            # Authenticate an actual producer-policy shape, not a fixture with
            # invented addressing fields added to the original root evidence.
            receipt['policy'] = policy
            manifest['recipes'] = copy.deepcopy(policy['recipes'])
            proof = receipt['readback_expectations']['nvme.json']['value']
            proof['spi_script_wrapper']['helper'] = policy['spi_wrapper_helper']
            receipt['verification']['manifest'] = proof['manifest'] = V.pin(json.dumps(manifest).encode())
            V.authenticate(receipt, manifest)
            self.assertLess(len(encoded), 4 * V.MIB)


if __name__ == '__main__':
    unittest.main()

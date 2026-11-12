"""Build-only adapter checks; fixture execution never stages containers or devices."""
import ast
import contextlib
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('/', '_'), REPO / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


L = load('build/launch-deterministic-host-roots.py')
ROOTS = load(L.TEMPLATE)
MAINTENANCE = load('build/maintenance-tools.py')


def pin(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def source_contents():
    names = subprocess.check_output(['git', '-C', str(REPO), 'ls-files', '-z']).decode().split('\0')
    names += [L.SELF, L.HELPER, *MAINTENANCE.SOURCES]
    return {name: (REPO / name).read_bytes() for name in names if name}


class DeterministicLauncherTests(unittest.TestCase):
    def parent(self, stack):
        held = stack.enter_context(L.Template(REPO / L.TEMPLATE, L.TEMPLATE_SHA))
        parent = L.load_template(held)
        L.adapt(parent, held)
        return parent, held

    def test_ast_changes_only_one_self_literal_and_rejects_drift(self):
        raw = (REPO / L.TEMPLATE).read_bytes()
        expected = ast.parse(raw)
        count = 0
        for node in ast.walk(expected):
            if isinstance(node, ast.Constant) and node.value == L.TEMPLATE:
                node.value = L.SELF
                count += 1
        self.assertEqual(count, 1)
        self.assertEqual(ast.dump(L.adapted_ast(raw), include_attributes=True),
                         ast.dump(expected, include_attributes=True))
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            L.adapted_ast(raw + b'\n')
        for mutation in (raw.replace(L.TEMPLATE.encode(), b'unknown-self'),
                         raw + ('\nextra=' + repr(L.TEMPLATE)).encode()):
            with mock.patch.object(L, 'TEMPLATE_SHA', hashlib.sha256(mutation).hexdigest()):
                with self.assertRaisesRegex(ValueError, 'anchors'):
                    L.adapted_ast(mutation)

    def test_contract_and_pure_plan_keep_old_policy_and_closure(self):
        before = MAINTENANCE.entries()
        self.assertEqual(len(MAINTENANCE.SOURCES), 77)
        self.assertNotIn(L.HELPER, MAINTENANCE.SOURCES)
        data = source_contents()
        with contextlib.ExitStack() as stack:
            parent, held = self.parent(stack)
            policy = parent.contract(REPO, data)
            self.assertEqual(policy, ROOTS.contract(REPO, data))
            self.assertEqual(set(policy), {'archive', 'diagnostic', 'binding', 'clock'})
            manifest = {'revision': 'a' * 40, 'files': {name: pin(raw) for name, raw in data.items()}}
            with mock.patch.object(Path, 'lstat', side_effect=AssertionError('input inspection')), \
                 mock.patch.object(Path, 'mkdir', side_effect=AssertionError('staging')):
                plan = parent.plan('fixture', manifest, policy, Path('/absent/diagnostic'), Path('/absent/userspace'))
            original = ROOTS.plan('fixture', manifest, policy, Path('/absent/diagnostic'), Path('/absent/userspace'))
            self.assertEqual({key: value for key, value in plan.items() if key != 'filesystem_normalization'}, original)
            self.assertEqual(plan['filesystem_normalization'], L.normalization_binding(pin(data[L.HELPER]), held.pin))
            self.assertEqual(parent.SELF, L.SELF)
            self.assertEqual(parent.__file__, str(REPO / L.SELF))
            for name in (L.TEMPLATE, L.HELPER):
                bad = copy.deepcopy(manifest)
                bad['files'][name]['sha256'] = '0' * 64
                with self.assertRaisesRegex(ValueError, 'source closure'):
                    parent.plan('fixture', bad, policy, Path('/missing'), Path('/missing'))
            bad = dict(data)
            del bad[L.HELPER]
            with self.assertRaisesRegex(ValueError, 'helper missing'):
                parent.contract(REPO, bad)
            with self.assertRaisesRegex(ValueError, 'outside committed closure'):
                parent.contract(REPO, dict(data, **{L.TEMPLATE: data[L.TEMPLATE] + b'\n'}))
        self.assertEqual(MAINTENANCE.entries(), before)
        self.assertEqual(pin((REPO / L.TEMPLATE).read_bytes())['sha256'], L.TEMPLATE_SHA)

    def test_same_cli_without_new_override_flags_and_plan_never_executes(self):
        with contextlib.ExitStack() as stack:
            parent, _ = self.parent(stack)
            data = source_contents()
            manifest = {'revision': 'a' * 40, 'files': {name: pin(raw) for name, raw in data.items()}}
            flags = type('Flags', (), {'isolated': 1, 'dont_write_bytecode': 1})()
            with mock.patch.object(parent, 'committed_sources', return_value=(manifest, data)), \
                 mock.patch.object(parent, 'read_source', return_value=data[L.SELF]), \
                 mock.patch.object(sys, 'flags', flags), \
                 mock.patch.object(parent, 'preflight', side_effect=AssertionError('staging')), \
                 mock.patch.object(parent, 'execute', side_effect=AssertionError('execution')), \
                 mock.patch('builtins.print') as printed:
                parent.main(['--project', str(REPO), '--tag', 'fixture', '--diagnostic', '/absent/diag',
                             '--userspace', '/absent/rootfs', '--plan'])
                result = json.loads(printed.call_args.args[0])
                self.assertFalse(result['hardware_acceptance'])
            tree = ast.parse((REPO / L.TEMPLATE).read_bytes())
            parser_flags = [call.args[0].value for call in ast.walk(tree)
                            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                            and call.func.attr == 'add_argument']
            self.assertEqual(set(parser_flags), {'--project', '--tag', '--diagnostic', '--userspace', '--plan'})

    def test_final_template_check_follows_original_callback_before_publication(self):
        events = []
        with L.Template(REPO / L.TEMPLATE, L.TEMPLATE_SHA) as held:
            parent = L.load_template(held)
            def execute(*args, **kwargs):
                kwargs['final_check']()
                events.append('publish')
            parent.execute = execute
            L.adapt(parent, held)
            with mock.patch.object(held, 'unchanged', side_effect=lambda: events.append('template')):
                parent.execute(final_check=lambda: events.append('original'))
            self.assertEqual(events, ['original', 'template', 'publish'])
            events.clear()
            with mock.patch.object(held, 'unchanged', side_effect=ValueError('template changed')):
                with self.assertRaisesRegex(ValueError, 'template changed'):
                    parent.execute()
            self.assertEqual(events, [])

    def test_payload_anchor_counts_and_snapshot_order(self):
        helper_pin = pin(b'fixture')
        program = L.normalized_program(ROOTS.payload_program, 'fixture', helper_pin).decode()
        self.assertLess(program.index('normalizer.prepare_tree(root,'), program.index(' before=snapshot(root)'))
        self.assertLess(program.index(' before=snapshot(root)'), program.index('normalizer.make_image('))
        self.assertLess(program.index('normalizer.make_image('), program.index(" assert snapshot(root)==before"))
        self.assertLess(program.index('payload.build('), program.index("manifest['filesystem_normalization']"))
        self.assertNotIn(' original(root,output,slot,size)', program)
        self.assertEqual(program.count('normalizer.prepare_tree('), 1)
        self.assertNotIn('root-payload.json', program)
        for anchor in ('spec.loader.exec_module(payload)\n', ' before=snapshot(root)\n',
                       ' original(root,output,slot,size)\n'):
            raw = ROOTS.payload_program('fixture').decode()
            for bad in (raw.replace(anchor, ''), raw + anchor):
                with self.assertRaisesRegex(ValueError, 'anchor differs'):
                    L.normalized_program(lambda tag: bad.encode(), 'fixture', helper_pin)

    def test_mock_payload_prepares_after_writes_and_keeps_marker_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'project'
            (project / 'build').mkdir(parents=True)
            readback = Path(directory) / 'readback'
            # A small independent producer fixture exercises the actual generated
            # capture and manifest updates without privileged execution.
            payload = '''import json,pathlib
SLOT_BYTES=4294967296
def make_image(*args):raise AssertionError('legacy creator called')
def write_json(path,value):pathlib.Path(path).write_text(json.dumps(value))
def build(archive,diagnostic,output,**kwargs):
 output.mkdir(parents=True)
 for slot in ('A','B'):
  root=output.parent/('stage-'+slot);root.mkdir()
  (root/'data').write_bytes(b'finished staging')
  (root/'root-payload.json').write_bytes(b'unchanged marker')
  make_image(root,output/('root-'+slot+'.ext4'),slot)
 write_json(output/'manifest.json',{'sources':{'fixture':True},'files':{},'reproducibility':'old'})
'''
            (project / 'build/host-payload.py').write_text(payload)
            helper = ('''import pathlib
def prepare_tree(root,epoch):
 assert (root/'data').read_bytes()==b'finished staging'
 assert (root/'root-payload.json').read_bytes()==b'unchanged marker'
def make_image(root,output,slot,size,epoch):
 expected=pathlib.Path(READBACK)/('slot-'+slot+'.expected.json')
 assert expected.is_file(),'snapshot must precede creator'
 output.write_bytes(b'fixture image')
 return {'epoch':epoch,'inode_count':3,'tools':{'fixture':'1'}}
'''.replace('READBACK', repr(str(readback)))).encode()
            helper_path = project / L.HELPER
            helper_path.write_bytes(helper)
            program = L.normalized_program(ROOTS.payload_program, 'fixture', pin(helper)).decode()
            program = program.replace("'/a733-export-output/project'", repr(str(project)))
            program = program.replace("'/a733-export-output/readback'", repr(str(readback)))
            exec(compile(program, '<fixture-program>', 'exec'), {})
            output = project / 'out/host-payload-fixture'
            manifest = json.loads((output / 'manifest.json').read_bytes())
            self.assertEqual(manifest['sources'], {'fixture': True})
            self.assertEqual(manifest['files'], {})
            self.assertEqual(manifest['filesystem_normalization'], {'profile': L.PROFILE, 'epoch': L.EPOCH,
                'helper': pin(helper), 'slots': {slot: {'epoch': L.EPOCH, 'inode_count': 3,
                'tools': {'fixture': '1'}} for slot in ('A', 'B')}})
            self.assertEqual(manifest['reproducibility'], 'normalized creator; actual repeat proof pending')
            for slot in ('A', 'B'):
                marker = output.parent / ('stage-' + slot) / 'root-payload.json'
                self.assertEqual(marker.read_bytes(), b'unchanged marker')
                self.assertTrue((readback / ('slot-' + slot + '.stable.json')).is_file())
            helper_path.write_bytes(helper + b'\n')
            with self.assertRaisesRegex(AssertionError, 'helper differs'):
                exec(compile(program, '<tampered-fixture-program>', 'exec'), {})


if __name__ == '__main__':
    unittest.main()

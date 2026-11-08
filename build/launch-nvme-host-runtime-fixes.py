#!/usr/bin/env python3
"""Compose only the registered runtime-corrected components; same historical file-only CLI.

The authenticated historical body changes only counted AST string constants.
Construction and host readback cannot establish physical hardware acceptance.
"""
import ast
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types

SELF = 'build/launch-nvme-host-runtime-fixes.py'
TEMPLATE = 'build/launch-nvme-host.py'
TEMPLATE_SHA = '5ae2db40d7f9e599fff34a2bc6dc6e14f927ffb8a47ecaac9dcb140b7d5a03d5'
BOOTSTRAP = 'build/launch-host-bootstrap-runtime-fixes.py'
BOOTSTRAP_SHA = 'de01fe092e5262d8433e7b2ca4c1f56f740ac840467b2b98f41d4762fef8384e'
REGISTRATIONS = {'roots': {'path': 'docs/evidence/production-host-roots-runtime-fixes-20261108.json',
           'pin': {'bytes': 17141,
                   'sha256': '12c45544ac379a0ca11312f833f2e49333b0cf94dc55156a280a18044ba8003d'},
           'git_blob': '63ee6c9c17dbca0f434feaef010c7a0fa630f070',
           'canonical_sha256': '2c04a75d5239224b80a721442e88ea872ccd63d18aaae841539d007697302971'},
 'state': {'path': 'docs/evidence/production-host-state-runtime-fixes-20261108.json',
           'pin': {'bytes': 13777,
                   'sha256': 'd0566a7ed65fdb192890c78655e6d30a871fd2584576dd669d0ddddd8eb02e04'},
           'git_blob': 'a2ea1c12cfb0d566c6348f92e6b3bcc7b2c657ac',
           'canonical_sha256': '4d3b694fd55fc1d40b73b1de8c3b0d2e9f9dce9ca2fb8c1dda058dfb77a1e6c1'},
 'bootstrap': {'path': 'docs/evidence/production-host-bootstrap-runtime-fixes-20261108.json',
               'pin': {'bytes': 9499,
                       'sha256': 'f718c119b3d2b79c110c26eec51ee18655242d02e2acfa4de59f93e7d2b852cb'},
               'git_blob': '988f05aa16232fbe4114f5719486e94ff1a88e0c',
               'canonical_sha256': '60d40b557637f1b7da3785e45fde52db37b692e36d30d8156616f48c9e8f9176'}}
READBACK_REGISTRATION = {'path': 'docs/evidence/production-host-roots-runtime-fixes-nvme-readback-binding-20261108.json',
 'pin': {'bytes': 5555,
         'sha256': '24ee24034c7c41f9827583be950f393ba6beacc5725a21c00709860c1ef5ed00'},
 'git_blob': 'ca2f96f454b8c892b6e6b1e771668dc4afa4d032',
 'canonical_sha256': '2a684166b782bfc386bfa478b5d72373e943e63ddbe493d5f3ed1263c7a8d1ce'}
SUBSTITUTIONS = {
    'build/launch-nvme-host.py': SELF,
    'build/launch-host-state.py': 'build/launch-host-state-runtime-fixes.py',
    'build/launch-host-bootstrap.py': BOOTSTRAP,
    '-20261107.json': '-runtime-fixes-20261108.json',
    'host-roots-timesync-01': 'host-roots-timesync-runtime-fixes-01',
    'host-state-timesync-02': 'host-state-timesync-runtime-fixes-02',
    'host-bootstrap-timesync-01': 'host-bootstrap-timesync-runtime-fixes-01',
    'host-payload-timesync-01': 'host-payload-timesync-runtime-fixes-01',
    'host-sd-timesync-01': 'host-sd-timesync-runtime-fixes-01',
    'work/project/out/host-sd-timesync-01': 'work/project/out/host-sd-timesync-runtime-fixes-01',
    'fc8bbef6-9385-4ddb-90ba-e41eaccc035b': '360dfb39-f49f-487e-b307-0bb887c854c5',
}
COUNTS = dict.fromkeys(SUBSTITUTIONS, 1)
COUNTS['host-state-timesync-02'] = 2


def require(ok, message):
    if not ok:
        raise ValueError(message)


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid,
            info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class Template:
    """Hold every physical ancestor and bounded, nofollow historical template."""
    def __init__(self, path, expected_sha):
        self.path, self.expected_sha = Path(path), expected_sha
        self.fds, self.parents = [], []

    def __enter__(self):
        try:
            require(self.path.is_absolute() and str(self.path) == os.path.abspath(self.path),
                    'canonical absolute template path required')
            directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self.fds.append(directory)
            for name in self.path.parts[1:-1]:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                self.fds.append(child)
                self.parents.append((directory, name, child, os.fstat(child)))
                directory = child
            self.directory = directory
            self.fd = os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                              dir_fd=directory)
            self.fds.append(self.fd)
            self.before = os.fstat(self.fd)
            require(stat.S_ISREG(self.before.st_mode) and self.before.st_nlink == 1
                    and self.before.st_size <= 1024**2, 'bounded single-link template required')
            chunks, remaining = [], 1024**2 + 1
            while remaining:
                data = os.read(self.fd, remaining)
                if not data:
                    break
                chunks.append(data)
                remaining -= len(data)
            self.raw = b''.join(chunks)
            require(len(self.raw) == self.before.st_size
                    and hashlib.sha256(self.raw).hexdigest() == self.expected_sha,
                    'historical template SHA256 differs')
            self.pin = {'bytes': len(self.raw), 'sha256': self.expected_sha}
            self.unchanged()
            return self
        except BaseException:
            self.close()
            raise

    def unchanged(self):
        require(signature(self.before) == signature(os.fstat(self.fd)) ==
                signature(os.stat(self.path.name, dir_fd=self.directory, follow_symlinks=False)),
                'historical template changed')
        for parent, name, child, before in self.parents:
            identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid)
            require(identity(before) == identity(os.fstat(child)) ==
                    identity(os.stat(name, dir_fd=parent, follow_symlinks=False)),
                    'historical template ancestry changed')
        require(hashlib.sha256(os.pread(self.fd, 1024**2 + 1, 0)).hexdigest() == self.expected_sha,
                'historical template bytes changed')

    def close(self):
        for fd in reversed(self.fds):
            os.close(fd)
        self.fds = []

    def __exit__(self, kind, value, traceback):
        try:
            self.unchanged()
        finally:
            self.close()



def adapted_ast(raw):
    require(hashlib.sha256(raw).hexdigest() == TEMPLATE_SHA, 'historical template SHA256 differs')
    tree = ast.parse(raw, filename=TEMPLATE)
    counts = dict.fromkeys(SUBSTITUTIONS, 0)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and type(node.value) is str and node.value in SUBSTITUTIONS:
            old = node.value
            counts[old] += 1
            node.value = SUBSTITUTIONS[old]
    require(counts == COUNTS, 'historical AST constant anchors differ')
    return tree


def load_template(held):
    parent = types.ModuleType('registered_nvme_runtime_fixes_template')
    parent.__file__ = str(Path(os.path.abspath(__file__)))
    exec(compile(adapted_ast(held.raw), str(held.path), 'exec'), parent.__dict__)
    return parent


def adapt(parent, held, stack):
    """Bind the exact bootstrap adapter and retain every nested final check."""
    original_contract, original_plan = parent.contract, parent.plan
    original_records = parent.records
    installed_roots = None
    family = None
    adapter = None
    bootstrap_held = None
    registered_family = None
    state_adapter = None
    readback_binding = None
    holds = [held]

    def unchanged():
        for item in holds:
            item.unchanged()

    def contract(roots, project, contents):
        nonlocal installed_roots, family, adapter, bootstrap_held, registered_family, state_adapter, readback_binding
        require(contents[TEMPLATE] == held.raw, 'historical launcher outside committed closure')
        for registration in (*REGISTRATIONS.values(), READBACK_REGISTRATION):
            raw = contents[registration['path']]
            require({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} == registration['pin'],
                    'registered component evidence bytes differ')
            require(hashlib.sha256(json.dumps(json.loads(raw), sort_keys=True,
                    separators=(',', ':')).encode()).hexdigest() == registration['canonical_sha256'],
                    'registered parsed component evidence differs')
        require(hashlib.sha256(contents[BOOTSTRAP]).hexdigest() == BOOTSTRAP_SHA,
                'registered bootstrap adapter differs')
        if family is None:
            adapter_held = stack.enter_context(Template(project / BOOTSTRAP, BOOTSTRAP_SHA))
            require(adapter_held.raw == contents[BOOTSTRAP], 'bootstrap adapter outside committed closure')
            holds.append(adapter_held)
            adapter = types.ModuleType('registered_nvme_bootstrap_adapter')
            adapter.__file__ = str(project / BOOTSTRAP)
            exec(compile(adapter_held.raw, adapter.__file__, 'exec'), adapter.__dict__)
            bootstrap_held = stack.enter_context(adapter.Template(project / adapter.TEMPLATE, adapter.TEMPLATE_SHA))
            require(contents[adapter.TEMPLATE] == bootstrap_held.raw,
                    'historical bootstrap launcher outside committed closure')
            holds.append(bootstrap_held)
            family = adapter.load_template(bootstrap_held)
            adapter.adapt(family, bootstrap_held, stack)
            # The nested bootstrap holds/authenticates these exact state-adapter
            # bytes. Read its registration constants for the additional Git pin
            # check that normally runs only in its own standalone plan.
            require(hashlib.sha256(contents[adapter.STATE]).hexdigest() == adapter.STATE_SHA,
                    'registered state adapter differs')
            state_adapter = types.ModuleType('registered_nvme_state_registration')
            state_adapter.__file__ = str(project / adapter.STATE)
            exec(compile(contents[adapter.STATE], state_adapter.__file__, 'exec'), state_adapter.__dict__)
            original_module, original_execute = roots.module, roots.execute
            registered_names = (BOOTSTRAP, str(project / BOOTSTRAP))
            def module(name, data):
                if name in registered_names:
                    require(data == adapter_held.raw, 'registered bootstrap module data differs')
                    unchanged()
                    return family
                require(Path(name).name != Path(BOOTSTRAP).name, 'registered bootstrap module path differs')
                return original_module(name, data)
            def execute(*args, **kwargs):
                final = kwargs['final_check']
                def final_check():
                    final()
                    unchanged()
                kwargs['final_check'] = final_check
                return original_execute(*args, **kwargs)
            # Install first: the bootstrap contract wraps these hooks with its
            # STATE family checks, so each guard precedes final publication.
            roots.module, roots.execute = module, execute
            installed_roots = roots
        require(roots is installed_roots, 'registered root module changed')
        policy = original_contract(roots, project, contents)
        registered_family = json.loads(contents[REGISTRATIONS['bootstrap']['path']])['runtime_fixes_controller']
        state_template = types.SimpleNamespace(pin=roots.pin(contents[state_adapter.TEMPLATE]))
        require(registered_family == adapter.chain_binding(bootstrap_held, state_template),
                'registered bootstrap family provenance differs')
        state_producer = policy['parent']['state_policy']['producer']
        # Original artifact documentation and the full state proof registration
        # describe the same root bytes; neither record is rewritten or flattened.
        root_producer = policy['producers']['roots']
        for key in ('phase', 'output', 'producer_revision', 'producer_records',
                    'verification', 'images', 'policy', 'readback', 'slot_uuids'):
            require(root_producer[key] == state_producer[key],
                    'registered root artifact/state binding differs: ' + key)
        readback_binding = json.loads(contents[READBACK_REGISTRATION['path']])
        require(readback_binding['schema'] == 1 and
                readback_binding['root_evidence'] == dict(path=REGISTRATIONS['roots']['path'],
                                                         **REGISTRATIONS['roots']['pin']) and
                readback_binding['root_phase'] == root_producer['phase'] and
                readback_binding['root_receipt'] == root_producer['producer_records']['receipt.json'] and
                readback_binding['root_manifest'] == root_producer['verification']['manifest'] and
                readback_binding['receipt_contents'] == root_producer['readback'] and
                readback_binding['receipt_contents']['producer_receipt'] == readback_binding['root_receipt'] and
                readback_binding['receipt_contents']['root_manifest'] == readback_binding['root_manifest'] and
                readback_binding['hardware_acceptance'] is False and
                readback_binding['physical_storage_written'] is False,
                'registered root readback binding differs')
        unchanged()
        return policy

    def plan(tag, manifest, policy):
        require(bootstrap_held is not None, 'registered contract required before plan')
        for registration in (*REGISTRATIONS.values(), READBACK_REGISTRATION):
            record = manifest['files'][registration['path']]
            require({key: record[key] for key in ('bytes', 'sha256')} == registration['pin']
                    and record.get('git_blob') == registration['git_blob'],
                    'registered component evidence source closure differs')
        state_adapter.evidence_record(manifest['files'][state_adapter.EVIDENCE])
        result = original_plan(tag, manifest, policy)
        result['runtime_fixes_controller'] = {
            'historical_template': dict(held.pin),
            'registered_components': {name: dict(value) for name, value in REGISTRATIONS.items()},
            'root_readback_binding': copy.deepcopy(READBACK_REGISTRATION),
            'bootstrap_adapter': {'path': BOOTSTRAP, 'sha256': BOOTSTRAP_SHA},
            'bootstrap_family': {
                'source_evidence': REGISTRATIONS['bootstrap']['path'],
                'held_historical_template': dict(bootstrap_held.pin),
                'registered_runtime_fixes_controller': json.loads(json.dumps(registered_family))}}
        unchanged()
        return result
    def records(policy):
        require(readback_binding is not None, 'registered contract required before records')
        require(policy['producers']['roots']['readback'] == readback_binding['receipt_contents'],
                'registered root readback policy changed')
        # Historical records() needs addressing metadata absent from the root
        # artifact record. Supply it in a private copy; receipt authentication
        # continues to see the exact original producer/source policy.
        addressed = copy.deepcopy(policy)
        addressed['producers']['roots']['readback'].update(
            builder_output=readback_binding['builder_output'], receipt=readback_binding['receipt'])
        unchanged()
        return original_records(addressed)
    parent.contract, parent.plan, parent.records = contract, plan, records


def main(argv=None):
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    with contextlib.ExitStack() as stack:
        held = stack.enter_context(Template(Path(os.path.abspath(__file__)).parent / Path(TEMPLATE).name,
                                           TEMPLATE_SHA))
        parent = load_template(held)
        adapt(parent, held, stack)
        parent.main(argv)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Build fresh STATE for the single registered November 8 runtime-corrected root chain.

Same CLI as launch-host-state.py. No physical storage or hardware acceptance.
The SHA-pinned historical body retains every predicate and execution statement;
only the five registered string constants below change before compilation.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types

EVIDENCE = 'docs/evidence/production-host-roots-runtime-fixes-20261108.json'
EVIDENCE_PIN = {'bytes': 17141, 'sha256': '12c45544ac379a0ca11312f833f2e49333b0cf94dc55156a280a18044ba8003d'}
EVIDENCE_BLOB = '63ee6c9c17dbca0f434feaef010c7a0fa630f070'
CANONICAL_SHA = '2c04a75d5239224b80a721442e88ea872ccd63d18aaae841539d007697302971'
READER_SHA = '80e843d499bcc2f6d299d6022dc7edf83d73842405e95b38b0f32eee72dcda30'
FORBIDDEN_UUIDS = ('00fd936c-49fb-4fea-87c8-928b9c1599e0',
                   'fc8bbef6-9385-4ddb-90ba-e41eaccc035b',
                   'feaaee34-d782-4c18-bd04-1029540ab26a')


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
    """Only registered literal values change; counts fail closed on anchor drift."""
    require(hashlib.sha256(raw).hexdigest() == TEMPLATE_SHA, 'historical template SHA256 differs')
    tree = ast.parse(raw, filename=TEMPLATE)
    counts = dict.fromkeys(SUBSTITUTIONS, 0)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and type(node.value) is str and node.value in SUBSTITUTIONS:
            old = node.value
            counts[old] += 1
            node.value = SUBSTITUTIONS[old]
    require(all(count == 1 for count in counts.values()), 'historical AST constant anchors differ')
    return tree


def load_template(held):
    parent = types.ModuleType('registered_state_runtime_fixes_template')
    # The original body must hold and authenticate this executing adapter, while
    # the separately held historical template remains independently SHA-pinned.
    parent.__file__ = str(Path(os.path.abspath(__file__)))
    exec(compile(adapted_ast(held.raw), str(held.path), 'exec'), parent.__dict__)
    return parent


def chain_binding(template_pin):
    return {'historical_template': dict(template_pin), 'registered_parent': EVIDENCE,
            'registered_parent_pin': dict(EVIDENCE_PIN), 'registered_parent_canonical_sha256': CANONICAL_SHA}


def evidence_record(record):
    require({key: record[key] for key in ('bytes', 'sha256')} == EVIDENCE_PIN
            and record.get('git_blob') == EVIDENCE_BLOB, 'registered root evidence source closure differs')


def guard_uuid(parent):
    original = parent.state_uuid
    def state_uuid(value):
        result = original(value)
        require(result not in FORBIDDEN_UUIDS, 'state UUID belongs to an existing installation or candidate')
        return result
    parent.state_uuid = state_uuid

SELF = 'build/launch-host-state-runtime-fixes.py'
TEMPLATE = 'build/launch-host-state.py'
TEMPLATE_SHA = '68bdbb52c81c41cf97e11b1298c3c04ff64bf4671574550dd4f7ffaad9f405e1'
SUBSTITUTIONS = {
    'build/launch-host-state.py': SELF,
    'docs/evidence/production-host-roots-20261107.json': EVIDENCE,
    '6f2477be759b28829041f70e514dbdf678bb1a13': 'f40c151fb8957db41ede80fbd758880a5d901efe',
    'host-roots-timesync-01': 'host-roots-timesync-runtime-fixes-01',
    'work/project/out/host-payload-timesync-01': 'work/project/out/host-payload-timesync-runtime-fixes-01',
}


def adapt(parent, held):
    guard_uuid(parent)
    original_contract, original_plan = parent.contract, parent.plan
    def contract(roots, project, contents):
        require(contents[TEMPLATE] == held.raw, 'historical launcher outside committed closure')
        require(contents['build/verify-host-roots.py'] and
                hashlib.sha256(contents['build/verify-host-roots.py']).hexdigest() == READER_SHA,
                'historical root reader differs')
        raw = contents[EVIDENCE]
        require({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} == EVIDENCE_PIN,
                'registered root evidence bytes differ')
        require(hashlib.sha256(json.dumps(json.loads(raw), sort_keys=True,
                separators=(',', ':')).encode()).hexdigest() == CANONICAL_SHA,
                'registered parsed root evidence differs')
        policy = original_contract(roots, project, contents)
        held.unchanged()
        return policy
    def plan(options, manifest, policy):
        evidence_record(manifest['files'][EVIDENCE])
        result = original_plan(options, manifest, policy)
        result['runtime_fixes_controller'] = chain_binding(held.pin)
        held.unchanged()
        return result
    parent.contract, parent.plan = contract, plan
    # All unchanged root execution final checks run before this additional held
    # template check, and before the producer can publish a successful receipt.
    original_module = parent.module
    def module(name, data):
        result = original_module(name, data)
        if name.endswith(parent.ROOTS):
            original_execute = result.execute
            def execute(*args, **kwargs):
                final = kwargs['final_check']
                def final_check():
                    final()
                    held.unchanged()
                kwargs['final_check'] = final_check
                return original_execute(*args, **kwargs)
            result.execute = execute
        return result
    parent.module = module


def main(argv=None):
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    with Template(Path(os.path.abspath(__file__)).parent / Path(TEMPLATE).name, TEMPLATE_SHA) as held:
        parent = load_template(held)
        adapt(parent, held)
        parent.main(argv)


if __name__ == '__main__':
    main()

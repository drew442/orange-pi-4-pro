#!/usr/bin/env python3
"""Read back fresh STATE for the single registered November 8 runtime-corrected root chain.

Same CLI as verify-host-state.py. The SHA-pinned historical body retains every
predicate and execution statement; only the four registered string constants
below change before compilation. Verification is read-only software evidence.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types

EVIDENCE = 'docs/evidence/production-host-roots-runtime-fixes-state-binding-20261108.json'
EVIDENCE_PIN = {'bytes': 18102, 'sha256': '14371bdf3ee8b18c9f37be9203e09249dd388e8ac7354df221c9e3b49b3be5d2'}
EVIDENCE_BLOB = '1a5d452c3871fee25062c88b9c37f48875a7b79d'
CANONICAL_SHA = 'e4be6ff26a59d116056b3f5b777aea9c3b4668b3674861bb41b63b99f10e8db4'
READER_SHA = '80e843d499bcc2f6d299d6022dc7edf83d73842405e95b38b0f32eee72dcda30'
FORBIDDEN_UUIDS = ('00fd936c-49fb-4fea-87c8-928b9c1599e0',
                   'fc8bbef6-9385-4ddb-90ba-e41eaccc035b',
                   'feaaee34-d782-4c18-bd04-1029540ab26a',
                   'a55f9ea1-d255-4556-b578-9aa9e9d72726')


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


def parent_proof_schema(producer):
    """Reject incompatible registered parent proofs before any construction."""
    require(type(producer) is dict, 'registered parent proof producer must be an object')
    trees = producer.get('expected_trees')
    readback = producer.get('readback')
    require(type(trees) is dict and set(trees) == {'A', 'B'}
            and type(readback) is dict and type(readback.get('slots')) is dict
            and set(readback['slots']) == {'A', 'B'}, 'registered parent proof slots differ')
    def valid_pin(value):
        return (type(value) is dict and set(value) == {'bytes', 'sha256'}
                and type(value['bytes']) is int and value['bytes'] > 0
                and type(value['sha256']) is str and len(value['sha256']) == 64
                and all(c in '0123456789abcdef' for c in value['sha256']))
    for slot in ('A', 'B'):
        expected, actual = trees[slot], readback['slots'][slot]
        require(type(expected) is dict and set(expected) ==
                {'expected', 'stability', 'members', 'independent_image_readback'}
                and type(actual) is dict, 'registered parent proof record differs: ' + slot)
        require(expected['independent_image_readback'] is False
                and valid_pin(expected['expected']) and valid_pin(expected['stability'])
                and type(expected['members']) is int and expected['members'] > 0
                and type(actual.get('namespace_members')) is int
                and expected['members'] == actual['namespace_members']
                and expected['expected'] == actual.get('expected')
                and expected['stability'] == actual.get('stability'),
                'registered parent proof binding differs: ' + slot)


def guard_uuid(parent):
    original = parent.state_uuid
    def state_uuid(value):
        result = original(value)
        require(result not in FORBIDDEN_UUIDS, 'state UUID belongs to an existing installation or candidate')
        return result
    parent.state_uuid = state_uuid

SELF = 'build/verify-host-state-runtime-fixes.py'
TEMPLATE = 'build/verify-host-state.py'
TEMPLATE_SHA = 'f4375efa014dabac6e68d192ffa0627e8c0645a9b5b4675f8fd95b82504f70e6'
LAUNCHER = 'build/launch-host-state-runtime-fixes.py'
LAUNCHER_TEMPLATE_SHA = '68bdbb52c81c41cf97e11b1298c3c04ff64bf4671574550dd4f7ffaad9f405e1'
SUBSTITUTIONS = {
    '08cb041263fd4d24770cd80615745c46b9136940737eb6acbb07c1db39bcec8c': CANONICAL_SHA,
    'docs/evidence/production-host-roots-20261107.json': EVIDENCE,
    'host-roots-timesync-01': 'host-roots-timesync-runtime-fixes-01',
    'build/verify-host-state.py': SELF,
}


def adapt(parent, held):
    guard_uuid(parent)
    original_policy = parent.source_policy
    def source_policy(receipt):
        policy = original_policy(receipt)
        files = receipt['sources']['files']
        evidence_record(files[EVIDENCE])
        parent_proof_schema(policy['producer'])
        require({key: files[TEMPLATE][key] for key in ('bytes', 'sha256')} == held.pin,
                'historical state reader source closure differs')
        expected = chain_binding({'bytes': 28805, 'sha256': LAUNCHER_TEMPLATE_SHA})
        require(receipt.get('runtime_fixes_controller') == expected, 'runtime-fixes launcher chain binding differs')
        parent.R.validate_pin({key: files[LAUNCHER][key] for key in ('bytes', 'sha256')})
        require({key: files['build/launch-host-state.py'][key] for key in ('bytes', 'sha256')} ==
                expected['historical_template'], 'historical state launcher source closure differs')
        held.unchanged()
        return policy
    parent.source_policy = source_policy
    original_publish = parent.R.publish
    def publish(path, report):
        # The original body publishes inside readback's finally block. Validate
        # the held template before that publication, never after reporting success.
        try:
            held.unchanged()
        except BaseException as error:
            report.update(success=False, state='failed', error=repr(error))
            original_publish(path, report)
            raise
        report['runtime_fixes_controller'] = chain_binding(held.pin)
        return original_publish(path, report)
    parent.R.publish = publish


def main(argv=None):
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    with Template(Path(os.path.abspath(__file__)).parent / Path(TEMPLATE).name, TEMPLATE_SHA) as held:
        parent = load_template(held)
        adapt(parent, held)
        parent.main(argv)


if __name__ == '__main__':
    main()

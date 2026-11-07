#!/usr/bin/env python3
"""Compose only the registered repeat components; same historical file-only CLI.

The authenticated historical body changes only counted AST string constants.
Construction and host readback cannot establish physical hardware acceptance.
"""
import ast
import contextlib
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types

SELF = 'build/launch-nvme-host-repeat.py'
TEMPLATE = 'build/launch-nvme-host.py'
TEMPLATE_SHA = '5ae2db40d7f9e599fff34a2bc6dc6e14f927ffb8a47ecaac9dcb140b7d5a03d5'
BOOTSTRAP = 'build/launch-host-bootstrap-repeat.py'
BOOTSTRAP_SHA = 'f4716fa6e764f46aa63b2a9078de730f2d6035a540a91d05ef6d6235a0ebab4a'
REGISTRATIONS = {
    'roots': {'path': 'docs/evidence/production-host-roots-repeat-20261107.json', 'pin': {'bytes': 25380, 'sha256': '4595d68a5077a9b6ac79a1aac37f6b5a1c081cefd08b4bbb64af0a670284f673'}, 'git_blob': '988ddfff51156769ea02467a00dab17cfd638ae9', 'canonical_sha256': 'a92e4e387abb8bb6fef7ccbb956104f0dac531305278c60b72085cd922a3a6f3'},
    'state': {'path': 'docs/evidence/production-host-state-repeat-20261107.json', 'pin': {'bytes': 14184, 'sha256': '4a9d008acc45c864148320df537f63f36f9464ea2a47cc2c18d3680a885774e9'}, 'git_blob': '93567119a2d51d8e08a9d0cc6faeca507293d6e2', 'canonical_sha256': '9d3d8bd3972d5ee0f7c73c571ad7a45c9815b48029b059647bfc21bc5e0fc6a6'},
    'bootstrap': {'path': 'docs/evidence/production-host-bootstrap-repeat-20261107.json', 'pin': {'bytes': 8958, 'sha256': '2ad64fdfe3aa99731059e285ab4ce278cf30958d5b62100a147786e8c9761dac'}, 'git_blob': '588cd9b799ff564a814635303ac3f8ae77333297', 'canonical_sha256': 'd56d7f054240e34e51a09e4a01208407f23422e673ddf31a24373708b23e514c'},
}
SUBSTITUTIONS = {
    'build/launch-nvme-host.py': SELF,
    'build/launch-host-state.py': 'build/launch-host-state-repeat.py',
    'build/launch-host-bootstrap.py': BOOTSTRAP,
    '-20261107.json': '-repeat-20261107.json',
    'host-roots-timesync-01': 'host-roots-timesync-repeat-01',
    'host-state-timesync-02': 'host-state-timesync-repeat-01',
    'host-bootstrap-timesync-01': 'host-bootstrap-timesync-repeat-01',
    'host-payload-timesync-01': 'host-payload-timesync-repeat-01',
    'host-sd-timesync-01': 'host-sd-timesync-repeat-01',
    'work/project/out/host-sd-timesync-01': 'work/project/out/host-sd-timesync-repeat-01',
    'fc8bbef6-9385-4ddb-90ba-e41eaccc035b': 'feaaee34-d782-4c18-bd04-1029540ab26a',
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
    parent = types.ModuleType('registered_nvme_repeat_template')
    parent.__file__ = str(Path(os.path.abspath(__file__)))
    exec(compile(adapted_ast(held.raw), str(held.path), 'exec'), parent.__dict__)
    return parent


def adapt(parent, held, stack):
    """Bind the exact bootstrap adapter and retain every nested final check."""
    original_contract, original_plan = parent.contract, parent.plan
    installed_roots = None
    family = None
    adapter = None
    bootstrap_held = None
    registered_family = None
    holds = [held]

    def unchanged():
        for item in holds:
            item.unchanged()

    def contract(roots, project, contents):
        nonlocal installed_roots, family, adapter, bootstrap_held, registered_family
        require(contents[TEMPLATE] == held.raw, 'historical launcher outside committed closure')
        for registration in REGISTRATIONS.values():
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
        registered_family = json.loads(contents[REGISTRATIONS['bootstrap']['path']])['repeat_controller']
        require(registered_family['historical_template'] == bootstrap_held.pin
                and registered_family['registered_parent_pin'] == REGISTRATIONS['state']['pin']
                and registered_family['registered_parent_canonical_sha256'] == REGISTRATIONS['state']['canonical_sha256'],
                'registered bootstrap family provenance differs')
        unchanged()
        return policy

    def plan(tag, manifest, policy):
        require(bootstrap_held is not None, 'registered contract required before plan')
        for registration in REGISTRATIONS.values():
            record = manifest['files'][registration['path']]
            require({key: record[key] for key in ('bytes', 'sha256')} == registration['pin']
                    and record.get('git_blob') == registration['git_blob'],
                    'registered component evidence source closure differs')
        result = original_plan(tag, manifest, policy)
        result['repeat_controller'] = {
            'historical_template': dict(held.pin),
            'registered_components': {name: dict(value) for name, value in REGISTRATIONS.items()},
            'bootstrap_adapter': {'path': BOOTSTRAP, 'sha256': BOOTSTRAP_SHA},
            'bootstrap_family': {
                'source_evidence': REGISTRATIONS['bootstrap']['path'],
                'held_historical_template': dict(bootstrap_held.pin),
                'registered_repeat_controller': json.loads(json.dumps(registered_family))}}
        unchanged()
        return result
    parent.contract, parent.plan = contract, plan


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

#!/usr/bin/env python3
"""Build the registered repeat STATE's EL2 bootstrap; same historical CLI.

Only counted AST string constants change in the SHA-pinned historical body.
No device writes, firmware-profile change or hardware acceptance is authorized.
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

SELF = 'build/launch-host-bootstrap-repeat.py'
TEMPLATE = 'build/launch-host-bootstrap.py'
TEMPLATE_SHA = 'bcadf141568ccfa77d875ca1a335fb00792125c831cc4f02aef7b8a1bd6cdc9f'
STATE = 'build/launch-host-state-repeat.py'
STATE_SHA = '5e2998921932607614e51b5f0177917afed24b7a821be5ebad44c4475eeedace'
EVIDENCE = 'docs/evidence/production-host-state-repeat-20261107.json'
EVIDENCE_PIN = {'bytes': 14184, 'sha256': '4a9d008acc45c864148320df537f63f36f9464ea2a47cc2c18d3680a885774e9'}
EVIDENCE_BLOB = '93567119a2d51d8e08a9d0cc6faeca507293d6e2'
CANONICAL_SHA = '9d3d8bd3972d5ee0f7c73c571ad7a45c9815b48029b059647bfc21bc5e0fc6a6'
SUBSTITUTIONS = {
    'build/launch-host-bootstrap.py': SELF,
    'build/launch-host-state.py': STATE,
    'docs/evidence/production-host-state-20261107.json': EVIDENCE,
    'host-state-timesync-02': 'host-state-timesync-repeat-01',
    'work/project/out/host-state-timesync-02': 'work/project/out/host-state-timesync-repeat-01',
    'fc8bbef6-9385-4ddb-90ba-e41eaccc035b': 'feaaee34-d782-4c18-bd04-1029540ab26a',
    '3abeb7ba014eae05acebfd7af3b75dab7c4c58999b96d8c752163bb96a2ccd33':
        '01a03e382d90105863285c8625e5f9744cec8ffb535124afd6d9520c7c022c42',
    'e68f03531e3fab56deb5c883ccb7a5dca13baff8': '3221a1cf9f589576f875a3c742ac819998a140c0',
}
COUNTS = dict.fromkeys(SUBSTITUTIONS, 1)
COUNTS['work/project/out/host-state-timesync-02'] = 2


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
    parent = types.ModuleType('registered_bootstrap_repeat_template')
    parent.__file__ = str(Path(os.path.abspath(__file__)))
    exec(compile(adapted_ast(held.raw), str(held.path), 'exec'), parent.__dict__)
    return parent


def chain_binding(held, state_held):
    return {'historical_template': dict(held.pin), 'registered_parent': EVIDENCE,
            'registered_parent_pin': dict(EVIDENCE_PIN),
            'registered_parent_canonical_sha256': CANONICAL_SHA,
            'state_adapter': {'path': STATE, 'sha256': STATE_SHA},
            'state_historical_template': dict(state_held.pin)}


def adapt(parent, held, stack):
    """Bind the exact registered state family without changing its predicates."""
    original_contract, original_plan = parent.contract, parent.plan
    state_held = None
    installed_roots = None
    family = None
    holds = [held]

    def unchanged():
        for item in holds:
            item.unchanged()

    def contract(roots, project, contents):
        nonlocal state_held, installed_roots, family
        require(EVIDENCE_PIN is not None and EVIDENCE_BLOB is not None and CANONICAL_SHA is not None,
                'repeat state registration pins pending')
        require(contents[TEMPLATE] == held.raw, 'historical launcher outside committed closure')
        raw = contents[EVIDENCE]
        require({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} == EVIDENCE_PIN,
                'registered state evidence bytes differ')
        require(hashlib.sha256(json.dumps(json.loads(raw), sort_keys=True,
                separators=(',', ':')).encode()).hexdigest() == CANONICAL_SHA,
                'registered parsed state evidence differs')
        require(hashlib.sha256(contents[STATE]).hexdigest() == STATE_SHA,
                'registered state adapter differs')
        if family is None:
            adapter_held = stack.enter_context(Template(project / STATE, STATE_SHA))
            require(adapter_held.raw == contents[STATE], 'state adapter outside committed closure')
            holds.append(adapter_held)
            adapter = types.ModuleType('registered_bootstrap_state_adapter')
            adapter.__file__ = str(project / STATE)
            exec(compile(adapter_held.raw, adapter.__file__, 'exec'), adapter.__dict__)
            state_held = stack.enter_context(adapter.Template(project / adapter.TEMPLATE, adapter.TEMPLATE_SHA))
            require(contents[adapter.TEMPLATE] == state_held.raw,
                    'historical state launcher outside committed closure')
            holds.append(state_held)
            family = adapter.load_template(state_held)
            adapter.adapt(family, state_held)
            original_module, original_execute = roots.module, roots.execute
            registered_names = (STATE, str(project / STATE))
            def module(name, data):
                if name in registered_names:
                    require(data == adapter_held.raw, 'registered state module data differs')
                    unchanged()
                    return family
                require(Path(name).name != Path(STATE).name, 'registered state module path differs')
                return original_module(name, data)
            def execute(*args, **kwargs):
                final = kwargs['final_check']
                def final_check():
                    final()
                    unchanged()
                kwargs['final_check'] = final_check
                return original_execute(*args, **kwargs)
            roots.module, roots.execute = module, execute
            installed_roots = roots
        require(roots is installed_roots, 'registered root module changed')
        policy = original_contract(roots, project, contents)
        unchanged()
        return policy

    def plan(options, manifest, policy):
        require(state_held is not None, 'registered contract required before plan')
        record = manifest['files'][EVIDENCE]
        require({key: record[key] for key in ('bytes', 'sha256')} == EVIDENCE_PIN
                and record.get('git_blob') == EVIDENCE_BLOB,
                'registered state evidence source closure differs')
        result = original_plan(options, manifest, policy)
        result['repeat_controller'] = chain_binding(held, state_held)
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

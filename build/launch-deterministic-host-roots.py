#!/usr/bin/env python3
"""Create normalized regular-file A/B roots through the SHA-pinned root launcher.

Same CLI and isolation/admission policy as launch-host-roots.py; invoke -I -B.
This build-only adapter never changes installed maintenance sources or markers.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import types

SELF = 'build/launch-deterministic-host-roots.py'
TEMPLATE = 'build/launch-host-roots.py'
TEMPLATE_SHA = 'eb55e7b0cd51f3deeede47777adda8255de9d48322eb568c502f9a52a797e4ff'
HELPER = 'build/deterministic-root-image.py'
EPOCH = 1793750400
PROFILE = 'deterministic-root-v1'
OUTER_HOST_LOCK = 'configs/builder-host-packages-20261112.lock.tsv'
OUTER_HOST_PIN = {'bytes': 32550, 'sha256': 'f779568a3e4e82afc488c1b99ccad0a88f2d96e74efe3b00172927c7539f4ef3'}
FROZEN_REPLAY_LOCK = 'configs/builder-replay-packages-20261106.lock.tsv'
FROZEN_REPLAY_PIN = {'bytes': 32546, 'sha256': '92662b76002eef6d90d58505b724eedbc474ded367b0011a8b71afbe50317425'}
# Observed November 11 updates on the outer host only. The frozen replay and
# historical userspace archive closure continue to use FROZEN_REPLAY_LOCK.
OUTER_VERSION_CHANGES = {
    'libperl5.34:amd64': ('5.34.0-3ubuntu1.9', '5.34.0-3ubuntu1.10'),
    'linux-libc-dev:amd64': ('5.15.0-202.212', '5.15.0-203.213'),
    'linux-tools-common': ('5.15.0-202.212', '5.15.0-203.213'),
    'perl': ('5.34.0-3ubuntu1.9', '5.34.0-3ubuntu1.10'),
    'perl-base': ('5.34.0-3ubuntu1.9', '5.34.0-3ubuntu1.10'),
    'perl-modules-5.34': ('5.34.0-3ubuntu1.9', '5.34.0-3ubuntu1.10'),
    'python3-pyasn1': ('0.4.8-1ubuntu0.3', '0.4.8-1ubuntu0.4'),
}


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
    matches = [node for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and type(node.value) is str and node.value == TEMPLATE]
    require(len(matches) == 1, 'historical AST SELF anchors differ')
    matches[0].value = SELF
    return tree


def load_template(held):
    parent = types.ModuleType('deterministic_host_roots_template')
    parent.__file__ = str(Path(os.path.abspath(__file__)))
    exec(compile(adapted_ast(held.raw), str(held.path), 'exec'), parent.__dict__)
    return parent


def replace_once(program, old, new):
    require(program.count(old) == 1, 'historical payload program anchor differs: ' + repr(old))
    return program.replace(old, new)


def normalization_binding(helper_pin, template_pin):
    return {'profile': PROFILE, 'epoch': EPOCH, 'helper': dict(helper_pin),
            'historical_template': dict(template_pin)}


def package_records(raw):
    require(type(raw) is bytes, 'package lock bytes required')
    records = {}
    for line in raw.decode('ascii', 'strict').splitlines():
        fields = line.split('\t')
        require(len(fields) == 3, 'malformed package lock tuple')
        name, version, architecture = fields
        require(re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?::[a-z0-9][a-z0-9-]*)?', name)
                and re.fullmatch(r'[0-9][A-Za-z0-9.+:~\-]*', version)
                and re.fullmatch(r'[a-z0-9][a-z0-9-]*', architecture)
                and name not in records, 'invalid/duplicate package lock tuple')
        records[name] = (version, architecture)
    require(len(records) == 842, 'package lock count differs')
    return records


def inventory_binding(parent, contents):
    records = {}
    for path, expected in ((OUTER_HOST_LOCK, OUTER_HOST_PIN),
                           (FROZEN_REPLAY_LOCK, FROZEN_REPLAY_PIN)):
        require(type(contents.get(path)) is bytes, 'package lock missing from committed closure: ' + path)
        require(parent.pin(contents[path]) == expected, 'package lock pin differs: ' + path)
        records[path] = package_records(contents[path])
    outer, frozen = records[OUTER_HOST_LOCK], records[FROZEN_REPLAY_LOCK]
    require({name: value[1] for name, value in outer.items()} ==
            {name: value[1] for name, value in frozen.items()}, 'package lock identities/architectures differ')
    changes = {name: (frozen[name][0], outer[name][0]) for name in outer
               if frozen[name][0] != outer[name][0]}
    require(changes == OUTER_VERSION_CHANGES, 'outer host version changes differ')
    return {'outer_host': {'path': OUTER_HOST_LOCK, **OUTER_HOST_PIN},
            'frozen_replay': {'path': FROZEN_REPLAY_LOCK, **FROZEN_REPLAY_PIN},
            'version_changes': {name: {'frozen_replay': old, 'outer_host': new}
                                for name, (old, new) in sorted(changes.items())}}


def normalized_program(original, tag, helper_pin):
    program = original(tag).decode('utf-8', 'strict')
    initialization = '''normalizer_path=p/HELPER
normalizer_data=normalizer_path.read_bytes()
normalizer_pin=HELPER_PIN
assert {'bytes':len(normalizer_data),'sha256':hashlib.sha256(normalizer_data).hexdigest()}==normalizer_pin,'normalizer helper differs from committed closure'
normalizer_spec=importlib.util.spec_from_file_location('deterministic_root_image',normalizer_path)
normalizer=importlib.util.module_from_spec(normalizer_spec)
exec(compile(normalizer_data,str(normalizer_path),'exec'),normalizer.__dict__)
normalizer_reports={}
'''.replace('HELPER_PIN', repr(helper_pin)).replace('HELPER', repr(HELPER))
    program = replace_once(program, 'spec.loader.exec_module(payload)\n',
                           'spec.loader.exec_module(payload)\n' + initialization)
    program = replace_once(program, ' before=snapshot(root)\n',
                           f' normalizer.prepare_tree(root,{EPOCH})\n before=snapshot(root)\n')
    program = replace_once(program, ' original(root,output,slot,size)\n',
                           f' normalizer_reports[slot]=normalizer.make_image(root,output,slot,size,epoch={EPOCH})\n')
    # This runs only after the unchanged payload build and its final manifest
    # writer. Installed root-payload.json and its maintenance closure stay intact.
    program += '''assert set(normalizer_reports)=={'A','B'}
manifest_path=p/OUTPUT/'manifest.json'
manifest=json.loads(manifest_path.read_bytes())
manifest['filesystem_normalization']={'profile':PROFILE,'epoch':EPOCH,'helper':normalizer_pin,'slots':normalizer_reports}
manifest['reproducibility']='normalized creator; actual repeat proof pending'
payload.write_json(manifest_path,manifest)
'''.replace('OUTPUT', repr('out/host-payload-' + tag)).replace('PROFILE', repr(PROFILE)).replace('EPOCH', str(EPOCH))
    compile(program, '<normalized-root-payload>', 'exec')
    return program.encode('utf-8')


def adapt(parent, held):
    require(parent.HOST_LOCK == FROZEN_REPLAY_LOCK, 'historical host lock anchor differs')
    parent.HOST_LOCK = OUTER_HOST_LOCK
    original_contract, original_plan = parent.contract, parent.plan
    original_payload, original_execute = parent.payload_program, parent.execute
    binding = {}
    inventories = {}

    def contract(project, contents):
        require(contents.get(TEMPLATE) == held.raw, 'historical launcher outside committed closure')
        require(type(contents.get(HELPER)) is bytes and contents[HELPER],
                'normalizer helper missing from committed closure')
        helper_pin = parent.pin(contents[HELPER])
        inventory = inventory_binding(parent, contents)
        policy = original_contract(project, contents)
        binding.clear()
        binding.update(normalization_binding(helper_pin, held.pin))
        inventories.clear()
        inventories.update(inventory)
        held.unchanged()
        return policy

    def plan(tag, manifest, policy, diagnostic, userspace):
        require(binding, 'normalizer contract required before plan')
        files = manifest.get('files', {})
        for name, expected in ((TEMPLATE, held.pin), (HELPER, binding['helper']),
                               (OUTER_HOST_LOCK, OUTER_HOST_PIN),
                               (FROZEN_REPLAY_LOCK, FROZEN_REPLAY_PIN)):
            require(type(files.get(name)) is dict and
                    {key: files[name].get(key) for key in ('bytes', 'sha256')} == expected,
                    'normalizer source closure differs: ' + name)
        result = original_plan(tag, manifest, policy, diagnostic, userspace)
        result['filesystem_normalization'] = normalization_binding(binding['helper'], held.pin)
        result['builder_package_inventory'] = json.loads(json.dumps(inventories))
        held.unchanged()
        return result

    def payload_program(tag):
        require(binding, 'normalizer contract required before payload generation')
        return normalized_program(original_payload, tag, binding['helper'])

    def execute(*args, **kwargs):
        final = kwargs.get('final_check')
        def final_check():
            if final is not None:
                final()
            held.unchanged()
        kwargs['final_check'] = final_check
        return original_execute(*args, **kwargs)

    parent.contract, parent.plan = contract, plan
    parent.payload_program, parent.execute = payload_program, execute


def main(argv=None):
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    with Template(Path(os.path.abspath(__file__)).parent / Path(TEMPLATE).name, TEMPLATE_SHA) as held:
        parent = load_template(held)
        adapt(parent, held)
        parent.main(argv)


if __name__ == '__main__':
    main()



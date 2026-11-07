#!/usr/bin/env python3
"""Construct the registered second state's NVMe-first firmware transport SD.

Compose exact authenticated adapters; regular-file construction only. Physical
boot, recovery, zero routine SD writes and actual KVM acceptance remain pending.
"""
import contextlib
import hashlib
import os
from pathlib import Path
import stat
import sys
import types

SELF = 'build/launch-nvme-test-sd-repeat.py'
REPEAT = 'build/launch-host-bootstrap-repeat.py'
REPEAT_SHA = 'f4716fa6e764f46aa63b2a9078de730f2d6035a540a91d05ef6d6235a0ebab4a'
FIRMWARE = 'build/launch-nvme-test-sd.py'
FIRMWARE_SHA = '2d7ce2feed7d3cf303ee75bc03bf514a6da22f97974d0a9aca7adacdec3e3d50'


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



def load_adapter(held, name, filename):
    module = types.ModuleType(name)
    module.__file__ = str(filename)
    exec(compile(held.raw, str(held.path), 'exec'), module.__dict__)
    return module


def compose(stack, path):
    """Hold both unchanged adapters and the historical bootstrap until exit."""
    path = Path(path)
    project = path.parent.parent
    repeat_held = stack.enter_context(Template(project / REPEAT, REPEAT_SHA))
    firmware_held = stack.enter_context(Template(project / FIRMWARE, FIRMWARE_SHA))
    repeat = load_adapter(repeat_held, 'transport_repeat_bootstrap_adapter', path)
    firmware = load_adapter(firmware_held, 'transport_repeat_firmware_adapter', project / FIRMWARE)
    require(repeat.SELF == REPEAT and repeat.SUBSTITUTIONS[repeat.TEMPLATE] == REPEAT
            and repeat.COUNTS[repeat.TEMPLATE] == 1,
            'registered executing-controller identity anchor differs')
    repeat.SUBSTITUTIONS[repeat.TEMPLATE] = SELF
    held = stack.enter_context(Template(project / repeat.TEMPLATE, repeat.TEMPLATE_SHA))
    parent = repeat.load_template(held)
    require(parent.SELF == SELF and parent.__file__ == str(path),
            'registered executing-controller identity differs')
    repeat.adapt(parent, held, stack)
    firmware.adapt(parent)
    original_contract, original_plan = parent.contract, parent.plan
    holds = (repeat_held, firmware_held, held)
    installed_roots = None

    def unchanged():
        for item in holds:
            item.unchanged()

    def contract(roots, selected_project, contents):
        nonlocal installed_roots
        require(selected_project == project, 'registered project path differs')
        require(contents[REPEAT] == repeat_held.raw and contents[FIRMWARE] == firmware_held.raw,
                'registered adapters outside committed closure')
        unchanged()
        if installed_roots is None:
            original_execute = roots.execute
            def execute(*args, **kwargs):
                final = kwargs['final_check']
                def final_check():
                    final()
                    unchanged()
                kwargs['final_check'] = final_check
                return original_execute(*args, **kwargs)
            roots.execute = execute
            installed_roots = roots
        require(roots is installed_roots, 'registered root module changed')
        policy = original_contract(roots, selected_project, contents)
        unchanged()
        return policy

    def plan(options, manifest, policy):
        result = original_plan(options, manifest, policy)
        result['firmware_repeat_controller'] = {
            'bootstrap_adapter': {'path': REPEAT, **repeat_held.pin},
            'firmware_adapter': {'path': FIRMWARE, **firmware_held.pin},
            'historical_template': {'path': repeat.TEMPLATE, **held.pin}}
        unchanged()
        return result

    parent.contract, parent.plan = contract, plan
    return parent


def main(argv=None):
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    with contextlib.ExitStack() as stack:
        parent = compose(stack, Path(os.path.abspath(__file__)))
        parent.main(argv)


if __name__ == '__main__':
    main()

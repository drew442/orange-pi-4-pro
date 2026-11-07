#!/usr/bin/env python3
"""Read the single registered repeat NVMe gzip stream from binary stdin.

Same CLI and bounded independent inspection as the pinned historical reader.
Only seven counted AST string literals change. This establishes file-artifact
verification only; physical boot, SD writes and KVM acceptance remain pending.
"""
import ast
import hashlib
import os
from pathlib import Path
import stat
import sys
import types
import zlib

MIB = 1024**2
SELF = 'build/verify-nvme-repeat-stream.py'
TEMPLATE = 'build/verify-nvme-stream.py'
TEMPLATE_SHA = '02b04bce52597d9a4ca00f19f4c520fe357e3ba437de43aa1bef38978474684d'
SUBSTITUTIONS = {
    'fc8bbef6-9385-4ddb-90ba-e41eaccc035b': 'feaaee34-d782-4c18-bd04-1029540ab26a',
    '6f46e080b9557dd6d720bb0b07d17b19044a7946': '339eef1ffcd1370dffc65fec81861f7081cb2daf',
    '/var/lib/a733-clean-replay-20261106/nvme-timesync-03': '/var/lib/a733-clean-replay-20261106/nvme-timesync-repeat-01',
    'work/project/out/nvme-image-timesync-03': 'work/project/out/nvme-image-timesync-repeat-01',
    '08cb041263fd4d24770cd80615745c46b9136940737eb6acbb07c1db39bcec8c': 'a92e4e387abb8bb6fef7ccbb956104f0dac531305278c60b72085cd922a3a6f3',
    'fee4c46acb4db8838bbcc4b03f935701032ea093206a53ee39671cd840c6546e': '9d3d8bd3972d5ee0f7c73c571ad7a45c9815b48029b059647bfc21bc5e0fc6a6',
    'c5799bb06995313b6c90f99d4aedcc7b802095a0742cdd19a08d48ba3e3fec98': 'd56d7f054240e34e51a09e4a01208407f23422e673ddf31a24373708b23e514c',
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def pin(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}



def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid,
            info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class Template:
    """Hold the authenticated physical sibling throughout stream verification."""
    def __init__(self, path, expected=None):
        require('..' not in Path(path).parts, 'parent traversal in metadata path')
        self.path = Path(os.path.abspath(path))
        require(not any(self.path == root or root in self.path.parents
                        for root in map(Path, ('/dev', '/proc', '/sys'))),
                'device and pseudo-filesystem metadata paths are forbidden')
        self.fds = []
        try:
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
            self.fds.append(fd)
            self.links = []
            for component in self.path.parts[1:-1]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                self.links.append((fd, component, os.fstat(child), child))
                self.fds.append(child)
                fd = child
            self.parent = fd
            path_fd = os.open(self.path.name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            self.fds.append(path_fd)
            named = os.fstat(path_fd)
            require(stat.S_ISREG(named.st_mode) and named.st_nlink == 1 and
                    0 < named.st_size <= 4 * MIB, 'bounded single regular metadata file required')
            # O_PATH performs no device data open. Reopen only the checked inode,
            # even if its directory entry was replaced after the type check.
            self.fd = os.open('/proc/self/fd/' + str(path_fd), os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            self.fds.append(self.fd)
            self.before = os.fstat(self.fd)
            require(stat.S_ISREG(self.before.st_mode) and self.before.st_nlink == 1 and
                    self.before.st_size <= 4 * MIB, 'bounded single regular metadata file required')
            require(signature(named) == signature(self.before), 'metadata leaf changed before open')
            self.data = os.pread(self.fd, self.before.st_size + 1, 0)
            self.pin = pin(self.data)
            require(len(self.data) == self.before.st_size, 'metadata short read')
            if expected is not None:
                require(self.pin == expected, 'metadata pin mismatch: ' + str(self.path))
            self.unchanged()
        except BaseException:
            self.close()
            raise

    def unchanged(self):
        for parent, name, before, child in self.links:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            require(stat.S_ISDIR(current.st_mode) and
                    (current.st_dev, current.st_ino) == (before.st_dev, before.st_ino) ==
                    (os.fstat(child).st_dev, os.fstat(child).st_ino), 'metadata ancestry changed')
        current = os.stat(self.path.name, dir_fd=self.parent, follow_symlinks=False)
        require(signature(current) == signature(self.before) == signature(os.fstat(self.fd)),
                'metadata identity changed: ' + str(self.path))
        require(pin(os.pread(self.fd, self.before.st_size + 1, 0)) == self.pin,
                'metadata bytes changed: ' + str(self.path))
        require(signature(os.fstat(self.fd)) == signature(self.before), 'metadata changed during hash')

    def close(self):
        for fd in reversed(self.fds):
            os.close(fd)
        self.fds = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
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
    require(all(count == 1 for count in counts.values()), 'historical AST constant anchors differ')
    return tree


def load_template(held):
    parent = types.ModuleType('registered_nvme_repeat_stream_template')
    parent.__file__ = os.path.abspath(__file__)
    exec(compile(adapted_ast(held.data), str(held.path), 'exec'), parent.__dict__)
    return parent


def adapt(parent, held):
    original_decode = parent.decode
    def decode(stream, compressed, raw, capture, fence):
        def combined_fence():
            fence()
            held.unchanged()
        return original_decode(stream, compressed, raw, capture, combined_fence)
    parent.decode = decode
    original_verify = parent.verify
    def verify(*args, **kwargs):
        held.unchanged()
        report = original_verify(*args, **kwargs)
        held.unchanged()
        report['repeat_stream_reader'] = {
            'historical_template': {'path': TEMPLATE, **held.pin},
            'registered_literals': dict(SUBSTITUTIONS),
            'historical_template_identity_unchanged': True,
        }
        return report
    parent.verify = verify


def main(argv=None):
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    path = Path(os.path.abspath(__file__)).parent / Path(TEMPLATE).name
    with Template(path) as held:
        require(held.pin['sha256'] == TEMPLATE_SHA, 'historical template SHA256 differs')
        parent = load_template(held)
        adapt(parent, held)
        parent.main(argv)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, zlib.error) as error:
        sys.exit('NVMe repeat stream verification failed: ' + str(error))

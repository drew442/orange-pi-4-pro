#!/usr/bin/env python3
"""Build-only deterministic ext4 creator for trusted, quiescent staging trees.

Reproduction requires the same pinned e2fsprogs/mke2fs configuration. Source
preparation is explicit and must precede the caller's expected-tree snapshot.
No mounts or physical-device arguments are supported.
"""
import os
from pathlib import Path
import re
import selectors
import stat
import struct
import subprocess
import tempfile
import time
import uuid

EPOCH = 1793750400
SLOT_BYTES = 4294967296
SLOT_UUIDS = {'A': 'a7330000-0000-4000-8000-00000000000a',
              'B': 'a7330000-0000-4000-8000-00000000000b'}
MAX_INODES = 100000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def epoch_value(epoch):
    require(type(epoch) is int and 0 < epoch < 2**31, 'whole positive signed 32-bit epoch required')
    return epoch


def physical(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'absolute physical path required')
    require(path != Path('/') and path.parts[1] not in ('dev', 'proc', 'sys'),
            'device/proc/sys/root paths refused')
    return path


def directory(path):
    """Open every ancestor without following links or accepting shared writes."""
    path = physical(path)
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in ('', *path.parts[1:]):
            if name:
                next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            info = os.fstat(fd)
            require(info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022,
                    'untrusted owner or writable directory ancestor')
        return fd
    except BaseException:
        os.close(fd)
        raise


def tree_entries(root):
    """Do not dereference source symlinks, including links to devices."""
    device = root.lstat().st_dev
    entries = []
    def walk_error(error):
        raise error
    for base, dirs, files in os.walk(root, topdown=False, followlinks=False, onerror=walk_error):
        for name in sorted(files + dirs):
            path = Path(base) / name
            info = path.lstat()
            require(info.st_dev == device, 'source mount boundary refused')
            require(stat.S_IFMT(info.st_mode) in (stat.S_IFDIR, stat.S_IFREG,
                    stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO),
                    'unsupported source inode type')
            entries.append(path)
            require(len(entries) < MAX_INODES, 'source namespace exceeds bound')
    return entries + [root]


def prepare_tree(root, epoch=EPOCH):
    """Set only atime/mtime, bottom-up, on a private quiescent staging tree."""
    root, epoch = physical(root), epoch_value(epoch)
    fd = directory(root)
    try:
        named, opened = root.lstat(), os.fstat(fd)
        require((named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino),
                'source identity changed')
        for path in tree_entries(root):
            os.utime(path, ns=(epoch * 10**9, epoch * 10**9), follow_symlinks=False)
    finally:
        os.close(fd)


def run(args, *, fds, epoch, limit=64 * 1024**2):
    env = dict(os.environ, PATH='/usr/sbin:/usr/bin:/sbin:/bin', LC_ALL='C',
               TZ='UTC', E2FSPROGS_FAKE_TIME=str(epoch))
    # Preserve the caller's established fakeroot environment when present;
    # this helper neither introduces an interposer nor changes its settings.
    with subprocess.Popen(args, env=env, pass_fds=tuple(fds),
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE) as process:
        buffers = {process.stdout: bytearray(), process.stderr: bytearray()}
        deadline = time.monotonic() + 300
        try:
            with selectors.DefaultSelector() as selector:
                for stream in buffers:
                    selector.register(stream, selectors.EVENT_READ)
                while selector.get_map():
                    require(time.monotonic() < deadline, 'filesystem tool timeout')
                    for key, _ in selector.select(1):
                        block = os.read(key.fd, 65536)
                        if block:
                            buffers[key.fileobj].extend(block)
                            require(sum(map(len, buffers.values())) <= limit, 'tool output exceeds bound')
                        else:
                            selector.unregister(key.fileobj)
            code = process.wait(timeout=max(.01, deadline - time.monotonic()))
            stdout, stderr = (bytes(buffers[s]) for s in (process.stdout, process.stderr))
            require(code == 0, f'{args[0]} failed ({code}): {stderr[-4096:]!r}')
            return stdout, stderr
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def batch(fd, commands, epoch, work, write=False):
    require(commands and len(commands) <= 2048 and all(re.fullmatch(
        r'(?:ls -p <\d+>|stat <\d+>|set_inode_field <\d+> [a-z_]+ (?:@?\d+))', c)
        for c in commands), 'unsafe debugfs batch')
    with tempfile.TemporaryFile(dir=work) as script:
        script.write(('\n'.join(commands) + '\n').encode('ascii'))
        script.flush()
        args = ['debugfs', *(['-w'] if write else []), '-f',
                f'/proc/self/fd/{script.fileno()}', f'/proc/self/fd/{fd}']
        stdout, stderr = run(args, fds=(fd, script.fileno()), epoch=epoch)
    require(re.fullmatch(rb'debugfs [^\r\n]+\n', stderr), 'debugfs diagnostic/error: ' + repr(stderr[:1000]))
    pieces = re.split(r'^debugfs: (.*)\n', stdout.decode('utf-8', 'strict'), flags=re.M)
    require(pieces[0] == '' and pieces[1::2] == commands, 'unexpected debugfs framing')
    if write:
        require(all(not p for p in pieces[2::2]), 'unexpected debugfs mutation output')
    return pieces[2::2]


def namespace(fd, epoch, work):
    pending, seen, inodes = [(2, 2)], set(), {2}
    while pending:
        chunk, pending = pending[:128], pending[128:]
        outputs = batch(fd, [f'ls -p <{ino}>' for ino, _ in chunk], epoch, work)
        for (ino, parent), output in zip(chunk, outputs):
            require(ino not in seen, 'directory alias/cycle')
            seen.add(ino)
            rows = {}
            for line in output.splitlines():
                if not line or line == '/0/000000/0/0//0/':
                    continue
                match = re.fullmatch(r'/(\d+)/([0-7]{6})/(\d+)/(\d+)/([^/]+)/([0-9]*)/', line)
                require(match is not None, 'unparsed directory entry')
                number, mode, _, _, name, _ = match.groups()
                number, mode = int(number), int(mode, 8)
                require(number > 0 and name not in rows and all(ord(c) >= 32 and ord(c) != 127 for c in name), 'invalid directory entry')
                rows[name] = number
                inodes.add(number)
                if stat.S_ISDIR(mode) and name not in ('.', '..'):
                    pending.append((number, ino))
            require(rows.get('.') == ino and rows.get('..') == parent, 'invalid directory topology')
            require(len(inodes) <= MAX_INODES and len(pending) <= MAX_INODES, 'image namespace exceeds bound')
    return sorted(inodes)


def normalize_inodes(fd, epoch, work):
    inodes = namespace(fd, epoch, work)
    fields = [(field, '@' + str(epoch)) for field in ('atime', 'mtime', 'ctime', 'crtime')]
    fields += [(field + '_extra', '0') for field in ('atime', 'mtime', 'ctime', 'crtime')]
    for start in range(0, len(inodes), 128):
        chunk = inodes[start:start + 128]
        batch(fd, [f'set_inode_field <{ino}> {field} {value}' for ino in chunk
                   for field, value in fields], epoch, work, write=True)
        outputs = batch(fd, [f'stat <{ino}>' for ino in chunk], epoch, work)
        for ino, output in zip(chunk, outputs):
            require(re.search(rf'^Inode: {ino}\s+Type: ', output, re.M) is not None,
                    'inode stat identity mismatch')
            for field in ('atime', 'mtime', 'ctime', 'crtime'):
                require(len(re.findall(rf'^ ?{field}: 0x{epoch:08x}:00000000 -- .*$', output, re.M)) == 1,
                        'inode time normalization readback mismatch')
    return len(inodes)


def check_superblock(fd, size, slot, epoch):
    sb = os.pread(fd, 1024, 1024)
    require(len(sb) == 1024, 'short superblock')
    word = lambda offset: struct.unpack_from('<I', sb, offset)[0]
    expected_uuid = uuid.UUID(SLOT_UUIDS[slot]).bytes
    require(sb[56:58] == b'\x53\xef' and sb[104:120] == expected_uuid
            and sb[120:136].rstrip(b'\0') == f'a733-root-{slot}'.encode('ascii')
            and not word(92) & 4 and word(100) & 0x1000 and word(24) == 2
            and (word(4) | word(336) << 32) * 4096 == size
            and sb[236:252] == expected_uuid
            and word(44) == 0 and all(word(i) == epoch for i in (48, 64, 264))
            and os.fstat(fd).st_size == size, 'raw immutable filesystem metadata mismatch')


def make_image(root, output, slot, size=SLOT_BYTES, epoch=EPOCH):
    """Create a new regular image; never modify source times implicitly."""
    root, output, epoch = physical(root), physical(output), epoch_value(epoch)
    require(slot in SLOT_UUIDS, 'unknown root slot')
    require(type(size) is int and 32 * 1024**2 <= size <= SLOT_BYTES and size % 4096 == 0,
            'image size must be 4KiB aligned within 32MiB..4GiB')
    require(output != root and root not in output.parents, 'output inside source refused')
    source_fd, parent_fd, fd = directory(root), None, None
    try:
        entries = tree_entries(root)
        require(all(path.lstat().st_mtime_ns == epoch * 10**9 for path in entries),
                'source tree must be explicitly prepared before snapshot/creation')
        parent_fd = directory(output.parent)
        fd = os.open(output.name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                     0o600, dir_fd=parent_fd)
        require(stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_nlink == 1,
                'new single regular output required')
        os.ftruncate(fd, size)
        image = f'/proc/self/fd/{fd}'
        root_info = os.fstat(source_fd)
        run(['mke2fs', '-q', '-t', 'ext4', '-F', '-b', '4096', '-O', '^has_journal',
             '-E', f'lazy_itable_init=0,hash_seed={SLOT_UUIDS[slot]},root_owner={root_info.st_uid}:{root_info.st_gid}', '-m', '0',
             '-U', SLOT_UUIDS[slot], '-L', f'a733-root-{slot}', '-d', f'/proc/self/fd/{source_fd}/.',
             image, str(size // 4096)], fds=(fd, source_fd), epoch=epoch)
        batch(fd, [f'set_inode_field <2> mode {root_info.st_mode}'], epoch, output.parent, write=True)
        count = normalize_inodes(fd, epoch, output.parent)
        run(['tune2fs', '-O', 'read-only', image], fds=(fd,), epoch=epoch)
        check_superblock(fd, size, slot, epoch)
        run(['e2fsck', '-fn', image], fds=(fd,), epoch=epoch)
        named, opened = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False), os.fstat(fd)
        require((named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino)
                and opened.st_nlink == 1, 'output identity changed')
        tools = {}
        for tool in ('mke2fs', 'tune2fs', 'debugfs', 'e2fsck'):
            # Version probes are diagnostic only; package pinning belongs to the outer builder.
            result = subprocess.run([tool, '-V'], capture_output=True, timeout=10,
                                    env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
            tools[tool] = (result.stdout + result.stderr).decode('utf-8', 'replace').splitlines()[0]
        return {'epoch': epoch, 'inode_count': count, 'tools': tools}
    finally:
        for opened_fd in (fd, parent_fd, source_fd):
            if opened_fd is not None:
                os.close(opened_fd)

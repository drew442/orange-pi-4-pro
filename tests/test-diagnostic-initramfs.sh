#!/usr/bin/env bash
# Host-only packaging regression checks; no board boot or KVM evidence.
set -euo pipefail
export LC_ALL=C
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
# Minimal ELF fixtures exercise architecture/static checks without downloading or
# pretending this program is BusyBox. Real BusyBox runtime is a separate gate.
python3 - "$work" <<'PY'
import pathlib
import struct
import sys
root = pathlib.Path(sys.argv[1])
def elf(name, machine=183, interp=False):
    ident = b'\x7fELF\x02\x01\x01' + bytes(9)
    data = ident + struct.pack('<HHIQQQIHHHHHH', 2, machine, 1, 0x400078,
                               64, 0, 0, 64, 56, 1, 0, 0, 0)
    kind = 3 if interp else 1
    data += struct.pack('<IIQQQQQQ', kind, 5, 120, 0x400078, 0x400078,
                        8, 8, 1)
    data += b'/ld.so\0\0' if interp else bytes(8)
    path = root / name
    path.write_bytes(data)
    path.chmod(0o755)
elf('aarch64-static')
elf('x86-static', machine=62)
elf('aarch64-dynamic', interp=True)
PY
mkdir -p "$work/modules/6.6-test/kernel/drivers"
printf 'module-fixture\n' > "$work/modules/6.6-test/kernel/drivers/test.ko"
chmod 0664 "$work/modules/6.6-test/kernel/drivers/test.ko"
recipe=$repo/build/diagnostic-initramfs.sh
"$recipe" "$work/aarch64-static" "$work/first.gz" "$work/modules"
touch -d @123456789 "$work/aarch64-static" "$work/modules/6.6-test/kernel/drivers/test.ko"
(umask 077; "$recipe" "$work/aarch64-static" "$work/second.gz" "$work/modules")
cmp "$work/first.gz" "$work/second.gz"
"$recipe" "$work/aarch64-static" "$work/no-modules.gz"
for bad in x86-static aarch64-dynamic; do
    if "$recipe" "$work/$bad" "$work/rejected.gz" > "$work/error" 2>&1; then
        echo "Incorrectly accepted $bad" >&2; exit 1
    fi
    [[ ! -e $work/rejected.gz ]]
done
cp "$work/aarch64-static" "$work/original-input"
if "$recipe" "$work/aarch64-static" "$work/aarch64-static" > "$work/error" 2>&1; then
    echo 'Incorrectly accepted output overwriting input' >&2; exit 1
fi
cmp "$work/aarch64-static" "$work/original-input"
mkdir "$work/not-a-file"
if "$recipe" "$work/aarch64-static" "$work/not-a-file" > "$work/error" 2>&1; then
    echo 'Incorrectly accepted directory output' >&2; exit 1
fi
ln -s /etc "$work/modules/escape"
if "$recipe" "$work/aarch64-static" "$work/first.gz" "$work/modules" > "$work/error" 2>&1; then
    echo 'Incorrectly accepted module symlink' >&2; exit 1
fi
cmp "$work/first.gz" "$work/second.gz"
python3 - "$work" "$repo" <<'PY'
import gzip
import pathlib
import re
import stat
import sys
work, repo = map(pathlib.Path, sys.argv[1:])
def unpack(path):
    data = gzip.decompress(path.read_bytes())
    files = {}
    offset = 0
    while True:
        assert data[offset:offset + 6] == b'070701'
        fields = [int(data[offset + 6 + i * 8:offset + 14 + i * 8], 16)
                  for i in range(13)]
        mode, uid, gid, mtime, size, namesize = (fields[i] for i in (1, 2, 3, 5, 6, 11))
        offset += 110
        name = data[offset:offset + namesize - 1].decode()
        offset = (offset + namesize + 3) & ~3
        payload = data[offset:offset + size]
        offset = (offset + size + 3) & ~3
        if name == 'TRAILER!!!':
            break
        assert uid == gid == mtime == 0, (name, uid, gid, mtime)
        assert not name.startswith('/'), name
        assert '..' not in pathlib.PurePosixPath(name).parts
        normalized = '.' if name == '.' else './' + name.removeprefix('./')
        files[normalized] = (mode, payload)
    return files
files = unpack(work / 'first.gz')
assert files['./init'][1] == (repo / 'configs/diagnostic-init').read_bytes()
assert stat.S_ISREG(files['./init'][0]) and files['./init'][0] & 0o777 == 0o755
assert files['./bin/sh'][1] == b'busybox'
assert stat.S_ISLNK(files['./bin/sh'][0])
assert stat.S_ISDIR(files['./sbin'][0])
assert stat.S_ISLNK(files['./sbin/modprobe'][0])
assert files['./sbin/modprobe'][1] == b'../bin/busybox'
# The kernel helper resolves to the same authenticated static BusyBox binary.
assert stat.S_ISREG(files['./bin/busybox'][0])
assert files['./lib/modules/6.6-test/kernel/drivers/test.ko'][1] == b'module-fixture\n'
assert stat.S_IMODE(files['./lib/modules/6.6-test/kernel/drivers/test.ko'][0]) == 0o664
for name in ('dev', 'proc', 'sys', 'run', 'tmp'):
    assert stat.S_ISDIR(files['./' + name][0])
assert not any(name.endswith('.ko') for name in unpack(work / 'no-modules.gz'))
init = files['./init'][1].decode()
mounts = re.findall(r'^mount -t (\S+)', init, re.M)
assert mounts == ['proc', 'sysfs', 'devtmpfs', 'tmpfs', 'tmpfs'], mounts
assert not re.search(r'^\s*(swapon|mkswap|mkfs\S*|dd|fsck\S*|reboot|poweroff)\b', init, re.M)
assert 'while :; do' in init and '/bin/sh -i </dev/console' in init
assert 'udhcpc -f -n -q -t 3 -T 3' in init
assert not re.search(r'^\s*(telnetd|httpd|sshd)\b', init, re.M)
(work / 'udhcpc.script').write_bytes(files['./etc/udhcpc.script'][1])
PY
# Verify the extracted DHCP hook changes addresses/routes only; no lease writes.
mkdir "$work/mock-bin"
cat > "$work/mock-bin/ip" <<'MOCK'
#!/bin/sh
printf '%s\n' "$*" >> "$IP_LOG"
MOCK
chmod +x "$work/mock-bin/ip"
export IP_LOG=$work/ip.log
PATH="$work/mock-bin:$PATH" interface=eth0 ip=192.0.2.10 subnet=255.255.255.0 mask=24 router='192.0.2.1 192.0.2.2' sh "$work/udhcpc.script" bound
printf '%s\n' 'addr flush dev eth0' 'addr add 192.0.2.10/24 dev eth0' 'route replace default via 192.0.2.1 dev eth0' > "$work/expected"
cmp "$work/ip.log" "$work/expected"
PATH="$work/mock-bin:$PATH" sh "$work/udhcpc.script" deconfig
cmp "$work/ip.log" "$work/expected"
bash -n "$recipe" "$repo/tests/test-diagnostic-initramfs.sh"
sh -n "$repo/configs/diagnostic-init" "$work/udhcpc.script"
echo 'PASS: deterministic packaging, static AArch64 checks, module safety, archive metadata and diagnostic policy (host only)'

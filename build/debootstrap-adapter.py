#!/usr/bin/env python3
"""Prepare a byte-pinned stock debootstrap with a narrow two-phase override.

This is preparation only. Foreign-stage device files are inert placeholders;
trusted namespace setup must bind devices and private APIs before second stage.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat

VERSION = '1.0.126+nmu1ubuntu0.9'
SOURCES = {
    'usr/sbin/debootstrap': (25026, 'e5ec6d523bdbf9287305609009c7acc5fbdd64f0b2ad95a6810ed70dc85577de'),
    'usr/share/debootstrap/functions': (48952, '2fe03b61a02659d131f541aabd51a8eecc658b87b9f5e279addfd6e44de5ce37'),
    'usr/share/debootstrap/scripts/sid': (508, '578da6cee38e4d0558f11f37876273c9b22f2b583457377f86c54a7e9c76b11e'),
    'usr/share/debootstrap/scripts/debian-common': (6683, '5a02bfaebb16313b139b2872e6d9a877bab7a3b8065b8a1c7ad918ddea2120a6'),
}
DEVICES = {'null': (1, 3), 'zero': (1, 5), 'full': (1, 7), 'random': (1, 8),
           'urandom': (1, 9), 'tty': (5, 0), 'console': (5, 1), 'ptmx': (5, 2)}
POLICY = {
    'foreign': 'arm64; first_stage only; TARGET=/a733-export-output/arm-root; data extraction only; eight exclusive regular empty mode-0666 namespace-root placeholders, never devices',
    'second_stage': 'arm64; second_stage only; TARGET=/; seven prebound real character devices, console remains placeholder; private read-only proc/filtered sysfs required',
    'overrides': ['check_sane_mount', 'setup_devices', 'setup_proc'],
    'restrictions': 'No mknod, mount, umount, capability restoration, container spoofing, fakeroot, dependency/signature/service/chroot bypass; original executable and scripts unchanged; functions append only',
    'api_setup': 'Trusted external helper must finish before ARM scripts; shell verifies own PID, NNP and all five capability sets, restrictive private API mounts, filtered sysfs and proc masks',
}

OVERRIDE = r'''
# BEGIN A733 PINNED ADAPTER v1 -- append-only override policy
# Foreign phase is DATA EXTRACTION ONLY. Regular device placeholders do not
# prove device readiness. Trusted external setup must exit before second stage.
# No container identity, privilege, dependency, signature or chroot override.
a733_phase () {
    [ "${ARCH:-}" = arm64 ] || return 1
    [ "${VARIANT:-}" != fakechroot ] && [ -z "${FAKECHROOT:-}" ] || return 1
    case "${TARGET:-}" in
        /a733-export-output/arm-root)
            [ "${HOST_ARCH:-}:${HOST_OS:-}" = amd64:amd64 ] &&
            am_doing_phase first_stage && ! am_doing_phase second_stage ;;
        /)
            [ "${HOST_ARCH:-}:${HOST_OS:-}" = arm64:arm64 ] &&
            am_doing_phase second_stage && ! am_doing_phase first_stage ;;
        *) return 1 ;;
    esac
}

# MUST run in the original shell: builtin read opens /proc/self/status for
# that shell, and its rendered Pid must equal $$. Do not subshell or pipe this.
a733_verify_status () {
    a733_pid= a733_nnp= a733_caps=0
    while IFS=' 	' read -r a733_key a733_value a733_extra; do
        case "$a733_key" in
            Pid:) [ -z "$a733_pid" ] && [ -z "$a733_extra" ] || return 1
                  a733_pid=$a733_value ;;
            NoNewPrivs:) [ -z "$a733_nnp" ] && [ -z "$a733_extra" ] || return 1
                        a733_nnp=$a733_value ;;
            CapInh:|CapPrm:|CapEff:|CapBnd:|CapAmb:)
                [ -z "$a733_extra" ] && [ "${#a733_value}" = 16 ] || return 1
                case "$a733_value" in *[!0123456789abcdefABCDEF]*) return 1 ;; esac
                [ $((0x$a733_value & 0xa631000)) = 0 ] || return 1
                # Bitset also rejects duplicate/missing capability fields.
                case "$a733_key" in
                    CapInh:) a733_bit=1 ;; CapPrm:) a733_bit=2 ;;
                    CapEff:) a733_bit=4 ;; CapBnd:) a733_bit=8 ;; CapAmb:) a733_bit=16 ;;
                esac
                [ $((a733_caps & a733_bit)) = 0 ] || return 1
                a733_caps=$((a733_caps | a733_bit)) ;;
        esac
    done < /proc/self/status
    [ "$a733_pid" = "$$" ] && [ "$a733_nnp" = 1 ] && [ "$a733_caps" = 31 ]
}

a733_verify_mounts () (
    set -f
    IFS=' '
    a733_proc=0 a733_sys=0 a733_filtered=0 a733_masks=0
    while IFS=' ' read -r a733_id a733_parent a733_dev a733_root a733_path a733_opts a733_tail; do
        case "$a733_path" in /proc|/proc/*|/sys|/sys/*) ;; *) continue ;; esac
        case ",$a733_opts," in *,ro,*) ;; *) return 1 ;; esac
        case ",$a733_opts," in *,nosuid,*) ;; *) return 1 ;; esac
        case ",$a733_opts," in *,nodev,*) ;; *) return 1 ;; esac
        case ",$a733_opts," in *,noexec,*) ;; *) return 1 ;; esac
        case "$a733_path" in
            /proc/kcore) a733_maskbit=1 ;; /proc/keys) a733_maskbit=2 ;;
            /proc/kallsyms) a733_maskbit=4 ;; /proc/sysrq-trigger) a733_maskbit=8 ;;
            /proc/timer_list) a733_maskbit=16 ;; /sys/firmware) a733_maskbit=32 ;;
            *) a733_maskbit=0 ;;
        esac
        if [ "$a733_maskbit" != 0 ]; then
            [ $((a733_masks & a733_maskbit)) = 0 ] || return 1
            a733_masks=$((a733_masks | a733_maskbit))
        fi
        # Split optional fields through the separator without external tools.
        set -- $a733_tail
        while [ "$#" -gt 0 ] && [ "$1" != - ]; do
            case "$1" in shared:*|master:*|propagate_from:*) return 1 ;; esac
            shift
        done
        [ "$#" -ge 4 ] && [ "$1" = - ] || return 1
        shift
        case "$a733_path:$1" in
            /proc:proc) a733_proc=$((a733_proc + 1)) ;;
            /sys:tmpfs) a733_sys=$((a733_sys + 1)) ;;
        esac
        if [ "$1" = sysfs ]; then
            case "$a733_path" in
                /sys/block) a733_bit=1 ;; /sys/bus) a733_bit=2 ;;
                /sys/class) a733_bit=4 ;; /sys/dev) a733_bit=8 ;;
                /sys/devices) a733_bit=16 ;; /sys/kernel) a733_bit=32 ;;
                *) return 1 ;;
            esac
            [ $((a733_filtered & a733_bit)) = 0 ] || return 1
            a733_filtered=$((a733_filtered | a733_bit))
        fi
    done < /proc/self/mountinfo
    [ "$a733_proc" = 1 ] && [ "$a733_sys" = 1 ] && [ "$a733_filtered" = 63 ] || return 1
    [ $((a733_masks & 32)) = 32 ] || return 1
    a733_maskbit=1
    for a733_path in /proc/kcore /proc/keys /proc/kallsyms /proc/sysrq-trigger /proc/timer_list; do
        if [ -e "$a733_path" ] || [ -L "$a733_path" ]; then
            [ $((a733_masks & a733_maskbit)) = "$a733_maskbit" ] || return 1
        else
            [ $((a733_masks & a733_maskbit)) = 0 ] || return 1
        fi
        a733_maskbit=$((a733_maskbit * 2))
    done
)

a733_verify_devices () (
    # No Python or awk: coreutils stat is present in required package data.
    for a733_spec in null:1:3 zero:1:5 full:1:7 random:1:8 urandom:1:9 tty:5:0 ptmx:5:2; do
        IFS=: read -r a733_name a733_major a733_minor <<EOF
$a733_spec
EOF
        a733_path=/dev/$a733_name
        [ ! -L "$a733_path" ] && [ -c "$a733_path" ] || return 1
        [ "$(stat -c '%t:%T' -- "$a733_path")" = "$a733_major:$a733_minor" ] || return 1
    done
    # nspawn does not supply console. It must still be the inert placeholder.
    [ ! -L /dev/console ] && [ -f /dev/console ] || return 1
    [ "$(stat -c '%u:%g:%s:%a:%h' -- /dev/console)" = '0:0:0:666:1' ] || return 1
    for a733_spec in fd:/proc/self/fd stdin:/proc/self/fd/0 stdout:/proc/self/fd/1 stderr:/proc/self/fd/2; do
        IFS=: read -r a733_name a733_dest <<EOF
$a733_spec
EOF
        [ -L "/dev/$a733_name" ] && [ "$(readlink -- "/dev/$a733_name")" = "$a733_dest" ] || return 1
    done
    [ -d /dev/pts ] && [ ! -L /dev/pts ] && [ -d /dev/shm ] && [ ! -L /dev/shm ]
)

a733_verify_api () (
    a733_verify_mounts || return 1
    [ -d /sys/class/net ] && [ -d /sys/firmware ] && [ ! -L /sys/firmware ] || return 1
    # Include hidden names; unmatched literal patterns are ignored only if absent.
    a733_lo=0
    for a733_path in /sys/class/net/* /sys/class/net/.[!.]* /sys/class/net/..?*; do
        [ -e "$a733_path" ] || [ -L "$a733_path" ] || continue
        [ "$a733_path" = /sys/class/net/lo ] || return 1
        a733_lo=$((a733_lo + 1))
    done
    [ "$a733_lo" = 1 ] || return 1
    for a733_path in /sys/firmware/* /sys/firmware/.[!.]* /sys/firmware/..?*; do
        [ ! -e "$a733_path" ] && [ ! -L "$a733_path" ] || return 1
    done
    for a733_path in /proc/kcore /proc/keys /proc/kallsyms /proc/sysrq-trigger /proc/timer_list; do
        if [ -e "$a733_path" ] || [ -L "$a733_path" ]; then
            [ ! -L "$a733_path" ] && [ -f "$a733_path" ] || return 1
            [ "$(stat -c '%s:%a:%h' -- "$a733_path")" = '0:0:1' ] || return 1
        fi
    done
)

check_sane_mount () {
    a733_phase || return 1
    [ "$#" = 1 ] && [ "$1" = "$TARGET" ] || return 1
    a733_verify_status || return 1
    if [ "$TARGET" = / ]; then
        a733_verify_devices && a733_verify_api || return 1
    fi
    # Fresh exclusive script tests real execution; cleanup failure is fatal.
    [ -d "$TARGET" ] && [ ! -L "$TARGET" ] || return 1
    a733_exec=$(mktemp "$TARGET/.a733-exec.XXXXXXXXXX") || return 1
    if ! printf '#!/bin/sh\nexit 0\n' > "$a733_exec" || ! chmod 700 "$a733_exec"; then
        rm -f -- "$a733_exec" || return 1
        return 1
    fi
    a733_result=0
    "$a733_exec" || a733_result=1
    rm -f -- "$a733_exec" || return 1
    return "$a733_result"
}

setup_devices () (
    a733_phase && [ "$TARGET" = /a733-export-output/arm-root ] || return 1
    [ "$(id -u)" = 0 ] && [ "$(id -g)" = 0 ] || return 1
    [ -d "$TARGET" ] && [ ! -L "$TARGET" ] || return 1
    if [ ! -e "$TARGET/dev" ] && [ ! -L "$TARGET/dev" ]; then
        mkdir -m 755 -- "$TARGET/dev" || return 1
    fi
    [ -d "$TARGET/dev" ] && [ ! -L "$TARGET/dev" ] || return 1
    [ "$(stat -c '%u:%g:%a' -- "$TARGET/dev")" = '0:0:755' ] || return 1
    # Preflight the entire set before creating anything. Existing nodes are
    # rejected, even empty safe files: creation is exclusive and never replaces.
    for a733_name in null zero full random urandom tty console ptmx pts shm fd stdin stdout stderr; do
        [ ! -e "$TARGET/dev/$a733_name" ] && [ ! -L "$TARGET/dev/$a733_name" ] || return 1
    done
    for a733_name in null zero full random urandom tty console ptmx; do
        (umask 000; set -C; : > "$TARGET/dev/$a733_name") || return 1
        chmod 666 -- "$TARGET/dev/$a733_name" || return 1
        [ ! -L "$TARGET/dev/$a733_name" ] && [ -f "$TARGET/dev/$a733_name" ] || return 1
        [ "$(stat -c '%u:%g:%s:%a:%h' -- "$TARGET/dev/$a733_name")" = '0:0:0:666:1' ] || return 1
    done
    mkdir -m 755 -- "$TARGET/dev/pts" "$TARGET/dev/shm" || return 1
    ln -s /proc/self/fd "$TARGET/dev/fd" &&
    ln -s /proc/self/fd/0 "$TARGET/dev/stdin" &&
    ln -s /proc/self/fd/1 "$TARGET/dev/stdout" &&
    ln -s /proc/self/fd/2 "$TARGET/dev/stderr"
)

setup_proc () {
    a733_phase && [ "$TARGET" = / ] || return 1
    a733_verify_status && a733_verify_devices && a733_verify_api || return 1
    # Match stock's regular mtab removal; no mounts, unmounts or cleanup hooks.
    if [ -f /etc/mtab ] && [ ! -L /etc/mtab ]; then
        rm -f -- /etc/mtab || return 1
    fi
}
# END A733 PINNED ADAPTER v1
'''.encode()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _physical(fd):
    # Linux statfs: first machine word is f_type; oversized storage avoids ABI
    # layout assumptions for the remaining fields. Reject pseudo/memory APIs.
    buf = ctypes.create_string_buffer(256)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.fstatfs(fd, ctypes.byref(buf)) != 0:
        raise OSError(ctypes.get_errno(), 'fstatfs failed')
    kind = ctypes.c_ulong.from_buffer(buf).value
    _require(kind not in {0x9fa0, 0x62656572, 0x01021994, 0x858458f6, 0x63677270,
                          0x64626720, 0x1cd1, 0x27e0eb, 0x73636673, 0x65735546},
             'pseudo/memory filesystem is not a physical source/output tree')


def _directory(path):
    """Hold a directory reached without following any symlink component."""
    path = Path(os.path.abspath(path))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        _physical(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_source(root_fd, relative, pin):
    parts = relative.split('/')
    parent = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = new
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            _physical(stream.fileno())
            _require(stat.S_ISREG(before.st_mode) and before.st_size == pin[0], 'source type/size mismatch: ' + relative)
            data = stream.read(pin[0] + 1)
            after = os.fstat(stream.fileno())
            stable = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_uid, item.st_gid,
                                   item.st_nlink, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
            _require(stable(before) == stable(after), 'source changed while held: ' + relative)
            _require(len(data) == pin[0] and hashlib.sha256(data).hexdigest() == pin[1], 'source hash mismatch: ' + relative)
            return data
    finally:
        os.close(parent)


def _mountinfo():
    return Path('/proc/self/mountinfo').read_text()


def _filesystem_location(path):
    """Resolve bind aliases by mount device and filesystem-relative pathname."""
    matches = []
    unescape = lambda value: re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)
    for index, line in enumerate(_mountinfo().splitlines()):
        left, right = line.split(' - ', 1)
        fields = left.split()
        mount = Path(unescape(fields[4]))
        if path == mount or mount in path.parents:
            location = Path(unescape(fields[3])) / path.relative_to(mount)
            matches.append((len(mount.parts), index, fields[2], location))
    _require(bool(matches), 'unknown filesystem topology: ' + str(path))
    _, _, device, location = max(matches)
    return device, location


def _guard_overlap(output, source_root):
    protected = [source_root / 'usr/share/debootstrap', source_root / 'usr/sbin/debootstrap']
    if source_root != Path('/'):
        protected.append(source_root)
    output_device, output_location = _filesystem_location(output)
    for source in protected:
        _require(output != source and output not in source.parents and source not in output.parents,
                 'source/output overlap')
        source_device, source_location = _filesystem_location(source)
        _require(output_device != source_device or
                 (output_location != source_location and output_location not in source_location.parents and
                  source_location not in output_location.parents),
                 'source/output overlap through filesystem alias')


def prepare(output: Path, source_root: Path = Path('/')) -> dict:
    # Validate the caller's spelling before any normalization can erase '..'.
    output, source_root = Path(output), Path(source_root)
    for path in (output, source_root):
        _require(path.is_absolute() and '..' not in path.parts,
                 'paths must be absolute and contain no parent traversal')
    _require(output != Path('/'), 'root cannot be output')
    # Default / protects only debootstrap's installed sources, allowing a
    # separate /a733-export-output bind on the same backing filesystem.
    _guard_overlap(output, source_root)
    root_fd = _directory(source_root)
    try:
        data = {relative: _read_source(root_fd, relative, pin) for relative, pin in SOURCES.items()}
    finally:
        os.close(root_fd)
    parent_fd = _directory(output.parent)
    try:
        os.mkdir(output.name, mode=0o700, dir_fd=parent_fd)  # never reuse output
        output_fd = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    try:
        # Held private output directory also prevents parent-path substitution.
        held = Path('/proc/self/fd') / str(output_fd)
        (held / 'scripts').mkdir(mode=0o700)
        records = {}
        for relative, original in data.items():
            name = 'debootstrap' if relative == 'usr/sbin/debootstrap' else relative.removeprefix('usr/share/debootstrap/')
            patched = original + OVERRIDE if name == 'functions' else original
            with (held / name).open('xb') as stream:
                stream.write(patched)
                os.fchmod(stream.fileno(), 0o755 if name == 'debootstrap' else 0o644)
                os.fsync(stream.fileno())
            records[relative] = {'source_bytes': len(original), 'source_sha256': hashlib.sha256(original).hexdigest(),
                                 'output': name, 'output_bytes': len(patched), 'output_sha256': hashlib.sha256(patched).hexdigest()}
        (held / 'scripts/bookworm').symlink_to('sid')
        manifest = {'version': VERSION, 'sources': records, 'policy': POLICY,
                    'override_sha256': hashlib.sha256(OVERRIDE).hexdigest(),
                    'devices': {name: list(numbers) for name, numbers in DEVICES.items()},
                    'bookworm_symlink': 'sid'}
        with (held / 'manifest.json').open('x') as stream:
            json.dump(manifest, stream, indent=2)
            stream.write('\n')
            os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
        os.fsync(output_fd)
        return manifest
    finally:
        os.close(output_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, default=Path('/'))
    args = parser.parse_args()
    prepare(args.output, args.source_root)


if __name__ == '__main__':
    main()

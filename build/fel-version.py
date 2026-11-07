#!/usr/bin/env python3
"""Build a version-only host FEL probe; never access USB or install software."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile

REPO = Path(__file__).resolve().parents[1]
COMMIT = '34dfa20f8284273085666436e4cac09af19646c8'
LOCK = REPO / 'configs/builder-replay-packages-20261106.lock.tsv'
PACKAGES = ('gcc-11', 'gcc-11-base:amd64', 'cpp-11', 'libgcc-11-dev:amd64',
            'binutils', 'binutils-common:amd64', 'binutils-x86-64-linux-gnu',
            'libbinutils:amd64', 'libc6:amd64', 'libc6-dev:amd64', 'libc-dev-bin',
            'libusb-1.0-0:amd64', 'libusb-1.0-0-dev:amd64', 'pkg-config')
ENV = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'TZ': 'UTC', 'SOURCE_DATE_EPOCH': '0'}
CFLAGS = ['-std=gnu99', '-O2', '-Wall', '-Wextra', '-ffunction-sections',
          '-fdata-sections', '-fno-ident', '-Wl,--gc-sections', '-Wl,--build-id=none']


def require(condition, message):
    if not condition:
        raise ValueError(message)


def physical(path):
    path = Path(os.path.abspath(path))
    require(path.resolve() == path, f'path must be physical (no symlinks): {path}')
    return path


def regular(path):
    require(stat.S_ISREG(path.lstat().st_mode), f'not a regular file: {path}')
    return path.read_bytes()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(args, cwd=None, binary=False, diagnostics=None):
    try:
        result = subprocess.run([str(arg) for arg in args], cwd=cwd, env=ENV,
                                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as error:
        if diagnostics is not None:
            diagnostics.write_bytes(b'stdout:\n' + (error.stdout or b'') + b'\nstderr:\n' + (error.stderr or b''))
        raise
    if diagnostics is not None:
        diagnostics.write_bytes(b'stdout:\n' + result.stdout + b'\nstderr:\n' + result.stderr)
    return result.stdout if binary else result.stdout.decode()


def compiler_command(output, retained, usb_flags):
    return ['/usr/bin/gcc-11', *CFLAGS, '-ffile-prefix-map=' + str(output) + '=/fel-version',
            '-I' + str(retained), '-I' + str(retained / 'include'),
            str(output / 'fel-version.c'),
            *(str(retained / name) for name in ('fel_lib.c', 'soc_info.c', 'progress.c')),
            *usb_flags, '-o', str(output / 'fel-version')]


def check_source(source):
    source = physical(source)
    require(source.is_dir() and physical(source / '.git').is_dir(),
            'source must be a physical Git checkout')
    require(run(['/usr/bin/git', '--no-replace-objects', '-C', source, 'rev-parse', 'HEAD']).strip() == COMMIT,
            'source HEAD differs from pinned upstream commit')
    require(not run(['/usr/bin/git', '--no-replace-objects', '-C', source, 'status', '--porcelain', '--untracked-files=all']).strip(),
            'source checkout must be clean')
    paths = run(['/usr/bin/git', '--no-replace-objects', '-C', source, 'ls-files', '-z'], binary=True).split(b'\0')
    hashes = {}
    for name in paths:
        if not name:
            continue
        name = os.fsdecode(name)
        path = physical(source / name)
        require(path.is_relative_to(source), 'source path escapes checkout')
        hashes[name] = digest(regular(path))
    return hashes


def check_output(output):
    output = physical(output)
    require(output.parent == physical(REPO / 'out') and
            re.fullmatch(r'fel-version-[A-Za-z0-9][A-Za-z0-9_-]*', output.name),
            'output must be a fresh repo/out/fel-version-NAME directory')
    require(not output.exists(), 'output already exists')
    return output


def check_builder():
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    require(release.get('ID', '').strip('"') == 'ubuntu' and
            release.get('VERSION_ID', '').strip('"') == '22.04', 'requires frozen Ubuntu Jammy builder')
    require(run(['/usr/bin/dpkg', '--print-architecture']).strip() == 'amd64', 'requires amd64 builder')
    locked = {row.split('\t')[0]: row.split('\t')[1] for row in regular(LOCK).decode().splitlines()
              if row and not row.startswith('#')}
    versions = {}
    for package in PACKAGES:
        actual = run(['/usr/bin/dpkg-query', '-W', '-f=${Version}', package]).strip()
        require(actual == locked[package], f'package differs from frozen lock: {package}')
        versions[package] = actual
    require(run(['/usr/bin/gcc-11', '-dumpmachine']).strip() == 'x86_64-linux-gnu', 'unexpected compiler target')
    return versions


def check_symbols(binary):
    symbols = run(['/usr/bin/nm', '-a', binary])
    require(not re.search(r'\b(?:aw_fel_(?:read|write\w*|execute|remotefunc\w*)|fel_(?:readl\w*|writel\w*|read_sid|memmove|clrsetbits\w*)|list_fel_devices|\w*flash\w*)\b', symbols),
            'forbidden memory/execute/SID/flash symbol retained')
    dynamic = run(['/usr/bin/readelf', '-d', binary])
    needed = re.findall(r'\(NEEDED\).*?\[(.*?)\]', dynamic)
    require(set(needed) == {'libusb-1.0.so.0', 'libc.so.6'}, f'unexpected runtime libraries: {needed}')
    return symbols, dynamic, needed


def build(source, output):
    source = physical(source)
    before = check_source(source)
    output = check_output(output)
    dependencies = check_builder()
    frontend = regular(REPO / 'build/fel-version.c')
    recipe = regular(Path(__file__).resolve())
    lock_data = regular(LOCK)
    archive = run(['/usr/bin/git', '--no-replace-objects', '-C', source, 'archive', '--format=tar', COMMIT], binary=True)
    output.mkdir()
    (output / 'sunxi-tools-source.tar').write_bytes(archive)
    retained = output / 'source'
    retained.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar:
            path = retained / member.name
            require(not Path(member.name).is_absolute() and '..' not in Path(member.name).parts,
                    'unsafe source archive path')
            require(member.isfile() or member.isdir(), 'source archive contains nonregular entry')
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                data = tar.extractfile(member).read()
                require(digest(data) == before[member.name], 'archive differs from source checkout')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
    (output / 'fel-version.c').write_bytes(frontend)
    (output / 'fel-version.py').write_bytes(recipe)
    (output / LOCK.name).write_bytes(lock_data)
    (output / 'LICENSE.md').write_bytes(regular(retained / 'LICENSE.md'))
    (output / 'NOTICE.txt').write_text('FEL transport: sunxi-tools https://github.com/linux-sunxi/sunxi-tools\n'
        f'Commit {COMMIT}; GPL-2.0-or-later. Original source notices retained in complete source archive.\n'
        'Frontend: fel-version.c, GPL-2.0-or-later. Retain source archive, frontend, recipe and license with binary.\n')
    usb_flags = run(['/usr/bin/pkg-config', '--cflags', '--libs', 'libusb-1.0']).split()
    require(all(flag.startswith(('-I/usr/include/', '-L/usr/lib/', '-lusb-1.0')) for flag in usb_flags),
            'unexpected pkg-config flags')
    command = compiler_command(output, retained, usb_flags)
    run(command, diagnostics=output / 'compiler.log')
    symbols, dynamic, needed = check_symbols(output / 'fel-version')
    (output / 'nm.txt').write_text(symbols)
    (output / 'readelf-dynamic.txt').write_text(dynamic)
    # These argument-only checks cannot call feldev_open. No valid USB address is tested here.
    run([output / 'fel-version', '--help'])
    invalid = subprocess.run([str(output / 'fel-version'), '0', '0'], env=ENV, capture_output=True)
    require(invalid.returncode == 2, 'invalid argument self-check failed')
    require(check_source(source) == before, 'source changed during build')
    require(regular(REPO / 'build/fel-version.c') == frontend and
            regular(Path(__file__).resolve()) == recipe and regular(LOCK) == lock_data,
            'recipe inputs changed during build')
    manifest = {'source_commit': COMMIT, 'source_url': 'https://github.com/linux-sunxi/sunxi-tools',
                'source_hashes': before, 'dependencies': dependencies, 'command': command,
                'compiler_version': run(['/usr/bin/gcc-11', '--version']), 'needed': needed,
                'environment': ENV, 'hardware_tested': False,
                'output_hashes': {str(path.relative_to(output)): digest(regular(path))
                                  for path in sorted(output.rglob('*')) if path.is_file()}}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(build(args.source, args.output))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'fel-version build refused: {error}\n')


if __name__ == '__main__':
    main()

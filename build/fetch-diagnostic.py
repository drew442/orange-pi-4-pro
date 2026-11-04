#!/usr/bin/env python3
"""Fetch exact inputs into a dedicated workspace; never reset an existing tree."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def main():
    if len(sys.argv) != 2:
        sys.exit(f'Usage: {sys.argv[0]} WORKSPACE')
    repo = Path(__file__).resolve().parent.parent
    lock = json.loads((repo / 'configs/diagnostic-sources.lock.json').read_text())
    for patch in lock['patches']:
        if digest(repo / patch['path']) != patch['sha256']:
            sys.exit(f"Patch hash mismatch: {patch['path']}")
    workspace = Path(sys.argv[1]).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    downloads = workspace / 'downloads'
    downloads.mkdir(exist_ok=True)
    for key, name in [('vendor', 'vendor'), ('kernel', 'kernel'), ('uboot', 'u-boot')]:
        spec = lock[key]
        tree = workspace / name
        if not tree.exists():
            run('git', 'init', str(tree))
            run('git', '-C', str(tree), 'remote', 'add', 'origin', spec['url'])
            run('git', '-C', str(tree), 'fetch', '--depth=1', 'origin', spec['commit'])
            run('git', '-C', str(tree), 'checkout', '--detach', spec['commit'])
        if run('git', '-C', str(tree), 'rev-parse', 'HEAD') != spec['commit']:
            sys.exit(f'Refusing unexpected HEAD in {tree}')
        if run('git', '-C', str(tree), 'status', '--porcelain', '--untracked-files=no'):
            sys.exit(f'Refusing tracked source modifications in {tree}')
    for spec in [lock['uboot_toolchain'], lock['busybox'], *lock['busybox_sources']]:
        target = downloads / spec['filename']
        if not target.exists():
            partial = target.with_suffix(target.suffix + '.partial')
            urllib.request.urlretrieve(spec['url'], partial)
            if digest(partial) != spec['sha256']:
                sys.exit(f'Hash mismatch: {partial}')
            partial.rename(target)
        if digest(target) != spec['sha256']:
            sys.exit(f'Hash mismatch: {target}')
    # Verify every vendor pack tool and firmware input, before executing tools.
    with (repo / 'configs/vendor-boot-inputs.sha256').open() as sums:
        subprocess.run(['sha256sum', '--check', '--quiet'], stdin=sums,
                       cwd=workspace / 'vendor', check=True)
    print('Pinned source and input hashes verified.')


if __name__ == '__main__':
    main()

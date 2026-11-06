#!/usr/bin/env python3
"""Fresh private ARM64 vDSO debug-path experiment; never a full kernel build."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys

SPEC = importlib.util.spec_from_file_location('vdso_kernel_helpers', Path(__file__).with_name('kernel-replay.py'))
kernel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kernel)
require, safe_path, read_regular, facts = kernel.require, kernel.safe_path, kernel.read_regular, kernel.facts
REPO, WORKSPACE, PROFILE, TARGET = kernel.REPO, kernel.WORKSPACE, kernel.PROFILE, kernel.TARGET
OWN_INPUTS = ('build/vdso-replay.py', 'tests/test_vdso_replay.py')
VDSO = 'arch/arm64/kernel/vdso'
TARGET_FACTS = {'bytes': 3296, 'sha256': 'f6b2803e8e4a17782f4d0cf089366c3a06f2bf2284ff245212ee2ac378168f94'}
EMBEDDING_OFFSET = 16289792


def validate_output(repo, workspace, output):
    # Reuse all full-kernel path guards, with this experiment's distinct prefix.
    require(output.name.startswith('vdso-replay-'), 'Use vdso-replay-NAME output')
    candidate = output.with_name('kernel-replay-' + output.name[len('vdso-replay-'):])
    kernel.validate_output(repo, workspace, candidate)
    safe_path(output)
    require(not os.path.lexists(output), 'Output already exists')


def initial_resources(paths):
    sample = kernel.resources(paths)
    require(min(sample['free_disk_bytes'].values()) >= 4_000_000_000,
            'vDSO-only experiment needs at least 4 GB available disk')
    require(sample['available_ram_bytes'] >= 3_000_000_000, 'Need at least 3 GB available RAM')
    return sample


def authenticate_embedding(image, vdso, expected_image, expected_vdso, expected_offset):
    kernel.helper.authenticate(image, expected_image)
    kernel.helper.authenticate(vdso, expected_vdso)
    blob, target = read_regular(image), read_regular(vdso)
    require(target, 'Empty vDSO target forbidden')
    offset = blob.find(target)
    require(offset == expected_offset and blob.find(target, offset + 1) == -1,
            'Complete vDSO must occur exactly once at registered Image offset')
    return {'image': expected_image, 'vdso': expected_vdso, 'offset': offset,
            'occurrences': 1, 'method': 'Complete unchanged vDSO byte sequence in authenticated Image'}


def snapshot(repo, workspace):
    before, config = kernel.snapshot(repo, workspace)
    # kernel.project_source binds its helpers; additionally bind this recipe and tests.
    for relative in OWN_INPUTS:
        committed = kernel.git(repo, 'show', f'{before["project"]["commit"]}:{relative}')
        actual = facts(repo / relative)
        require(actual == {'bytes': len(committed), 'sha256': hashlib.sha256(committed).hexdigest()},
                f'Actual project input differs from HEAD: {relative}')
        before['project']['inputs'][relative] = actual
    require(before['kernel']['shallow'] is True, 'Registered kernel cache must remain shallow')
    before['vdso_embedding'] = authenticate_embedding(
        workspace / 'artifacts/Image', workspace / 'kernel' / VDSO / 'vdso.so',
        before['targets']['Image'], TARGET_FACTS, EMBEDDING_OFFSET)
    debug = workspace / 'kernel' / VDSO / 'vdso.so.dbg'
    before['optional_debug_unanchored'] = facts(debug) if os.path.lexists(debug) else None
    return before, config


def make_command(tree, workspace):
    source, original = str(safe_path(tree)), str(safe_path(workspace / 'kernel'))
    require(re.fullmatch(r'[A-Za-z0-9/._-]+', source + original), 'Unsafe Make mapping spelling')
    return ['make', '-j2', 'V=1', 'ARCH=arm64', 'CROSS_COMPILE=aarch64-linux-gnu-',
            'LOCALVERSION=-a733-diag', f'KCFLAGS=-fdebug-prefix-map={source}={original}',
            f'KAFLAGS=-Wa,--debug-prefix-map={source}={original}']


def compare_complete(actual, original):
    # The complete stripped ELF is the target, including its unchanged build ID.
    return {'actual': facts(actual), 'expected': facts(original),
            'equal': read_regular(actual) == read_regular(original)}


def capture_generated(tree, artifacts, make):
    for relative in ('include/generated', 'include/config', 'arch/arm64/include/generated'):
        root = tree / relative
        for directory, directories, files in os.walk(root, followlinks=False):
            for name in directories:
                safe_path(Path(directory) / name)
            for name in files:
                source = Path(directory) / name
                destination = artifacts / 'generated' / source.relative_to(tree)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(read_regular(source))
    commands = {}
    for name, flag in (('vgettimeofday', make[-2].split('=', 1)[1]),
                       ('note', make[-1].split('=', 1)[1]), ('sigreturn', make[-1].split('=', 1)[1])):
        source = tree / VDSO / f'.{name}.o.cmd'
        content = read_regular(source)
        require(flag.encode() in content, f'Actual {name} command lacks explicit debug-path mapping')
        destination = artifacts / 'commands' / source.name
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(content)
        commands[name] = facts(destination)
    return commands


def build(repo, workspace, output, before, config):
    output.mkdir()
    artifacts, logs, tree = output / 'artifacts', output / 'logs', output / 'kernel-source'
    artifacts.mkdir()
    logs.mkdir()
    result = {'schema': 1, 'target': TARGET, 'success': False, 'umask': '0002',
              'scope': 'vDSO-only; no Image, modules or dtbs targets',
              'hardware_acceptance': 'Not established; actual KVM guest remains required',
              'limitations': 'Cached shallow sources and registered builder; optional debug comparison is unanchored'}
    caller_umask = os.umask(0o002)
    try:
        with (logs / 'resources.jsonl').open('w') as resource_log:
            monitor = kernel.Monitor((workspace, output), resource_log)
            monitor.sample('start')
            monitor.thread.start()
            try:
                kernel.write_json(artifacts / 'input-snapshot.json', before)
                for relative, expected in before['project']['inputs'].items():
                    destination = artifacts / 'project-inputs' / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(repo / relative, destination)
                    kernel.helper.authenticate(destination, expected)
                original = artifacts / 'original-vdso.so'
                shutil.copyfile(workspace / 'kernel' / VDSO / 'vdso.so', original)
                kernel.helper.authenticate(original, before['vdso_embedding']['vdso'])
                if before['optional_debug_unanchored'] is not None:
                    shutil.copyfile(workspace / 'kernel' / VDSO / 'vdso.so.dbg', artifacts / 'original-vdso.so.dbg')
                    kernel.helper.authenticate(artifacts / 'original-vdso.so.dbg', before['optional_debug_unanchored'])
                with (logs / 'source-checkout.log').open('wb') as log:
                    monitor.command(kernel.git_command(workspace / 'kernel', 'clone', '--shared', '--no-checkout',
                                                       workspace / 'kernel', safe_path(tree)), output, log)
                    monitor.command(kernel.git_command(tree, 'checkout', '--detach', kernel.KERNEL_HEAD), tree, log)
                private_before = kernel.source_state(tree, kernel.KERNEL_HEAD)
                require(private_before == before['kernel'], 'Private source/shallow state differs')
                require(not os.path.lexists(tree / '.scmversion'), 'Unexpected private .scmversion')
                make = make_command(tree, workspace)
                kernel.write_json(artifacts / 'source-provenance.json', {
                    'project': before['project'], 'private_source_before': private_before,
                    'original_kernel': before['kernel'], 'original_vendor': before['vendor'],
                    'source_method': 'git clone --shared --no-checkout; detached pinned commit',
                    'build_environment': kernel.environment(), 'jobs': 2, 'umask': '0002',
                    'make_command': make, 'targets': ['olddefconfig', 'prepare'],
                    'flag_source': 'Pinned Makefile:1034-1035 appends KAFLAGS/KCFLAGS; arch/arm64/Makefile:192-195 prepare builds vDSO'})
                (tree / '.config').write_bytes(config)
                with (logs / 'kernel-config.log').open('wb') as log:
                    monitor.command([tree / 'scripts/config', '--file', tree / '.config', '--enable', 'VIRTUALIZATION',
                                     '--enable', 'KVM', '--enable', 'IKCONFIG', '--enable', 'IKCONFIG_PROC',
                                     '--set-str', 'SYSTEM_TRUSTED_KEYS', '', '--set-str', 'SYSTEM_REVOCATION_KEYS', ''], tree, log)
                    monitor.command(make + ['olddefconfig'], tree, log)
                shutil.copyfile(tree / '.config', artifacts / 'kernel.config')
                kernel.helper.authenticate(tree / '.config', before['targets']['kernel.config'])
                require(b'CONFIG_KVM=y\n' in read_regular(tree / '.config'), 'KVM must remain enabled')
                with (logs / 'vdso-build.log').open('wb') as log:
                    monitor.command(make + ['prepare'], tree, log)
                require(read_regular(tree / 'include/config/kernel.release').strip() == b'6.6.98-a733-diag',
                        'Unexpected kernel release')
                kernel.helper.authenticate(tree / '.config', before['targets']['kernel.config'])
                result['actual_commands'] = capture_generated(tree, artifacts, make)
                for name in ('vdso.so', 'vdso.so.dbg'):
                    shutil.copyfile(tree / VDSO / name, artifacts / name)
                result['comparison'] = compare_complete(artifacts / 'vdso.so', original)
                if before['optional_debug_unanchored'] is not None:
                    result['debug_comparison_unanchored'] = compare_complete(artifacts / 'vdso.so.dbg', artifacts / 'original-vdso.so.dbg')
                result['success'] = result['comparison']['equal']
                require(result['success'], 'Complete vDSO differs from authenticated unchanged target')
            except BaseException as exc:
                result['success'] = False
                result['error'] = f'{type(exc).__name__}: {exc}'
                raise
            finally:
                try:
                    after, _ = snapshot(repo, workspace)
                    kernel.write_json(artifacts / 'input-snapshot-after.json', after)
                    require(after == before, 'Shared inputs changed during experiment')
                    result['shared_inputs_unchanged'] = True
                    if tree.is_dir():
                        kernel.write_json(artifacts / 'private-source-after.json', kernel.source_state(tree, kernel.KERNEL_HEAD))
                except Exception as exc:
                    result['success'] = False
                    result['postcheck_error'] = str(exc)
                monitor.stop.set()
                monitor.thread.join()
                monitor.sample('end')
                if monitor.failed.is_set():
                    result['success'] = False
                    result['resource_error'] = monitor.reason
                kernel.write_json(artifacts / 'replay-result.json', result)
                entries = {p.relative_to(output).as_posix(): facts(p)
                           for directory in (artifacts, logs) for p in sorted(directory.rglob('*'))
                           if p.is_file() and not p.is_symlink()}
                kernel.write_json(output / 'replay-manifest.json', {'schema': 1, 'files': entries})
    finally:
        os.umask(caller_umask)
    require(result['success'], 'vDSO experiment failed final verification; see retained result')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True, choices=[TARGET])
    parser.add_argument('--builder-profile', required=True, choices=[PROFILE])
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        validate_output(REPO, WORKSPACE, args.output)
        initial_resources((WORKSPACE, args.output.parent))
        before, config = snapshot(REPO, WORKSPACE)
        signal.signal(signal.SIGTERM, kernel.interrupted)
        signal.signal(signal.SIGHUP, kernel.interrupted)
        build(REPO, WORKSPACE, args.output, before, config)
    except (ValueError, OSError, subprocess.CalledProcessError, KeyError, UnicodeError) as exc:
        print(f'vDSO replay refused/failed: {exc}', file=sys.stderr)
        return 1
    print(f'Complete vDSO matches authenticated unchanged target: {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

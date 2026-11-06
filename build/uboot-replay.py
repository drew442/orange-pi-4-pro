#!/usr/bin/env python3
"""One offline, private replay of a registered retained U-Boot challenge.

This is artifact reproduction, not hardware acceptance or a clean builder audit.
The challenge is authenticated public build input. Its opaque header consumer's
security role has not been fully audited; the vendor random default is preserved.
"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import threading
import time

TARGET = 'el2-diagnostic-20261104'
MANIFEST_SHA = '749d5ba0e95a516edfc4bf671b51d2533fb0588bc0d3abcdce090d1a081b3a32'
UBOOT_HEAD = 'b791be842935b27268ae3d00e943a9075495f30a'
WORKSPACE = Path('/home/drew/orange-pi-4-pro-build')
PROVENANCE = Path('/var/lib/a733-el2-work-20261104/out/el2-diagnostic-20261104-03/artifacts')
REPO = Path(__file__).resolve().parent.parent
GENERATOR = '''CLEAN_FILES += board/sunxi/sunxi_challenge.c
board/sunxi/sunxi_challenge.c:
\t@echo "  prepare sunxi_challenge..."
\t@dd if=/dev/urandom of=sunxi_challenge bs=128 count=1 > /dev/null 2>&1
\t@xxd -c 8 -i sunxi_challenge > board/sunxi/sunxi_challenge.c
\t@sed -i '/^unsigned/i __attribute__((__used__))' board/sunxi/sunxi_challenge.c
\t@rm sunxi_challenge
prepare: board/sunxi/sunxi_challenge.c
'''
REPLAY_GENERATOR = GENERATOR.replace(
    '\t@dd if=', 'ifneq ($(A733_REPLAY_CHALLENGE),)\n'
    '\t@cp -- "$(A733_REPLAY_CHALLENGE)" board/sunxi/sunxi_challenge.c\n'
    'else\n\t@dd if=').replace(
    'prepare: board/', 'endif\nprepare: board/')
RETAINED = ('uboot-sunxi-challenge.c', 'uboot-unpacked.bin', 'uboot.config',
            'boot_package.fex', 'uboot-build.log', 'uboot-provenance.sha256',
            'uboot-env-nowhere-size.patch', 'uboot-a733-el2-handoff.patch')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_path(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'Absolute path without traversal required')
    require(not any(root == path or root in path.parents for root in map(Path, ('/dev', '/proc', '/sys'))),
            'Device/pseudo-filesystem path forbidden')
    for part in (path, *path.parents):
        require(not part.is_symlink(), f'Symlink forbidden: {part}')
    return path


def read_regular(path):
    path = safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), f'Expected regular file: {path}')
        return stream.read()


def facts(path):
    # Stream large archives without retaining them in RAM.
    path = safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode), f'Expected regular file: {path}')
        checksum = hashlib.sha256()
        count = 0
        for chunk in iter(lambda: stream.read(1048576), b''):
            count += len(chunk)
            checksum.update(chunk)
        after = os.fstat(stream.fileno())
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns) and count == before.st_size,
                f'Input mutated while reading: {path}')
    return {'bytes': count, 'sha256': checksum.hexdigest()}


def authenticate(path, expected):
    require(facts(path) == expected, f'Untrusted input: {path}')


def validate_challenge(data):
    """Accept only the exact vendor xxd -c 8 format, containing 128 data bytes."""
    require(len(data) == 930, 'Challenge C must have canonical 930-byte length')
    text = data.decode('ascii')
    start = '__attribute__((__used__))\nunsigned char sunxi_challenge[] = {\n'
    end = '\n};\n__attribute__((__used__))\nunsigned int sunxi_challenge_len = 128;\n'
    require(text.startswith(start) and text.endswith(end), 'Unexpected challenge declarations')
    body = text[len(start):-len(end)]
    lines = body.split('\n')
    require(len(lines) == 16, 'Challenge must contain 128 bytes')
    for index, line in enumerate(lines):
        pattern = r'  0x[0-9a-f]{2}(?:, 0x[0-9a-f]{2}){7}' + (',' if index < 15 else '')
        require(re.fullmatch(pattern, line) is not None, 'Noncanonical challenge data')
    return bytes(int(value, 16) for value in re.findall(r'0x([0-9a-f]{2})', body))


def patch_generator(text):
    require(text.count(GENERATOR) == 1 and 'A733_REPLAY_CHALLENGE' not in text,
            'Unexpected pinned challenge generator context')
    return text.replace(GENERATOR, REPLAY_GENERATOR)


def validate_output(repo, workspace, provenance, output):
    for path in (repo, workspace, provenance, output):
        safe_path(path)
    require(workspace.is_dir() and provenance.is_dir(), 'Missing source/provenance directory')
    require(output.parent == repo / 'out' and output.parent.is_dir(), 'Output must be directly under repository out/')
    require(re.fullmatch(r'uboot-replay-[A-Za-z0-9][A-Za-z0-9._-]*', output.name), 'Use uboot-replay-NAME output')
    require(re.fullmatch(r'[A-Za-z0-9/._-]+', str(output)), 'Output spelling unsafe for Make')
    require(not os.path.lexists(output), 'Output already exists')
    for source in (workspace, provenance):
        require(source != output and source not in output.parents and output not in source.parents,
                'Input/output overlap forbidden')
    require(workspace != provenance and workspace not in provenance.parents and provenance not in workspace.parents,
            'Source/provenance overlap forbidden')


def run(command, cwd=None, env=None, log=None):
    if env is not None and cwd is not None:
        env = dict(env, PWD=str(cwd))
    result = subprocess.run(list(map(str, command)), cwd=cwd, env=env,
                            stdout=log or subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
    return result.stdout if log is None else None



PROJECT_INPUTS = (
    'build/uboot-replay.py', 'configs/diagnostic-sources.lock.json',
    'configs/vendor-boot-inputs.sha256', 'configs/builder-packages.lock.tsv',
    'docs/evidence/el2-diagnostic-20261104.json',
    'build/patches/uboot-env-nowhere-size.patch',
    'build/patches/uboot-a733-el2-handoff.patch',
)


def project_source(repo):
    """Prove the actual recipe and inputs belong to a clean Git commit."""
    safe_path(repo / '.git')
    require((repo / '.git').is_dir(), 'Project checkout requires local .git metadata')
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env['GIT_OPTIONAL_LOCKS'] = '0'
    command = ['git', '--no-optional-locks', '-C', repo]
    require(Path(run(command + ['rev-parse', '--show-toplevel'], env=env).decode().strip()) == repo,
            'Project Git root mismatch')
    head = run(command + ['rev-parse', 'HEAD'], env=env).decode().strip()
    require(re.fullmatch('[0-9a-f]{40}', head), 'Invalid project commit')
    status = run(command + ['status', '--porcelain', '--untracked-files=all'], env=env).decode()
    require(not status, 'Project checkout must be clean and committed, including untracked source files')
    inputs = {}
    for relative in PROJECT_INPUTS:
        # Comparing the actual bytes with HEAD also detects changes hidden by
        # skip-worktree/assume-unchanged index flags or ignored untracked files.
        actual = facts(repo / relative)
        committed = run(command + ['show', f'{head}:{relative}'], env=env)
        require(actual == {'bytes': len(committed), 'sha256': hashlib.sha256(committed).hexdigest()},
                f'Project input differs from commit: {relative}')
        inputs[relative] = actual
    return {'commit': head, 'git_status_porcelain': status, 'inputs': inputs}


def replay_source_provenance(project, artifacts):
    return {
        'schema': 1, 'project': project,
        'recipe': project['inputs']['build/uboot-replay.py'],
        'registered_target': TARGET, 'authenticated_full_manifest_sha256': MANIFEST_SHA,
        'original_uboot_head': UBOOT_HEAD, 'source_date_epoch': 1772630733,
        'scm_suffix': '-gb791be8-dirty', 'make_dirty': 'dirty',
        'applied_patches': {name: facts(artifacts / name) for name in (
            'uboot-env-nowhere-size.patch', 'uboot-a733-el2-handoff.patch',
            'uboot-challenge-replay.patch')},
        'replay_challenge_c': facts(artifacts / 'original-uboot-sunxi-challenge.c'),
        'challenge_classification': 'Authenticated public retained input; opaque header consumer security audit remains open',
    }


def trusted_provenance(repo, provenance):
    evidence = json.loads(read_regular(repo / 'docs/evidence/el2-diagnostic-20261104.json'))
    require(evidence['full_manifest_sha256'] == MANIFEST_SHA, 'Unregistered provenance anchor')
    manifest_path = provenance / 'artifact-manifest.json'
    raw = read_regular(manifest_path)
    require(hashlib.sha256(raw).hexdigest() == MANIFEST_SHA, 'Unauthenticated provenance manifest')
    manifest = json.loads(raw)
    for name in ('boot_package.fex', 'uboot.config'):
        require(manifest['files'][name] == evidence['files'][name], 'Core manifest disagrees with committed evidence')
    for name in RETAINED:
        authenticate(provenance / name, manifest['files'][name])
    validate_challenge(read_regular(provenance / 'uboot-sunxi-challenge.c'))
    config = read_regular(provenance / 'uboot.config')
    require(b'CONFIG_SUNXI_MIPS=y' not in config, 'Unexpected SUNXI_MIPS configuration')
    return manifest


def validate_inputs(repo, workspace, provenance, manifest):
    """Return complete authenticated mutable-input snapshot; repeat before success."""
    lock = json.loads(read_regular(repo / 'configs/diagnostic-sources.lock.json'))
    require(lock['uboot']['commit'] == UBOOT_HEAD, 'Unexpected U-Boot revision')
    snapshot = {'project_source': project_source(repo)}
    for name in ('diagnostic-sources.lock.json', 'vendor-boot-inputs.sha256', 'builder-packages.lock.tsv'):
        path = repo / 'configs' / name
        snapshot[str(path)] = facts(path)
    for key, directory in (('vendor', 'vendor'), ('uboot', 'u-boot'), ('kernel', 'kernel')):
        tree = safe_path(workspace / directory)
        env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
        head = run(['git', '--no-optional-locks', '-C', tree, 'rev-parse', 'HEAD'], env=env).decode().strip()
        require(head == lock[key]['commit'], f'Unpinned {directory} HEAD')
        require(not run(['git', '--no-optional-locks', '-C', tree, 'status', '--porcelain', '--untracked-files=no'], env=env),
                f'Tracked changes in {directory}')
        snapshot[f'git:{directory}'] = head
    lines = read_regular(repo / 'configs/vendor-boot-inputs.sha256').decode().splitlines()
    require(len(lines) == 26, 'Expected 26 vendor inputs')
    paths = set()
    for line in lines:
        expected, relative = line.split()
        require(re.fullmatch('[0-9a-f]{64}', expected) and not Path(relative).is_absolute() and '..' not in Path(relative).parts,
                'Invalid vendor lock entry')
        path = workspace / 'vendor' / relative
        require(path not in paths, 'Duplicate vendor input')
        paths.add(path)
        snapshot[str(path)] = facts(path)
        require(snapshot[str(path)]['sha256'] == expected, f'Untrusted vendor input: {relative}')
    archive = workspace / 'downloads' / lock['uboot_toolchain']['filename']
    snapshot[str(archive)] = facts(archive)
    require(snapshot[str(archive)]['sha256'] == lock['uboot_toolchain']['sha256'], 'Untrusted compiler archive')
    evidence = json.loads(read_regular(repo / 'docs/evidence/el2-diagnostic-20261104.json'))
    for patch, expected in [(repo / item['path'], item['sha256']) for item in lock['patches']] + [
            (repo / 'build/patches/uboot-a733-el2-handoff.patch',
             evidence['sources']['build/patches/uboot-a733-el2-handoff.patch']['sha256'])]:
        snapshot[str(patch)] = facts(patch)
        require(snapshot[str(patch)]['sha256'] == expected, 'Untrusted source patch')
        require(snapshot[str(patch)] == manifest['files'][patch.name], 'Patch disagrees with retained provenance')
    authenticate(provenance / 'artifact-manifest.json', {'bytes': (provenance / 'artifact-manifest.json').stat().st_size,
                                                        'sha256': MANIFEST_SHA})
    for name in RETAINED:
        authenticate(provenance / name, manifest['files'][name])
        snapshot[str(provenance / name)] = manifest['files'][name]
    for directory in (workspace, repo / 'out'):
        require(shutil.disk_usage(directory).free > 4_000_000_000, f'Need 4 GB free: {directory}')
    return snapshot, lock


def check_builder(repo):
    release = dict(line.split('=', 1) for line in read_regular(Path('/usr/lib/os-release')).decode().splitlines() if '=' in line)
    require(release.get('ID', '').strip('"') == 'ubuntu' and release.get('VERSION_ID', '').strip('"') == '22.04'
            and platform.machine() == 'x86_64', 'Requires recorded Ubuntu 22.04 x86_64 builder')
    require(run(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${Architecture}\n']) ==
            read_regular(repo / 'configs/builder-packages.lock.tsv'), 'Builder package inventory differs')


def build(repo, workspace, provenance, output, manifest, before, lock):
    output.mkdir()
    artifacts = output / 'artifacts'
    logs = output / 'logs'
    artifacts.mkdir()
    logs.mkdir()
    stop = threading.Event()

    def monitor():
        with (logs / 'resource-samples.txt').open('wb') as stream:
            while not stop.is_set():
                stream.write((time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) + '\n').encode())
                for cmd in (['free', '-b'], ['df', '-B1', workspace, output]):
                    run(cmd, log=stream)
                stream.flush()
                stop.wait(30)
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    env = dict(os.environ, LC_ALL='C', TZ='UTC', SOURCE_DATE_EPOCH='1793750400',
               KBUILD_BUILD_TIMESTAMP='2026-11-04 00:00:00 UTC', KBUILD_BUILD_USER='builder',
               KBUILD_BUILD_HOST='jammy', KBUILD_BUILD_VERSION='1')
    for key in ('MAKEFLAGS', 'MFLAGS', 'MAKEOVERRIDES', 'A733_REPLAY_CHALLENGE'):
        env.pop(key, None)
    try:
        (artifacts / 'input-snapshot.json').write_text(json.dumps(before, indent=2) + '\n')
        for name in RETAINED + ('artifact-manifest.json',):
            destination = artifacts / ('original-' + name)
            shutil.copyfile(provenance / name, destination)
            if name != 'artifact-manifest.json':
                authenticate(destination, manifest['files'][name])
            else:
                require(facts(destination)['sha256'] == MANIFEST_SHA, 'Copied provenance manifest mismatch')
        challenge = artifacts / 'original-uboot-sunxi-challenge.c'
        validate_challenge(read_regular(challenge))
        for name in ('diagnostic-sources.lock.json', 'vendor-boot-inputs.sha256', 'builder-packages.lock.tsv'):
            shutil.copyfile(repo / 'configs' / name, artifacts / name)
            authenticate(artifacts / name, before[str(repo / 'configs' / name)])
        for relative, destination in [('docs/evidence/el2-diagnostic-20261104.json', 'registered-target.json'),
                                      ('build/uboot-replay.py', 'uboot-replay.py')]:
            shutil.copyfile(repo / relative, artifacts / destination)
            authenticate(artifacts / destination, before['project_source']['inputs'][relative])
        archive = artifacts / 'uboot-pinned-source.tar'
        with archive.open('wb') as stream:
            run(['git', '--no-optional-locks', '-C', workspace / 'u-boot', 'archive', '--format=tar', UBOOT_HEAD], log=stream)
        tree = output / 'uboot-source'
        tree.mkdir()
        run(['tar', '-xf', archive, '-C', tree])
        run(['git', '-C', tree, 'init', '-q'])
        (tree / '.scmversion').write_text('-gb791be8-dirty\n')
        shutil.copyfile(tree / '.scmversion', artifacts / 'uboot.scmversion')
        for name in ('uboot-env-nowhere-size.patch', 'uboot-a733-el2-handoff.patch'):
            shutil.copyfile(repo / 'build/patches' / name, artifacts / name)
            authenticate(artifacts / name, manifest['files'][name])
            run(['git', '-C', tree, 'apply', artifacts / name])
        makefile = tree / 'Makefile'
        original = makefile.read_text()
        modified = patch_generator(original)
        replay_patch = ''.join(difflib.unified_diff(original.splitlines(True), modified.splitlines(True),
                                                   fromfile='a/Makefile', tofile='b/Makefile'))
        (artifacts / 'uboot-challenge-replay.patch').write_text(replay_patch)
        run(['git', '-C', tree, 'apply', artifacts / 'uboot-challenge-replay.patch'])
        source_provenance = replay_source_provenance(before['project_source'], artifacts)
        (artifacts / 'source-provenance.json').write_text(json.dumps(source_provenance, indent=2) + '\n')
        compiler_root = output / 'toolchain-private'
        compiler_root.mkdir()
        compiler_archive = artifacts / lock['uboot_toolchain']['filename']
        shutil.copyfile(workspace / 'downloads' / compiler_archive.name, compiler_archive)
        authenticate(compiler_archive, before[str(workspace / 'downloads' / compiler_archive.name)])
        run(['tar', '-xJf', compiler_archive, '-C', compiler_root])
        cross = compiler_root / lock['uboot_toolchain']['filename'].removesuffix('.tar.xz') / 'bin/arm-linux-gnueabi-'
        require(run([str(cross) + 'gcc', '-dumpfullversion']).strip() == b'7.4.1', 'Wrong compiler version')
        (logs / 'uboot-compiler.txt').write_bytes(run([str(cross) + 'gcc', '--version']))
        (tree / 'vendor-export/bin').mkdir(parents=True)
        make = ['make', 'DIRTY=dirty', 'SOURCE_DATE_EPOCH=1772630733', f'CROSS_COMPILE={cross}',
                f'LICHEE_CHIP_CONFIG_DIR={tree}/vendor-export', 'LICHEE_BUSSINESS=',
                f'LICHEE_PLAT_OUT={tree}/vendor-export', f'A733_REPLAY_CHALLENGE={challenge}']
        with (logs / 'uboot-build.log').open('wb') as log:
            run(make + ['-j2', 'sun60iw2p1_t736_defconfig'], cwd=tree, env=env, log=log)
            run([workspace / 'kernel/scripts/config', '--file', tree / '.config', '--disable', 'ENV_IS_IN_EXT4',
                 '--enable', 'ENV_IS_NOWHERE', '--disable', 'CMD_SAVEENV', '--enable', 'BOOTCMD_SKIP_MISC',
                 '--disable', 'SUNXI_SWITCH_SYSTEM', '--disable', 'BOOTCOUNT_LIMIT', '--set-str', 'BOOTCOMMAND',
                 'setenv boot_targets mmc0; run distro_bootcmd', '--disable', 'SUNXI_OTA_TURNNING'], env=env, log=log)
            run(make + ['olddefconfig'], cwd=tree, env=env, log=log)
            authenticate(tree / '.config', manifest['files']['uboot.config'])
            run(make + ['-j2'], cwd=tree, env=env, log=log)
        for source, destination in [('board/sunxi/sunxi_challenge.c', 'uboot-sunxi-challenge.c'),
                                    ('u-boot.bin', 'uboot-unpacked.bin'), ('.config', 'uboot.config'),
                                    ('u-boot', 'uboot.elf'), ('u-boot.map', 'uboot.map')]:
            shutil.copyfile(tree / source, artifacts / destination)
        authenticate(artifacts / 'uboot-sunxi-challenge.c', manifest['files']['uboot-sunxi-challenge.c'])
        authenticate(artifacts / 'uboot.config', manifest['files']['uboot.config'])
        pack = artifacts / 'pack'
        pack.mkdir()
        tools = output / 'pack-tools'
        tools.mkdir()
        prefix = 'external/packages/pack-uboot/'
        for line in read_regular(artifacts / 'vendor-boot-inputs.sha256').decode().splitlines():
            checksum, relative = line.split()
            source = workspace / 'vendor' / relative
            if relative.startswith(prefix + 'sun60iw2/bin/'):
                destination = pack / relative.removeprefix(prefix + 'sun60iw2/bin/')
            else:
                destination = tools / Path(relative).name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            require(facts(destination)['sha256'] == checksum, 'Private pack copy hash mismatch')
        for source, destination in [('boot0_sdcard_a733.fex', 'boot0_sdcard.fex'),
                                    ('sys_config/sys_config.fex', 'sys_config.fex'),
                                    ('dts/u-boot-current.dts', 'dts/orangepi4pro-u-boot.dts')]:
            shutil.copyfile(pack / source, pack / destination)
        shutil.copyfile(tree / 'u-boot.bin', pack / 'u-boot.fex')
        env['PATH'] = str(tools) + ':' + env['PATH']
        with (logs / 'boot-pack.log').open('wb') as log:
            for command in [
                ['dtc', '-p', '2048', '-W', 'no-unit_address_vs_reg', '-@', '-O', 'dtb', '-o', 'orangepi4pro-u-boot.dtb', '-b', '0', 'dts/u-boot-current.dts'],
                ['busybox', 'unix2dos', 'sys_config.fex'], ['script', 'sys_config.fex'],
                ['cp', 'orangepi4pro-u-boot.dtb', 'sunxi.fex'], ['update_dtb', 'sunxi.fex', '4096'],
                ['update_uboot', '-no_merge', 'u-boot.fex', 'sys_config.bin'],
                ['busybox', 'unix2dos', 'boot_package.cfg'], ['dragonsecboot', '-pack', 'boot_package.cfg']]:
                run(command, cwd=pack, env=env, log=log)
        shutil.copyfile(pack / 'boot_package.fex', artifacts / 'boot_package.fex')
        after, _ = validate_inputs(repo, workspace, provenance, manifest)
        require(after == before, 'Shared inputs changed during replay')
        check_builder(repo)
        comparisons = {name: facts(artifacts / name) == manifest['files'][name]
                       for name in ('uboot-unpacked.bin', 'boot_package.fex')}
        result = {'schema': 1, 'target': TARGET, 'comparisons': comparisons,
                  'success': all(comparisons.values()), 'hardware_acceptance': 'Not established by replay',
                  'limitations': 'Reused pinned source caches/kernel; not a full clean builder. Logs/manifests are not claimed bit-identical. Opaque challenge header consumer security audit remains open.'}
        (artifacts / 'replay-result.json').write_text(json.dumps(result, indent=2) + '\n')
        require(result['success'], 'Replay mismatch; retained artifacts and comparison report are available')
    finally:
        stop.set()
        thread.join()
        # Keep all evidence, including failed builds and compiler, for review.
        entries = {str(path.relative_to(output)): facts(path) for directory in (artifacts, logs)
                   for path in sorted(directory.rglob('*')) if path.is_file()}
        (output / 'replay-manifest.json').write_text(json.dumps({'schema': 1, 'files': entries}, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True, choices=[TARGET])
    parser.add_argument('--workspace', type=Path, default=WORKSPACE)
    parser.add_argument('--provenance', type=Path, default=PROVENANCE)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        require(args.workspace == WORKSPACE and args.provenance == PROVENANCE, 'Only registered workspace/provenance paths accepted')
        validate_output(REPO, args.workspace, args.provenance, args.output)
        manifest = trusted_provenance(REPO, args.provenance)
        before, lock = validate_inputs(REPO, args.workspace, args.provenance, manifest)
        check_builder(REPO)
        build(REPO, args.workspace, args.provenance, args.output, manifest, before, lock)
    except (ValueError, OSError, subprocess.CalledProcessError, KeyError, UnicodeError) as exc:
        print(f'Replay refused/failed: {exc}', file=sys.stderr)
        return 1
    print(f'Replayed unpacked U-Boot and boot package match registered bytes: {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

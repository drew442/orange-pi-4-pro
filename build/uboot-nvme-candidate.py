#!/usr/bin/env python3
"""Build one private A733 NVMe U-Boot component candidate, without deployment.

The pinned distro macros retain their broader partition/prefix search behavior.
A successful component build establishes neither SPI boot nor hardware acceptance.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time

REPO = Path(__file__).resolve().parent.parent
POLICY = 'configs/uboot-nvme-candidate-v1.json'
CORE_SHA = '3558c6b75d0fdfa731eaff925b101a136b288953364996f72b8a6bcd3fcb1cfd'
PROFILE = 'replay-20261106'
BOOTCOMMAND = 'setenv boot_targets nvme; run distro_bootcmd'
BASE_COMMAND = b'CONFIG_BOOTCOMMAND="setenv boot_targets mmc0; run distro_bootcmd"\n'
NEW_COMMAND = ('CONFIG_BOOTCOMMAND="' + BOOTCOMMAND + '"\n').encode()
OWN_INPUTS = ('build/uboot-nvme-candidate.py', POLICY)
SPI_BOOT0 = {'bytes': 237568, 'sha256': '7f0babc242c1e4614c7243bd2c45a980e4385727ffbb544a7c8f2d324cf8dc07'}


def load_core(repo):
    # Authenticate bytes before executing imported helpers. Git authentication
    # and a second hash check follow; the old recipe remains entirely unchanged.
    path = repo / 'build/uboot-replay.py'
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('Core recipe symlink forbidden')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Core recipe must be regular')
        raw = stream.read()
    if hashlib.sha256(raw).hexdigest() != CORE_SHA:
        raise ValueError('Untrusted replay core')
    spec = importlib.util.spec_from_file_location('authenticated_uboot_replay', path)
    module = importlib.util.module_from_spec(spec)
    # Execute exactly the bytes authenticated above, avoiding a second load or pyc.
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


core = load_core(REPO)
require = core.require


def controlled_env():
    return {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent',
            'LC_ALL': 'C', 'TZ': 'UTC', 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0',
            'SOURCE_DATE_EPOCH': '1772630733',
            'KBUILD_BUILD_TIMESTAMP': '2026-11-04 00:00:00 UTC',
            'KBUILD_BUILD_USER': 'builder', 'KBUILD_BUILD_HOST': 'jammy',
            'KBUILD_BUILD_VERSION': '1'}


def validate_output(repo, workspace, provenance, output):
    # Reuse all original physical-path/overlap rules, changing only the name.
    for path in (repo, workspace, provenance, output):
        core.safe_path(path)
    require(workspace.is_dir() and provenance.is_dir(), 'Missing source/provenance directory')
    require(output.parent == repo / 'out' and output.parent.is_dir(), 'Output must be directly under repository out/')
    require(re.fullmatch(r'uboot-nvme-candidate-[A-Za-z0-9][A-Za-z0-9._-]*', output.name),
            'Use uboot-nvme-candidate-NAME output')
    require(re.fullmatch(r'[A-Za-z0-9/._-]+', str(output)), 'Output spelling unsafe for Make')
    require(not os.path.lexists(output), 'Output already exists')
    for source in (workspace, provenance):
        require(source != output and source not in output.parents and output not in source.parents,
                'Input/output overlap forbidden')
    require(workspace != provenance and workspace not in provenance.parents and provenance not in workspace.parents,
            'Source/provenance overlap forbidden')


def project_source(repo):
    project = core.project_source(repo)
    command = ['git', '--no-optional-locks', '-C', repo, 'show']
    for relative in OWN_INPUTS:
        actual = core.facts(repo / relative)
        committed = core.run(command + [f'{project["commit"]}:{relative}'], env=controlled_env())
        require(actual == {'bytes': len(committed), 'sha256': hashlib.sha256(committed).hexdigest()},
                f'Project input differs from commit: {relative}')
        project['inputs'][relative] = actual
    require(project['inputs']['build/uboot-replay.py']['sha256'] == CORE_SHA, 'Core Git pin mismatch')
    return project


def validate_policy(repo):
    policy = json.loads(core.read_regular(repo / POLICY))
    expected = {'schema': 1, 'candidate': 'a733-nvme-v1', 'builder_profile': PROFILE,
                'core_recipe_sha256': CORE_SHA, 'bootcommand': BOOTCOMMAND,
                'source_date_epoch': 1772630733, 'scm_suffix': '-gb791be8-dirty',
                'challenge': 'vendor-fresh-random-128-bytes', 'spi_boot0': SPI_BOOT0,
                'search_behavior': 'Inherits pinned distro bootable partition search (fallback partition 1), prefixes / and /boot/, script and extlinux behavior. This is not a closed whole-firmware partition policy.',
                'hardware_acceptance': False}
    require(policy == expected, 'Unregistered candidate policy')
    return policy


def candidate_config(original):
    require(original.count(BASE_COMMAND) == 1, 'Expected exact retained SD BOOTCOMMAND')
    require(sum(line.startswith(b'CONFIG_BOOTCOMMAND=') for line in original.splitlines()) == 1,
            'Duplicate BOOTCOMMAND')
    expected = original.replace(BASE_COMMAND, NEW_COMMAND)
    for line in (b'CONFIG_ENV_IS_NOWHERE=y', b'# CONFIG_CMD_SAVEENV is not set',
                 b'CONFIG_BOOTCMD_SKIP_MISC=y', b'# CONFIG_SUNXI_SWITCH_SYSTEM is not set',
                 b'# CONFIG_BOOTCOUNT_LIMIT is not set', b'# CONFIG_SUNXI_OTA_TURNNING is not set'):
        require(line in expected.splitlines(), f'Missing immutable boot safeguard: {line!r}')
    require(b'CONFIG_ENV_IS_IN_EXT4=y' not in expected, 'Writable environment forbidden')
    return expected


def validate_config(actual, retained):
    require(actual == candidate_config(retained), 'Candidate config differs beyond the one BOOTCOMMAND line')


def snapshot(repo, workspace, provenance, manifest):
    before, lock = core.validate_inputs(repo, workspace, provenance, manifest, PROFILE)
    before['project_source'] = project_source(repo)
    before['candidate_policy'] = validate_policy(repo)
    require(core.check_builder(repo, PROFILE) == before['builder_profile'], 'Builder inventory mismatch')
    return before, lock


def capacity_budget(workspace, lock):
    """Conservative one-candidate construction bound, excluding shared caches.

    Source contents/member counts come from pinned Git and compiler expansion
    from authenticated tar headers. Fixed allowances bound build/pack outputs
    and logs. Reserve strictly more than 4 GB for the unchanged final input
    validation, in addition to the full estimated construction. The live floor
    still aborts growth beyond this planning estimate.
    """
    archive = workspace / 'downloads' / lock['uboot_toolchain']['filename']
    require(core.facts(archive)['sha256'] == lock['uboot_toolchain']['sha256'],
            'Untrusted compiler archive for budget')
    listing = core.run(['git', '--no-optional-locks', '-C', workspace / 'u-boot',
                        'ls-tree', '-r', '-l', '-z', core.UBOOT_HEAD], env=controlled_env())
    sizes = []
    for entry in listing.split(b'\0'):
        if not entry:
            continue
        metadata, _ = entry.split(b'\t', 1)
        mode, kind, object_id, size = metadata.split()
        require(kind == b'blob' and size.isdigit(), 'Unexpected source budget entry')
        sizes.append(int(size))
    require(sizes, 'Empty source tree budget')
    source_nodes = len(sizes) + len(sizes)  # bound directory count by leaf count
    source_allocated = sum(((size + 4095) // 4096) * 4096 for size in sizes) + source_nodes * 4096
    source_archive = sum(sizes) + len(sizes) * 1024 + 10240
    compiler_expanded = 0
    compiler_nodes = 0
    with tarfile.open(archive, 'r:xz') as stream:
        for member in stream:
            require(not member.name.startswith('/') and '..' not in Path(member.name).parts,
                    'Unsafe compiler member in budget')
            compiler_nodes += 1
            compiler_expanded += ((member.size + 4095) // 4096) * 4096 + 4096
    require(compiler_nodes > 0, 'Empty compiler archive')
    # All 26 pack inputs are authenticated by snapshot(); include a full second
    # copy for intermediates, plus 32 MiB package/DTB output allowance.
    pack_inputs = sum(core.facts(workspace / 'vendor' / line.split()[1])['bytes']
                      for line in core.read_regular(REPO / 'configs/vendor-boot-inputs.sha256').decode().splitlines())
    parts = {'source_archive_upper_bytes': source_archive,
             'source_expansion_allocation_bytes': source_allocated,
             'compiler_archive_copy_bytes': archive.stat().st_size,
             'compiler_expansion_allocation_bytes': compiler_expanded,
             'objects_and_compiler_outputs_allowance_bytes': 256 * 1024 * 1024,
             'private_pack_and_outputs_allowance_bytes': 2 * pack_inputs + 32 * 1024 * 1024,
             'logs_and_provenance_allowance_bytes': 64 * 1024 * 1024}
    estimated = sum(parts.values())
    return {'schema': 1, 'parts': parts, 'source_members': len(sizes),
            'compiler_members': compiler_nodes, 'estimated_construction_bytes': estimated,
            'required_free_bytes': 4_000_000_001 + estimated,
            'final_validation_disk_reserve_bytes': 4_000_000_001,
            'runtime_disk_floor_bytes': 3_000_000_000, 'runtime_ram_floor_bytes': 1_000_000_000,
            'scope': 'One component construction; outer source staging is additional. Fixed allowances are estimates guarded by live sampling.'}


def check_capacity(workspace, output, budget):
    for path in (workspace, output.parent):
        free = shutil.disk_usage(path).free
        require(free > budget['required_free_bytes'],
                f"Candidate construction needs >{budget['required_free_bytes']} free bytes: {path} has {free}")


def make_command(tree, cross):
    return ['make', 'DIRTY=dirty', 'SOURCE_DATE_EPOCH=1772630733', f'CROSS_COMPILE={cross}',
            f'LICHEE_CHIP_CONFIG_DIR={tree}/vendor-export', 'LICHEE_BUSSINESS=',
            f'LICHEE_PLAT_OUT={tree}/vendor-export', '-j2']


class Runner:
    """Sample real resources and terminate the running process group on a floor breach."""
    def __init__(self, workspace, output):
        self.workspace, self.output = workspace, output
        self.resource_log = output / 'logs/resource-samples.jsonl'
        self.command_count = 0

    def sample(self):
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        available = int(memory['MemAvailable'].split()[0]) * 1024
        free = {str(path): shutil.disk_usage(path).free for path in (self.workspace, self.output)}
        row = {'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
               'disk_free_bytes': free, 'memory_available_bytes': available}
        with self.resource_log.open('a') as stream:
            stream.write(json.dumps(row) + '\n')
        require(min(free.values()) >= 3_000_000_000, 'Runtime free disk below 3 GB')
        require(available >= 1_000_000_000, 'Runtime available RAM below 1 GB')

    def run(self, command, cwd=None, env=None, log=None):
        self.sample()
        env = dict(env or controlled_env())
        if cwd is not None:
            env['PWD'] = str(cwd)
        # File-backed capture avoids deadlocking on full stdout pipes and retains
        # failures even for commands whose result is used as data.
        capture = None
        self.command_count += 1
        with (self.output / 'logs/commands.jsonl').open('a') as stream:
            stream.write(json.dumps({'command': list(map(str, command)), 'cwd': str(cwd),
                                     'capture': self.command_count}) + '\n')
        if log is None:
            capture = (self.output / f'logs/command-{self.command_count:04d}.log').open('w+b')
        destination = log if log is not None else capture
        try:
            process = subprocess.Popen(list(map(str, command)), cwd=cwd, env=env,
                                       stdout=destination, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                while True:
                    try:
                        code = process.wait(timeout=30)
                        break
                    except subprocess.TimeoutExpired:
                        self.sample()
                self.sample()
                if code:
                    raise subprocess.CalledProcessError(code, command)
            except BaseException:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise
            if capture is not None:
                capture.seek(0)
                return capture.read()
        finally:
            if capture is not None:
                capture.close()


def private_pack(workspace, artifacts, output):
    pack, tools = artifacts / 'pack', output / 'pack-tools'
    pack.mkdir()
    tools.mkdir()
    prefix = 'external/packages/pack-uboot/'
    for line in core.read_regular(artifacts / 'vendor-boot-inputs.sha256').decode().splitlines():
        checksum, relative = line.split()
        destination = (pack / relative.removeprefix(prefix + 'sun60iw2/bin/')
                       if relative.startswith(prefix + 'sun60iw2/bin/') else tools / Path(relative).name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / 'vendor' / relative, destination)
        require(core.facts(destination)['sha256'] == checksum, 'Private pack input mismatch')
    core.authenticate(pack / 'boot0_spinor_a733.fex', SPI_BOOT0)
    return pack, tools


def verify_firmware(pack, artifacts, expected):
    for name in ('monitor.fex', 'scp.fex', 'boot0_spinor_a733.fex'):
        core.authenticate(pack / name, expected[name])
    shutil.copyfile(pack / 'boot0_spinor_a733.fex', artifacts / 'boot0_spinor_a733.fex')
    core.authenticate(artifacts / 'boot0_spinor_a733.fex', SPI_BOOT0)


def pack_commands():
    return [
        ['dtc', '-p', '2048', '-W', 'no-unit_address_vs_reg', '-@', '-O', 'dtb', '-o', 'orangepi4pro-u-boot.dtb', '-b', '0', 'dts/u-boot-current.dts'],
        ['busybox', 'unix2dos', 'sys_config.fex'], ['script', 'sys_config.fex'],
        ['cp', 'orangepi4pro-u-boot.dtb', 'sunxi.fex'], ['update_dtb', 'sunxi.fex', '4096'],
        ['update_uboot', '-no_merge', 'u-boot.fex', 'sys_config.bin'],
        ['busybox', 'unix2dos', 'boot_package.cfg'], ['dragonsecboot', '-pack', 'boot_package.cfg']]


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def build(repo, workspace, provenance, output, manifest, before, lock):
    budget = capacity_budget(workspace, lock)
    check_capacity(workspace, output, budget)
    output.mkdir()
    artifacts, logs = output / 'artifacts', output / 'logs'
    artifacts.mkdir()
    logs.mkdir()
    runner = Runner(workspace, output)
    run = runner.run
    result = {'schema': 1, 'candidate': 'a733-nvme-v1', 'component_build_success': False,
              'hardware_acceptance': False, 'deployment_authorized': False}
    try:
        write_json(artifacts / 'capacity-budget.json', budget)
        write_json(artifacts / 'input-snapshot.json', before)
        for relative, expected in before['project_source']['inputs'].items():
            destination = artifacts / 'project-inputs' / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo / relative, destination)
            core.authenticate(destination, expected)
        for name in core.RETAINED + ('artifact-manifest.json',):
            shutil.copyfile(provenance / name, artifacts / ('original-' + name))
            expected = (manifest['files'][name] if name != 'artifact-manifest.json' else
                        {'bytes': (provenance / name).stat().st_size, 'sha256': core.MANIFEST_SHA})
            core.authenticate(artifacts / ('original-' + name), expected)
        for name in ('diagnostic-sources.lock.json', 'vendor-boot-inputs.sha256', *core.BUILDER_PROFILES.values()):
            shutil.copyfile(repo / 'configs' / name, artifacts / name)
            core.authenticate(artifacts / name, before[str(repo / 'configs' / name)])
        archive = artifacts / 'uboot-pinned-source.tar'
        with archive.open('wb') as stream:
            run(['git', '--no-optional-locks', '-C', workspace / 'u-boot', 'archive', '--format=tar', core.UBOOT_HEAD], log=stream)
        tree = output / 'uboot-source'
        tree.mkdir()
        run(['tar', '-xf', archive, '-C', tree])
        run(['git', '-C', tree, 'init', '-q'])
        (tree / '.scmversion').write_text('-gb791be8-dirty\n')
        for name in ('uboot-env-nowhere-size.patch', 'uboot-a733-el2-handoff.patch'):
            shutil.copyfile(repo / 'build/patches' / name, artifacts / name)
            core.authenticate(artifacts / name, manifest['files'][name])
            run(['git', '-C', tree, 'apply', artifacts / name])
        makefile = core.read_regular(tree / 'Makefile').decode()
        require(makefile.count(core.GENERATOR) == 1 and 'A733_REPLAY_CHALLENGE' not in makefile,
                'Fresh vendor random generator changed')
        require(not (tree / 'board/sunxi/sunxi_challenge.c').exists(), 'Unexpected pregenerated challenge')
        compiler_root = output / 'toolchain-private'
        compiler_root.mkdir()
        compiler_archive = artifacts / lock['uboot_toolchain']['filename']
        shutil.copyfile(workspace / 'downloads' / compiler_archive.name, compiler_archive)
        core.authenticate(compiler_archive, before[str(workspace / 'downloads' / compiler_archive.name)])
        run(['tar', '-xJf', compiler_archive, '-C', compiler_root])
        cross = compiler_root / compiler_archive.name.removesuffix('.tar.xz') / 'bin/arm-linux-gnueabi-'
        require(run([str(cross) + 'gcc', '-dumpfullversion']).strip() == b'7.4.1', 'Wrong compiler version')
        (logs / 'uboot-compiler.txt').write_bytes(run([str(cross) + 'gcc', '--version']))
        (tree / 'vendor-export/bin').mkdir(parents=True)
        make = make_command(tree, cross)
        retained = core.read_regular(artifacts / 'original-uboot.config')
        with (logs / 'uboot-build.log').open('wb') as log:
            run(make + ['sun60iw2p1_t736_defconfig'], cwd=tree, log=log)
            # Start from authenticated retained configuration, changing exactly one
            # line. olddefconfig may normalize, but must leave these exact bytes.
            (tree / '.config').write_bytes(candidate_config(retained))
            run(make + ['olddefconfig'], cwd=tree, log=log)
            validate_config(core.read_regular(tree / '.config'), retained)
            run(make, cwd=tree, log=log)
        validate_config(core.read_regular(tree / '.config'), retained)
        core.validate_challenge(core.read_regular(tree / 'board/sunxi/sunxi_challenge.c'))
        require((tree / 'Makefile').read_text() == makefile, 'Challenge generator changed during build')
        for source, destination in [('board/sunxi/sunxi_challenge.c', 'uboot-sunxi-challenge.c'),
                                    ('u-boot.bin', 'uboot-unpacked.bin'), ('.config', 'uboot.config'),
                                    ('u-boot', 'uboot.elf'), ('u-boot.map', 'uboot.map'),
                                    ('arch/arm/lib/bootm.c', 'bootm.c'), ('arch/arm/lib/bootm.o', 'bootm.o')]:
            shutil.copyfile(tree / source, artifacts / destination)
        require(b'0x8000ff40' in core.read_regular(artifacts / 'bootm.c'), 'Missing reviewed EL2 service')
        with (artifacts / 'bootm.disassembly').open('wb') as stream:
            run([str(cross) + 'objdump', '-dr', tree / 'arch/arm/lib/bootm.o'], log=stream)
        pack, tools = private_pack(workspace, artifacts, output)
        firmware = {name: core.facts(pack / name) for name in ('monitor.fex', 'scp.fex', 'boot0_spinor_a733.fex')}
        for source, destination in [('boot0_sdcard_a733.fex', 'boot0_sdcard.fex'),
                                    ('sys_config/sys_config.fex', 'sys_config.fex'),
                                    ('dts/u-boot-current.dts', 'dts/orangepi4pro-u-boot.dts')]:
            shutil.copyfile(pack / source, pack / destination)
        shutil.copyfile(artifacts / 'uboot-unpacked.bin', pack / 'u-boot.fex')
        env = controlled_env()
        env['PATH'] = str(tools) + ':' + env['PATH']
        with (logs / 'boot-pack.log').open('wb') as log:
            for command in pack_commands():
                run(command, cwd=pack, env=env, log=log)
        verify_firmware(pack, artifacts, firmware)
        shutil.copyfile(pack / 'boot_package.fex', artifacts / 'boot_package.fex')
        require(core.facts(artifacts / 'boot_package.fex')['bytes'] > 0, 'Empty boot package')
        after, _ = snapshot(repo, workspace, provenance, manifest)
        require(after == before, 'Shared inputs or builder inventory changed during candidate build')
        write_json(artifacts / 'source-provenance.json', {
            'schema': 1, 'project': before['project_source'], 'builder_profile': before['builder_profile'],
            'policy': before['candidate_policy'], 'configuration': core.facts(artifacts / 'uboot.config'),
            'fresh_generated_challenge': core.facts(artifacts / 'uboot-sunxi-challenge.c'),
            'baseline_manifest_sha256': core.MANIFEST_SHA, 'core_recipe_sha256': CORE_SHA,
            'firmware_unchanged': firmware,
            'limitations': 'Component candidate only; no SPI image, programming or hardware acceptance. Fresh challenge retained for a separate registered reproduction.'})
        result['component_build_success'] = True
    except BaseException as exc:
        result['failure'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        write_json(artifacts / 'candidate-result.json', result)
        entries = {str(path.relative_to(output)): core.facts(path) for directory in (artifacts, logs)
                   for path in sorted(directory.rglob('*')) if path.is_file()}
        write_json(output / 'candidate-manifest.json', {'schema': 1, 'files': entries,
                   'private_source_retained': 'uboot-source', 'hardware_acceptance': False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--provenance', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--builder-profile', required=True, choices=[PROFILE])
    args = parser.parse_args()
    # No inherited make/compiler, dynamic-loader, Python or Git overrides reach
    # helpers or subprocesses. The fixed outer launcher provides paths explicitly.
    os.environ.clear()
    os.environ.update(controlled_env())
    try:
        validate_output(REPO, args.workspace, args.provenance, args.output)
        project_source(REPO)
        validate_policy(REPO)
        manifest = core.trusted_provenance(REPO, args.provenance)
        before, lock = snapshot(REPO, args.workspace, args.provenance, manifest)
        build(REPO, args.workspace, args.provenance, args.output, manifest, before, lock)
    except (ValueError, OSError, subprocess.CalledProcessError, KeyError, UnicodeError) as exc:
        print(f'NVMe component candidate refused/failed: {exc}', file=sys.stderr)
        return 1
    print(f'Built NVMe U-Boot components for independent review: {args.output}; hardware acceptance remains open')
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Offline exact-artifact kernel replay in a new private Git checkout.

Reuses cached sources and the registered builder; establishes neither a fresh
builder nor hardware acceptance. Original sources and artifacts are never built
in, changed, or removed. Failed runs retain their private tree and evidence.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import signal
import stat
import subprocess
import sys
import threading
import time

SPEC = importlib.util.spec_from_file_location('kernel_replay_helpers', Path(__file__).with_name('uboot-replay.py'))
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)
require, safe_path, read_regular, facts = helper.require, helper.safe_path, helper.read_regular, helper.facts

REPO = Path(__file__).resolve().parent.parent
WORKSPACE = Path('/home/drew/orange-pi-4-pro-build')
PROFILE = 'replay-20261106'
TARGET = 'diagnostic-20261104'
DEBUG_PATH_PROFILES = ('native', 'original-20261104')
KERNEL_HEAD = '2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f'
VENDOR_HEAD = 'a95129bf30ea11886cc35f08ebb3e69695a26656'
CONFIG = 'external/config/kernel/linux-sun60iw2-current-a733.config'
PRODUCTS = {'Image': 'arch/arm64/boot/Image',
            'board.dtb': 'arch/arm64/boot/dts/allwinner/sun60i-a733-orangepi-4-pro.dtb',
            'kernel.config': '.config'}
PROJECT_INPUTS = ('build/kernel-replay.py', 'build/uboot-replay.py', 'build/diagnostic.sh',
                  'configs/diagnostic-sources.lock.json', 'configs/builder-packages.lock.tsv',
                  'configs/builder-replay-packages-20261106.lock.tsv',
                  'docs/evidence/diagnostic-20261104.json')


def environment():
    # No inherited Make, Git, compiler, include, linker, locale or user config.
    return {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C', 'TZ': 'UTC',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
            'GIT_OPTIONAL_LOCKS': '0', 'SOURCE_DATE_EPOCH': '1793750400',
            'KBUILD_BUILD_TIMESTAMP': '2026-11-04 00:00:00 UTC',
            'KBUILD_BUILD_USER': 'builder', 'KBUILD_BUILD_HOST': 'jammy',
            'KBUILD_BUILD_VERSION': '1'}


def run(command, cwd=None, log=None):
    return helper.run(command, cwd=cwd, env=environment(), log=log)


def git_command(tree, *args):
    tree = safe_path(tree)
    # Root may replay Drew-owned caches. Trust only this validated invocation's
    # exact tree; never inherit SUDO_UID or alter persistent Git configuration.
    trust = ['-c', f'safe.directory={tree}']
    if args and args[0] == 'clone':
        # Clone unsets GIT_CONFIG_PARAMETERS for upload-pack, which separately
        # validates source/.git. Give that read-only subprocess its exact trust.
        endpoint = safe_path(tree / '.git')
        upload_pack = shlex.join(['/usr/bin/git', '-c', f'safe.directory={endpoint}', 'upload-pack'])
        args = (args[0], f'--upload-pack={upload_pack}', *args[1:])
    return ['git', '--no-optional-locks', '-c', 'core.hooksPath=/dev/null',
            *trust, '-C', tree, *args]


def git(tree, *args):
    return run(git_command(tree, *args))


def project_source(repo):
    safe_path(repo / '.git')
    require((repo / '.git').is_dir(), 'Project requires full local .git directory')
    require(git(repo, 'rev-parse', '--show-toplevel').decode().strip() == str(repo), 'Wrong project Git root')
    require(git(repo, 'rev-parse', '--is-shallow-repository').strip() == b'false', 'Shallow project forbidden')
    head = git(repo, 'rev-parse', 'HEAD').decode().strip()
    require(re.fullmatch('[0-9a-f]{40}', head), 'Invalid project HEAD')
    status = git(repo, 'status', '--porcelain', '--untracked-files=all').decode()
    require(not status, 'Project must be clean and committed, including untracked files')
    inputs = {}
    for relative in PROJECT_INPUTS:
        committed = git(repo, 'show', f'{head}:{relative}')
        inputs[relative] = facts(repo / relative)
        require(inputs[relative] == {'bytes': len(committed), 'sha256': hashlib.sha256(committed).hexdigest()},
                f'Actual project input differs from HEAD: {relative}')
    return {'commit': head, 'git_status_porcelain': status, 'inputs': inputs}


def validate_output(repo, workspace, output):
    for path in (repo, workspace, output):
        safe_path(path)
    require(workspace.is_dir(), 'Missing registered workspace')
    require(output.parent == repo / 'out' and output.parent.is_dir(), 'Output must be directly under repository out/')
    require(re.fullmatch(r'kernel-replay-[A-Za-z0-9][A-Za-z0-9._-]*', output.name), 'Use kernel-replay-NAME output')
    require(re.fullmatch(r'[A-Za-z0-9/._-]+', str(output)), 'Unsafe Make output spelling')
    require(not os.path.lexists(output), 'Output already exists')
    require(output != workspace and workspace not in output.parents and output not in workspace.parents,
            'Input/output overlap forbidden')


def source_state(tree, expected):
    safe_path(tree / '.git')
    require((tree / '.git').is_dir(), f'Full source Git checkout required: {tree}')
    shallow = git(tree, 'rev-parse', '--is-shallow-repository').strip()
    require(shallow in (b'true', b'false'), 'Invalid source shallow metadata')
    head = git(tree, 'rev-parse', 'HEAD').decode().strip()
    require(head == expected, f'Unpinned source HEAD: {tree}')
    status = git(tree, 'status', '--porcelain', '--untracked-files=all').decode()
    require(not status, f'Source checkout must be clean: {tree}')
    return {'commit': head, 'git_status_porcelain': status, 'shallow': shallow == b'true'}


def module_inventory(root):
    """Sorted relative regular-file byte/mode inventory; reject unexpected links."""
    safe_path(root)
    require(root.is_dir(), f'Missing modules directory: {root}')
    inventory = []
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in sorted(directories + files):
            path = Path(directory) / name
            mode = path.lstat().st_mode
            require(not stat.S_ISLNK(mode), f'Unexpected module symlink: {path}')
            require(stat.S_ISDIR(mode) or stat.S_ISREG(mode), f'Unexpected module file type: {path}')
            if stat.S_ISREG(mode):
                inventory.append({'path': path.relative_to(root).as_posix(),
                                  'mode': stat.S_IMODE(mode), **facts(path)})
    return sorted(inventory, key=lambda entry: entry['path'])


def remove_generated_links(root):
    # Only these two generated symlinks, one release directory deep, are removed.
    for release in (root / 'lib/modules').iterdir():
        safe_path(release)
        require(release.is_dir(), 'Unexpected modules release entry')
        for name in ('build', 'source'):
            path = release / name
            if path.is_symlink():
                path.unlink()
            else:
                require(not os.path.lexists(path), f'Unexpected generated link type: {path}')


def snapshot(repo, workspace):
    project = project_source(repo)
    lock = json.loads(read_regular(repo / 'configs/diagnostic-sources.lock.json'))
    require(lock['kernel']['commit'] == KERNEL_HEAD and lock['vendor']['commit'] == VENDOR_HEAD,
            'Source lock differs from registered target')
    require(lock['source_date_epoch'] == 1793750400 and lock['kernel_compiler']['version'] == '11.4.0',
            'Kernel metadata differs from registered target')
    evidence = json.loads(read_regular(repo / 'docs/evidence/diagnostic-20261104.json'))
    targets = {name: evidence['files'][name] for name in PRODUCTS}
    for name, expected in targets.items():
        helper.authenticate(workspace / 'artifacts' / name, expected)
    kernel = source_state(workspace / 'kernel', KERNEL_HEAD)
    require(not os.path.lexists(workspace / 'kernel/.scmversion'), 'Original kernel .scmversion must be absent')
    vendor = source_state(workspace / 'vendor', VENDOR_HEAD)
    config = git(workspace / 'vendor', 'show', f'{VENDOR_HEAD}:{CONFIG}')
    require(run(['aarch64-linux-gnu-gcc', '-dumpfullversion']).strip() == b'11.4.0', 'Wrong kernel compiler')
    # Full exact profile checked on both sides, not just compiler/APT versions.
    release = dict(line.split('=', 1) for line in read_regular(Path('/usr/lib/os-release')).decode().splitlines() if '=' in line)
    require(release.get('ID', '').strip('"') == 'ubuntu' and
            release.get('VERSION_ID', '').strip('"') == '22.04' and helper.platform.machine() == 'x86_64',
            'Requires recorded Ubuntu 22.04 x86_64 builder')
    builder = helper.validate_inventory(repo, PROFILE,
                                       run(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${Architecture}\n']))
    require(len(helper.package_rows(read_regular(repo / builder['lock_file']))) == 842,
            'Expected complete 842-package replay inventory')
    baseline = workspace / 'artifacts/modules'
    modules = module_inventory(baseline) if os.path.lexists(baseline) else None
    return {'project': project, 'kernel': kernel, 'vendor': vendor, 'targets': targets,
            'builder': builder, 'compiler': run(['aarch64-linux-gnu-gcc', '--version']).decode(),
            'vendor_config_blob': {'bytes': len(config), 'sha256': hashlib.sha256(config).hexdigest()},
            'original_modules_inventory': modules,
            'original_modules_authentication': 'Unanchored retained bytes; no committed modules inventory hash'}, config


def resources(paths):
    # Fixed read-only proc endpoint; artifact helpers intentionally reject /proc.
    mem = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    return {'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'available_ram_bytes': int(mem['MemAvailable'].strip().split()[0]) * 1024,
            'free_disk_bytes': {str(path): shutil.disk_usage(path).free for path in paths}}


def initial_resources(paths):
    sample = resources(paths)
    require(min(sample['free_disk_bytes'].values()) > 15_000_000_000, 'Need more than 15 GB available disk')
    require(sample['available_ram_bytes'] >= 3_000_000_000, 'Need at least 3 GB available RAM')
    return sample


class Monitor:
    """Thirty-second samples and process-group cancellation on low disk."""
    def __init__(self, paths, log):
        self.paths, self.log = paths, log
        self.stop = threading.Event()
        self.failed = threading.Event()
        self.reason = ''
        self.thread = threading.Thread(target=self.watch, daemon=True)

    def sample(self, phase):
        try:
            sample = resources(self.paths)
            sample['phase'] = phase
            self.log.write(json.dumps(sample) + '\n')
            self.log.flush()
            require(min(sample['free_disk_bytes'].values()) >= 3_000_000_000,
                    'Available disk dropped below 3 GB')
        except Exception as exc:
            self.reason = str(exc)
            self.failed.set()

    def watch(self):
        while not self.stop.wait(30):
            self.sample('periodic')

    def command(self, command, cwd, log):
        require(not self.failed.is_set(), self.reason)
        env = dict(environment(), PWD=str(cwd))
        with subprocess.Popen(list(map(str, command)), cwd=cwd, env=env,
                              stdout=log, stderr=subprocess.STDOUT, start_new_session=True) as process:
            try:
                while process.poll() is None:
                    if self.failed.wait(1):
                        raise ValueError(self.reason)
                require(process.returncode == 0, f'Command failed ({process.returncode}): {command}')
            finally:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                # The leader may exit while a compiler descendant ignores TERM.
                # Always clear its remaining private process group as well.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        require(not self.failed.is_set(), self.reason)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def build_settings(tree, workspace, debug_path_profile='native'):
    require(debug_path_profile in DEBUG_PATH_PROFILES, 'Unknown debug-path profile')
    make = ['make', '-j2', 'ARCH=arm64', 'CROSS_COMPILE=aarch64-linux-gnu-']
    kcflags, kaflags = None, None
    if debug_path_profile == 'original-20261104':
        source, original = str(safe_path(tree)), str(safe_path(workspace / 'kernel'))
        require(re.fullmatch(r'[A-Za-z0-9/._-]+', source + original), 'Unsafe Make mapping spelling')
        kcflags = f'-fdebug-prefix-map={source}={original}'
        kaflags = f'-Wa,--debug-prefix-map={source}={original}'
        make += [f'KCFLAGS={kcflags}', f'KAFLAGS={kaflags}']
    return {'debug_path_profile': debug_path_profile, 'kcflags': kcflags, 'kaflags': kaflags,
            'umask': '0002' if debug_path_profile == 'original-20261104' else '0022',
            'make_command': make}


def capture_generated(tree, artifacts, settings):
    """Retain actual Kbuild commands and generated headers; validate observed maps."""
    for relative in ('include/generated', 'include/config', 'arch/arm64/include/generated'):
        root = safe_path(tree / relative)
        require(root.is_dir(), f'Missing generated headers directory: {relative}')
        for directory, directories, files in os.walk(root, followlinks=False):
            for name in directories:
                safe_path(Path(directory) / name)
            for name in files:
                source = Path(directory) / name
                destination = artifacts / 'generated' / source.relative_to(tree)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(read_regular(source))
    commands = {}
    for relative, flag in (('arch/arm64/kernel/vdso/.vgettimeofday.o.cmd', settings['kcflags']),
                           ('arch/arm64/kernel/vdso/.note.o.cmd', settings['kaflags']),
                           ('arch/arm64/kernel/vdso/.sigreturn.o.cmd', settings['kaflags']),
                           ('init/.main.o.cmd', settings['kcflags'])):
        content = read_regular(tree / relative)
        destination = artifacts / 'commands' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        commands[relative] = facts(destination)
        if flag is None:
            require(b'-fdebug-prefix-map=' not in content and b'--debug-prefix-map=' not in content,
                    f'Native actual command unexpectedly maps debug paths: {relative}')
        else:
            require(flag.encode() in content, f'Actual command lacks explicit debug-path mapping: {relative}')
    return commands


def build(repo, workspace, output, before, config, debug_path_profile='native'):
    settings = build_settings(output / 'kernel-source', workspace, debug_path_profile)
    caller_umask = os.umask(int(settings['umask'], 8))
    settings['caller_umask'] = f'{caller_umask:04o}'
    try:
        return _build(repo, workspace, output, before, config, settings)
    finally:
        # Includes failures before output initialization, signals and postchecks.
        os.umask(caller_umask)


def _build(repo, workspace, output, before, config, settings):
    output.mkdir()  # Never reuse, delete, or recursively clean any existing tree.
    artifacts, logs, tree = output / 'artifacts', output / 'logs', output / 'kernel-source'
    artifacts.mkdir()
    logs.mkdir()
    result = {'schema': 1, 'target': TARGET, 'success': False, 'build_settings': settings,
              'build_environment_before': environment(), 'umask_before': settings['umask'],
              'hardware_acceptance': 'Not established; actual KVM guest remains required',
              'limitations': 'Reuses cached sources and registered builder; not a fresh-builder acquisition. Modules baseline comparison is unanchored.'}
    with (logs / 'resources.jsonl').open('w') as resource_log:
        monitor = Monitor((workspace, output), resource_log)
        monitor.sample('start')
        monitor.thread.start()
        try:
            write_json(artifacts / 'input-snapshot.json', before)
            for relative in PROJECT_INPUTS:
                destination = artifacts / 'project-inputs' / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repo / relative, destination)
                helper.authenticate(destination, before['project']['inputs'][relative])
            for name in PRODUCTS:
                shutil.copyfile(workspace / 'artifacts' / name, artifacts / ('original-' + name))
                helper.authenticate(artifacts / ('original-' + name), before['targets'][name])
            with (logs / 'source-checkout.log').open('wb') as log:
                monitor.command(git_command(workspace / 'kernel', 'clone', '--shared', '--no-checkout',
                                            workspace / 'kernel', safe_path(tree)), output, log)
                monitor.command(git_command(tree, 'checkout', '--detach', KERNEL_HEAD), tree, log)
            private_before = source_state(tree, KERNEL_HEAD)
            require(private_before == before['kernel'], 'Private clone changed pinned source/shallow state')
            require(not os.path.lexists(tree / '.scmversion'), 'Unexpected private .scmversion')
            write_json(artifacts / 'source-provenance.json', {
                'project': before['project'], 'original_kernel': before['kernel'],
                'original_vendor': before['vendor'], 'private_source_before': private_before,
                'source_method': 'git clone --shared --no-checkout; detached pinned commit',
                'build_environment': environment(), 'build_settings': settings,
                'jobs': 2, 'localversion': '-a733-diag',
                'original_scmversion': 'absent', 'expected_utsrelease': '6.6.98-a733-diag'})
            (tree / '.config').write_bytes(config)
            make = settings['make_command']
            with (logs / 'kernel-config.log').open('wb') as log:
                monitor.command([tree / 'scripts/config', '--file', tree / '.config', '--enable', 'VIRTUALIZATION',
                                 '--enable', 'KVM', '--enable', 'IKCONFIG', '--enable', 'IKCONFIG_PROC',
                                 '--set-str', 'SYSTEM_TRUSTED_KEYS', '', '--set-str', 'SYSTEM_REVOCATION_KEYS', ''], tree, log)
                monitor.command(make + ['olddefconfig'], tree, log)
            shutil.copyfile(tree / '.config', artifacts / 'kernel.config')
            result['config_before_build'] = {'actual': facts(artifacts / 'kernel.config'),
                                             'expected': before['targets']['kernel.config']}
            helper.authenticate(tree / '.config', before['targets']['kernel.config'])
            require(b'CONFIG_KVM=y\n' in read_regular(tree / '.config'), 'KVM must remain enabled')
            with (logs / 'kernel-build.log').open('wb') as log:
                monitor.command(make + ['LOCALVERSION=-a733-diag', 'Image', 'modules', 'dtbs'], tree, log)
            result['actual_commands'] = capture_generated(tree, artifacts, settings)
            require(read_regular(tree / 'include/config/kernel.release').strip() == b'6.6.98-a733-diag',
                    'Unexpected kernel SCM/localversion release')
            modules = artifacts / 'modules'
            modules.mkdir()
            with (logs / 'modules-install.log').open('wb') as log:
                monitor.command(make + ['LOCALVERSION=-a733-diag', f'INSTALL_MOD_PATH={modules}',
                                        'INSTALL_MOD_STRIP=1', 'modules_install'], tree, log)
            remove_generated_links(modules)
            inventory = module_inventory(modules)
            require(inventory, 'Empty installed modules inventory')
            write_json(artifacts / 'modules-inventory.json', inventory)
            result['modules_match_unanchored_retained_baseline'] = (
                inventory == before['original_modules_inventory'] if before['original_modules_inventory'] is not None else None)
            for name, relative in PRODUCTS.items():
                shutil.copyfile(tree / relative, artifacts / name)
            result['comparisons'] = {name: {'actual': facts(artifacts / name), 'expected': before['targets'][name],
                                                   'equal': facts(artifacts / name) == before['targets'][name]}
                                     for name in PRODUCTS}
            result['success'] = all(entry['equal'] for entry in result['comparisons'].values())
            require(result['success'], 'Kernel replay differs from retained authenticated target')
        except BaseException as exc:
            result['success'] = False
            result['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            try:
                after, _ = snapshot(repo, workspace)
                write_json(artifacts / 'input-snapshot-after.json', after)
                require(after == before, 'Shared sources, artifacts, project or builder changed during replay')
                result['shared_inputs_unchanged'] = True
                if tree.is_dir():
                    write_json(artifacts / 'private-source-after.json', source_state(tree, KERNEL_HEAD))
            except Exception as exc:
                result['success'] = False
                result['postcheck_error'] = str(exc)
            result['build_environment_after'] = environment()
            observed_umask = os.umask(int(settings['umask'], 8))
            result['umask_after'] = f'{observed_umask:04o}'
            if result['umask_after'] != settings['umask']:
                result['success'] = False
                result['umask_error'] = 'Build umask changed during replay'
            monitor.stop.set()
            monitor.thread.join()
            monitor.sample('end')
            if monitor.failed.is_set():
                result['success'] = False
                result['resource_error'] = monitor.reason
            write_json(artifacts / 'replay-result.json', result)
            entries = {path.relative_to(output).as_posix(): facts(path)
                       for directory in (artifacts, logs) for path in sorted(directory.rglob('*'))
                       if path.is_file() and not path.is_symlink()}
            write_json(output / 'replay-manifest.json', {'schema': 1, 'files': entries})
    require(result['success'], 'Replay failed final verification; retained result records the reason')
    return result


def interrupted(signum, _frame):
    raise InterruptedError(f'Replay interrupted by signal {signum}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True, choices=[TARGET])
    parser.add_argument('--builder-profile', required=True, choices=[PROFILE])
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--debug-path-profile', choices=DEBUG_PATH_PROFILES, default='native',
                        help='Opt into original debug directories and umask 0002; default native uses umask 0022')
    args = parser.parse_args()
    try:
        validate_output(REPO, WORKSPACE, args.output)
        initial_resources((WORKSPACE, args.output.parent))
        before, config = snapshot(REPO, WORKSPACE)
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGHUP, interrupted)
        build(REPO, WORKSPACE, args.output, before, config, args.debug_path_profile)
    except (ValueError, OSError, subprocess.CalledProcessError, KeyError, UnicodeError) as exc:
        print(f'Kernel replay refused/failed: {exc}', file=sys.stderr)
        return 1
    print(f'Kernel Image, DTB and config match authenticated retained bytes: {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

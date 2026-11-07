#!/usr/bin/env python3
"""Build a separate NVMe U-Boot component in the frozen builder; never flash.

The historical two-replay launcher and its 10 GB guard remain unchanged. This
single-candidate launcher retains the component's 4 GB start floor, its full
construction budget, and the 3 GB disk/1 GB memory runtime abort thresholds.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import subprocess
import sys
import time
import types
import zlib

SELF = 'build/launch-uboot-nvme-candidate.py'
COMPONENT = 'build/uboot-nvme-candidate.py'
SHARED = 'build/launch-userspace-configuration.py'
SCRIPT = 'configs/spi-nvme-host-boot.cmd'
POLICY = 'configs/uboot-nvme-candidate-v1.json'
BASE = Path('/var/lib/a733-clean-replay-20261106')
SOURCE_HOME = BASE / 'home'
PROVENANCE = Path('/var/lib/a733-el2-work-20261104/out/el2-diagnostic-20261104-03/artifacts')
WORKSPACE = '/home/drew/orange-pi-4-pro-build'
CORE = 'build/uboot-replay.py'
CORE_INPUTS = (CORE, 'configs/diagnostic-sources.lock.json', 'configs/vendor-boot-inputs.sha256',
               'configs/builder-packages.lock.tsv', 'configs/builder-replay-packages-20261106.lock.tsv',
               'docs/evidence/el2-diagnostic-20261104.json', 'build/patches/uboot-env-nowhere-size.patch',
               'build/patches/uboot-a733-el2-handoff.patch')
SOURCE_FILES = tuple(dict.fromkeys((SELF, COMPONENT, SHARED, SCRIPT, POLICY) + CORE_INPUTS))
HOST_LOCK = 'configs/builder-replay-packages-20261106.lock.tsv'
PROBE_SHA = 'ce565b23ac0b927e54fbfc057bee69ae95d12ba930a5c5421699538e9da14fea'
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C', 'TZ': 'UTC'}
TIMEOUT = 1800


def require(value, message):
    if not value:
        raise ValueError(message)


def pin(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def read_source(path):
    require(path.is_absolute() and str(path) == os.path.abspath(path), 'physical absolute source required')
    require(not any(path == p or p in path.parents for p in map(Path, ('/dev', '/proc', '/sys'))), 'pseudo source forbidden')
    for parent in path.parents:
        require(stat.S_ISDIR(parent.lstat().st_mode), 'physical source parents required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= 16 * 1024**2,
                'bounded single regular source required')
        data = stream.read(16 * 1024**2 + 1)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(len(data) == before.st_size and identity(before) == identity(os.fstat(fd))
                == identity(path.lstat()), 'source changed during read')
    return data


def committed_sources(project):
    revision = subprocess.check_output(['git', '-C', str(project), 'rev-parse', '--verify', 'HEAD'], env=ENV).decode().strip()
    require(re.fullmatch('[0-9a-f]{40}', revision), 'invalid project revision')
    records, contents = {}, {}
    for name in SOURCE_FILES:
        data = read_source(project / name)
        fields = subprocess.check_output(['git', '-C', str(project), 'ls-tree', revision, '--', name], env=ENV).decode().split()
        require(len(fields) == 4 and fields[0] in ('100644', '100755') and fields[1] == 'blob' and fields[3] == name,
                'uncommitted source: ' + name)
        require(subprocess.check_output(['git', '-C', str(project), 'cat-file', 'blob', fields[2]], env=ENV) == data,
                'source differs from Git blob: ' + name)
        records[name], contents[name] = dict(pin(data), git_blob=fields[2]), data
    return {'revision': revision, 'files': records}, contents


def module(name, data):
    result = types.ModuleType(name.replace('/', '_'))
    result.__file__ = name
    exec(compile(data, name, 'exec'), result.__dict__)
    return result


def phase_path(tag):
    require(isinstance(tag, str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', tag), 'unsafe tag')
    return BASE / ('uboot-nvme-component-' + tag)


def plan(tag, manifest):
    phase = phase_path(tag)
    return {'schema': 1, 'classification': 'component candidate only; no flash or hardware acceptance',
            'phase': str(phase), 'inputs': manifest, 'candidate': 'project/out/uboot-nvme-candidate-01',
            'source_home': str(SOURCE_HOME), 'provenance': str(PROVENANCE), 'compilation_jobs': 2,
            'minimum_initial_free_bytes_exclusive': 4_000_000_000,
            'additional_budget': 'inner recipe must pass complete single-construction budget before output creation',
            'runtime_minimum_free_bytes': 3_000_000_000, 'runtime_minimum_available_memory_bytes': 1_000_000_000,
            'timeout_seconds': TIMEOUT, 'spi_image_or_programmer': False}


def inner_program(revision):
    require(re.fullmatch('[0-9a-f]{40}', revision), 'invalid inner revision')
    return ('''import hashlib,json,os,pathlib,subprocess
os.umask(0o002)
project=pathlib.Path('/a733-export-output/project')
subprocess.run(['git','clone','--no-checkout','/a733-debs/project.bundle',str(project)],check=True)
subprocess.run(['git','-C',str(project),'checkout','--detach',REVISION],check=True)
assert subprocess.check_output(['git','-C',str(project),'rev-parse','HEAD']).decode().strip()==REVISION
(project/'out').mkdir()
output=project/'out/uboot-nvme-candidate-01'
subprocess.run(['/usr/bin/python3','-I','-B',str(project/'build/uboot-nvme-candidate.py'),
 '--workspace','/home/drew/orange-pi-4-pro-build',
 '--provenance','/var/lib/a733-el2-work-20261104/out/el2-diagnostic-20261104-03/artifacts',
 '--builder-profile','replay-20261106','--output',str(output)],check=True)
scriptdir=pathlib.Path('/a733-export-output/host-script')
scriptdir.mkdir(mode=0o700)
source=project/'configs/spi-nvme-host-boot.cmd'
script=scriptdir/'spi-nvme-host-boot.scr'
env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/nonexistent','LC_ALL':'C','TZ':'UTC','SOURCE_DATE_EPOCH':'1793750400'}
subprocess.run(['mkimage','-C','none','-A','arm','-T','script','-d',str(source),str(script)],check=True,env=env)
def record(p):
 data=p.read_bytes();return {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
(scriptdir/'receipt.json').write_text(json.dumps({'schema':1,'success':True,'source':record(source),'script':record(script),
 'classification':'separate unbooted NVMe script candidate; never installed'},indent=2)+'\\n')
'''.replace('REVISION', repr(revision))).encode()


def check_resources(sample, elapsed):
    require(sample['free_bytes'] >= 3_000_000_000 and sample['available_memory_bytes'] >= 1_000_000_000
            and elapsed <= TIMEOUT, 'candidate resource floor or timeout exceeded')


def verify_host_script(image, source):
    require(len(image) >= 72, 'truncated legacy script')
    magic, header_crc, epoch, size, load, entry, data_crc, system, arch, kind, compression, name = struct.unpack(
        '>7I4B32s', image[:64])
    require(magic == 0x27051956 and (system, arch, kind, compression) == (5, 2, 6, 0)
            and epoch == 1793750400 and load == entry == 0 and name == bytes(32), 'legacy script header differs')
    require(zlib.crc32(image[:4] + bytes(4) + image[8:64]) == header_crc, 'legacy script header CRC differs')
    payload = image[64:]
    require(len(payload) == size and zlib.crc32(payload) == data_crc, 'legacy script payload CRC/size differs')
    require(payload == struct.pack('>2I', len(source), 0) + source, 'legacy script source payload differs')
    return {'source': pin(source), 'script': pin(image), 'epoch': epoch, 'crc_verified': True}


def command(shared, builder, phase):
    args = builder.nspawn_command(shared.ROOT, shared.SEED / 'probe.sh')[:-2]
    require(args.count('--private-users-ownership=chown') == 1, 'unexpected namespace command')
    args[args.index('--private-users-ownership=chown')] = '--private-users-ownership=off'
    args += ['--read-only', '--bind-ro=' + str(SOURCE_HOME) + ':/home',
             '--bind=' + str(phase / 'work') + ':/a733-export-output',
             '--bind-ro=' + str(PROVENANCE) + ':' + str(PROVENANCE),
             '--bind-ro=' + str(phase / 'inputs') + ':/a733-debs',
             '--bind-ro=' + str(phase / 'inputs/run.py') + ':/a733-install.sh']
    return args + ['/bin/sh', '-c', '/bin/sh /a733-probe.sh && exec /usr/bin/python3 -I -B /a733-install.sh']


def preflight(shared, phase, contents):
    require(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0, 'initial-root required')
    require(Path('/proc/self/uid_map').read_text().split() == ['0', '0', '4294967295'], 'initial user namespace required')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    require(release.get('ID', '').strip('"') == 'ubuntu' and release.get('VERSION_ID', '').strip('"') == '22.04'
            and os.uname().machine == 'x86_64', 'Ubuntu22.04AMD64 required')
    shared.safe_directory(BASE, trusted=True)
    require(not os.path.lexists(phase), 'candidate phase exists')
    require(shared.resource_sample()['free_bytes'] > 4_000_000_000, 'need more than 4 GB before staging')
    shared.frozen_root_directory(shared.ROOT)
    shared.safe_directory(SOURCE_HOME)
    info = SOURCE_HOME.lstat()
    require(info.st_uid == info.st_gid == shared.UID_BASE and stat.S_IMODE(info.st_mode) == 0o700,
            'unexpected imported source home')
    shared.safe_directory(PROVENANCE, trusted=True)
    require(not shared.mapped_processes() and not shared.phase_mounts(phase), 'active builder process/mount')
    helper = shared.regular_bytes(shared.HELPER, owner=0)
    require(hashlib.sha256(helper).hexdigest() == shared.HELPER_SHA, 'frozen builder helper pin differs')
    require(pin(shared.regular_bytes(shared.SEED / 'probe.sh', owner=0))['sha256'] == PROBE_SHA, 'probe pin differs')
    for suffix in ('', '/out', '/out/el2-diagnostic-20261104-03', '/out/el2-diagnostic-20261104-03/artifacts'):
        anchor = shared.ROOT / ('var/lib/a733-el2-work-20261104' + suffix)
        shared.safe_directory(anchor)
        info = anchor.lstat()
        require(info.st_uid == info.st_gid == shared.UID_BASE and stat.S_IMODE(info.st_mode) == 0o755,
                'frozen provenance anchor differs')
    require(not any((shared.ROOT / str(PROVENANCE).lstrip('/')).iterdir()), 'frozen provenance anchor is not empty')
    before = subprocess.check_output(shared.INVENTORY, env=ENV)
    shared.validate_host_inventory(before, contents[HOST_LOCK])
    return module(str(shared.HELPER), helper), before


def execute(shared, builder, project, phase, manifest, contents, before, report):
    phase.mkdir(mode=0o700)
    (phase / 'work').mkdir(mode=0o700)
    os.chown(phase / 'work', shared.UID_BASE, shared.UID_BASE)
    inputs = phase / 'inputs'
    inputs.mkdir(mode=0o700)
    bundle = inputs / 'project.bundle'
    subprocess.run(['git', '-C', str(project), 'bundle', 'create', str(bundle), 'HEAD'], env=ENV, check=True)
    os.chmod(bundle, 0o444)
    raw_bundle = shared.regular_bytes(bundle, owner=0)
    # Bundle plus checkout must fit above the existing4GB component start floor.
    require(shared.resource_sample()['free_bytes'] > 4_000_000_000 + 2 * len(raw_bundle), 'bundle/checkout staging budget exceeded')
    shared.atomic_json(inputs / 'manifest.json', manifest)
    with (inputs / 'run.py').open('xb') as stream:
        stream.write(inner_program(manifest['revision']))
    os.chmod(inputs / 'run.py', 0o444)
    os.chmod(inputs, 0o555)
    shared.atomic_json(phase / 'source-manifest.json', manifest)
    shared.atomic_json(phase / 'host-packages.before.json', {'sha256': hashlib.sha256(before).hexdigest()})
    args = command(shared, builder, phase)
    report.update(success=False, command=args, bundle=pin(raw_bundle), resources=[])
    proc = None
    handlers = {s: signal.signal(s, shared.interrupted) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    try:
        with (phase / 'runtime.log').open('xb') as stream:
            proc = subprocess.Popen(args, stdout=stream, stderr=subprocess.STDOUT, env=ENV, start_new_session=True)
            start = time.monotonic()
            while True:
                sample = shared.resource_sample()
                report['resources'].append(sample)
                check_resources(sample, time.monotonic() - start)
                if proc.poll() is not None:
                    break
                time.sleep(1)
            report['returncode'] = proc.wait(timeout=15)
            stream.flush()
            os.fsync(stream.fileno())
        require(report['returncode'] == 0, 'candidate payload failed; inspect retained runtime log')
        report['probe'] = builder.validate_probe((phase / 'runtime.log').read_bytes())
        result = json.loads(shared.regular_bytes(phase / 'work/project/out/uboot-nvme-candidate-01/artifacts/candidate-result.json',
                                                owner=shared.UID_BASE))
        script = json.loads(shared.regular_bytes(phase / 'work/host-script/receipt.json', owner=shared.UID_BASE))
        require(result.get('component_build_success') is True and result.get('hardware_acceptance') is False
                and result.get('deployment_authorized') is False and script.get('success') is True,
                'candidate result did not pass')
        image = shared.regular_bytes(phase / 'work/host-script/spi-nvme-host-boot.scr', owner=shared.UID_BASE,
                                     limit=len(contents[SCRIPT]) + 72)
        checked = verify_host_script(image, contents[SCRIPT])
        require(script.get('source') == checked['source'] and script.get('script') == checked['script'],
                'host script receipt bindings differ')
        report.update(component_result=result, host_script=script, host_script_inspection=checked, success=True)
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        shared.stop_process(proc)
        report['mapped_processes_final'] = shared.mapped_processes()
        report['phase_mounts_final'] = shared.phase_mounts(phase)
        after = subprocess.check_output(shared.INVENTORY, env=ENV)
        report['host_inventory_unchanged'] = before == after
        report['success'] = (report['success'] and before == after and not report['mapped_processes_final'] and not report['phase_mounts_final'])
        shared.atomic_json(phase / 'receipt.json', report)
        for s, handler in handlers.items():
            signal.signal(s, handler)
    require(report['success'], 'candidate final cleanup/inventory checks failed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--plan', action='store_true')
    options = parser.parse_args(argv)
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    manifest, contents = committed_sources(options.project)
    require(read_source(Path(__file__).absolute()) == contents[SELF], 'executing launcher differs from committed source')
    report = plan(options.tag, manifest)
    if options.plan:
        print(json.dumps(report, sort_keys=True))
        return
    shared = module(SHARED, contents[SHARED])
    phase = phase_path(options.tag)
    builder, before = preflight(shared, phase, contents)
    execute(shared, builder, options.project, phase, manifest, contents, before, report)
    print(json.dumps({'success': True, 'phase': str(phase)}))


if __name__ == '__main__':
    main()

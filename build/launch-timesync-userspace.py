#!/usr/bin/env python3
"""Derive/export a fresh private time-profile clone; no hardware acceptance.

--plan reads committed project sources only, without accessing builder inputs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time
import types

BASE = Path('/var/lib/a733-clean-replay-20261106')
SELF = 'build/launch-timesync-userspace.py'
PIPELINE = 'build/launch-userspace-pipeline.py'
SHARED = 'build/launch-userspace-configuration.py'
DERIVER = 'build/configure-timesync-userspace.py'
ADAPTER = 'build/export-timesync-userspace.py'
CONTRACT = 'configs/host-timesync-inputs.lock.json'
HOST_LOCK = 'configs/builder-replay-packages-20261106.lock.tsv'
CONTRACT_PIN = {'bytes': 3837, 'sha256': 'b183b9df1c19241420c6939c4487dcf4894ae60daa3827d73589a6e112918a5f'}
SOURCE_FILES = (SELF, PIPELINE, SHARED, DERIVER, ADAPTER, CONTRACT, HOST_LOCK,
                'configs/host-userspace-profiles.lock.json', 'build/prepare-host-userspace.py',
                'build/offline-userspace-repo.py', 'build/debootstrap-adapter.py', 'build/configure-host-userspace.py',
                'build/export-host-userspace.py', 'build/userspace-bootstrap-api.py',
                'build/timesync-identity.py', 'configs/host-userspace-packages.lock.tsv',
                'configs/host-userspace-package-hashes.lock.tsv', 'configs/host-timesync-packages.lock.tsv',
                'configs/host-timesync-package-hashes.lock.tsv', 'configs/host-timesync-sysusers.conf',
                'configs/host-userspace-source-archives.lock.json',
                'docs/evidence/time-sync-package-input-20261106.json')
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C', 'TZ': 'UTC'}
MIN_INITIAL = 5 * 1024**3
CONFIG_FLOOR = 4 * 1024**3
RUNTIME_FLOOR = 3 * 1024**3
HEADROOM = 64 * 1024**2
TIMEOUTS = {'configure': 2700, 'export': 900}


def require(value, message):
    if not value:
        raise ValueError(message)


def pin(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def module(name, data):
    result = types.ModuleType(name.replace('/', '_'))
    result.__file__ = name
    exec(compile(data, name, 'exec'), result.__dict__)
    return result


def tag_path(tag, prefix='userspace-timesync-'):
    require(isinstance(tag, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', tag), 'unsafe tag')
    return BASE / (prefix + tag)


def absolute(path):
    require(path.is_absolute() and '..' not in path.parts, 'canonical absolute path required')
    return path


def committed_sources(project):
    # Authenticate the bootstrap readers before executing any project module.
    absolute(project)
    revision = subprocess.check_output(['/usr/bin/git', '-C', str(project), 'rev-parse', '--verify', 'HEAD'], env=ENV).decode().strip()
    require(re.fullmatch('[0-9a-f]{40}', revision), 'invalid Git revision')
    records, contents = {}, {}
    for name in SOURCE_FILES:
        path = project / name
        for parent in path.parents:
            require(stat.S_ISDIR(parent.lstat().st_mode), 'physical project ancestors required')
        data = held(path, limit=16 * 1024**2)[0]
        fields = subprocess.check_output(['/usr/bin/git', '-C', str(project), 'ls-tree', revision, '--', name], env=ENV).decode().split()
        require(len(fields) == 4 and fields[0] in ('100644', '100755') and fields[1] == 'blob' and fields[3] == name, 'uncommitted source: ' + name)
        require(subprocess.check_output(['/usr/bin/git', '-C', str(project), 'cat-file', 'blob', fields[2]], env=ENV) == data, 'source differs from committed blob: ' + name)
        contents[name], records[name] = data, dict(pin(data), git_blob=fields[2])
    require(pin(contents[CONTRACT]) == CONTRACT_PIN, 'input contract pin differs')
    d = module(DERIVER, contents[DERIVER])
    a = module(ADAPTER, contents[ADAPTER])
    require(set(d.SOURCE_FILES) <= set(contents), 'missing derivation dependencies')
    require(pin(contents[DERIVER]) == a.DERIVER_PIN and pin(contents[a.BACKEND]) == a.BACKEND_PIN,
            'adapter source pins differ')
    contract = json.loads(contents[CONTRACT])
    for name, expected in contract['locks'].items():
        require(pin(contents[name]) == expected, 'lock pin differs')
    for expected in (contract['identity']['policy'], contract['package_evidence'], contract['source_lock']):
        require(pin(contents[expected['path']]) == {k: expected[k] for k in ('bytes', 'sha256')}, 'source input pin differs')
    return {'revision': revision, 'files': records}, contents


def signature(item):
    return (item.st_dev, item.st_ino, item.st_mode, item.st_uid, item.st_gid,
            item.st_nlink, item.st_size, item.st_mtime_ns, item.st_ctime_ns)


def held(path, owner=None, limit=None, data=True):
    absolute(path)
    for parent in path.parents:
        require(stat.S_ISDIR(parent.lstat().st_mode), 'physical input ancestors required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'single regular input required')
        require(owner is None or before.st_uid == before.st_gid == owner, 'input owner differs')
        require(limit is None or before.st_size <= limit, 'oversized input')
        digest, size, chunks = hashlib.sha256(), 0, []
        for block in iter(lambda: stream.read(1024**2), b''):
            size += len(block)
            require(limit is None or size <= limit, 'input grew beyond bound')
            digest.update(block)
            if data:
                chunks.append(block)
        require(signature(before) == signature(os.fstat(fd)) == signature(path.lstat()), 'input changed during read')
    return b''.join(chunks), {'bytes': size, 'sha256': digest.hexdigest()}


def plan(tag, base_tag, extra, manifest):
    phase = tag_path(tag)
    return {'schema': 1, 'classification': 'userspace derivation only; no hardware acceptance',
            'phase': str(phase), 'work': str(phase / 'work'),
            'base': str(tag_path(base_tag, 'userspace-pipeline-') / 'work'),
            'extra_deb': str(absolute(extra)), 'inputs': manifest,
            'clone': ['arm-root', 'repo'], 'initial_free_bytes_minimum': MIN_INITIAL,
            'capacity_budget': 'max(5 GiB, 4 GiB + full regular contents + 4096/node + all staged sources twice + BOM/DEB twice + 64 MiB)',
            'phases': [{'name': name, 'start_free_bytes_minimum': CONFIG_FLOOR if name == 'configure' else 3_500_000_000,
                        'runtime_free_bytes_minimum': RUNTIME_FLOOR, 'available_memory_bytes_minimum': 1_000_000_000,
                        'timeout_seconds': timeout} for name, timeout in TIMEOUTS.items()]}


def mounts(shared, phase, base):
    return shared.phase_mounts(phase) + shared.phase_mounts(base)


def preflight(shared, pipeline, phase, base, contents):
    require(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0, 'initial-root host required')
    shared.safe_directory(BASE, trusted=True)
    # This early refusal must precede even base existence checks and all staging.
    require(shared.resource_sample()['free_bytes'] >= MIN_INITIAL, 'need at least 5 GiB free before any staging or clone')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    require(release.get('ID', '').strip('"') == 'ubuntu' and release.get('VERSION_ID', '').strip('"') == '22.04'
            and os.uname().machine == 'x86_64', 'Ubuntu 22.04 AMD64 host required')
    require(Path('/proc/self/uid_map').read_text().split() == ['0', '0', '4294967295'], 'initial user namespace required')
    for path in (shared.SEED, shared.HELPER.parent):
        shared.safe_directory(path, trusted=True)
    shared.frozen_root_directory(shared.ROOT)
    require(not os.path.lexists(phase), 'phase already exists; use fresh tag')
    require(not shared.mapped_processes() and not mounts(shared, phase, base), 'active builder process/mount')
    metadata = shared.HELPER.lstat()
    require(metadata.st_uid == metadata.st_gid == 0 and not metadata.st_mode & 0o022, 'untrusted helper')
    helper = shared.regular_bytes(shared.HELPER, owner=0)
    require(pin(helper)['sha256'] == shared.HELPER_SHA, 'helper pin differs')
    probe = shared.regular_bytes(shared.SEED / 'probe.sh', owner=0)
    require(pin(probe)['sha256'] == pipeline.PROBE_SHA, 'probe pin differs')
    shared.safe_directory(shared.SEED / 'firmware-empty', trusted=True)
    require(not any((shared.SEED / 'firmware-empty').iterdir()), 'firmware mask not empty')
    qemu = {str(p): shared.sha(p) for p in (Path('/usr/bin/qemu-aarch64-static'), shared.ROOT / 'usr/bin/qemu-aarch64-static',
             Path('/usr/libexec/qemu-binfmt/aarch64-binfmt-P').resolve(strict=True))}
    require(all(value == shared.QEMU_SHA for value in qemu.values()), 'QEMU pin differs')
    binfmt = Path('/proc/sys/fs/binfmt_misc/qemu-aarch64').read_text()
    require('enabled\n' in binfmt and 'flags: PF\n' in binfmt and
            'interpreter /usr/libexec/qemu-binfmt/aarch64-binfmt-P\n' in binfmt, 'binfmt differs')
    inventory = subprocess.check_output(shared.INVENTORY, env=ENV)
    shared.validate_host_inventory(inventory, contents[HOST_LOCK])
    builder = module(str(shared.HELPER), helper)
    require(builder.PROBE == probe, 'helper probe differs')
    return builder, inventory, {'qemu': qemu, 'binfmt': binfmt}


def base_inputs(shared, base, contents, extra, builder=None):
    shared.safe_directory(base.parent, trusted=True)
    shared.safe_directory(base)
    contract = json.loads(contents[CONTRACT])
    report = json.loads(held(base.parent / 'receipt.json', owner=0, limit=1024**2)[0])
    require(report.get('phase') == str(base.parent) and report.get('work') == str(base)
            and report.get('success') is True and report.get('final_archive') == contract['base']['archive']
            and [p.get('name') for p in report.get('phases', [])] == ['foreign', 'configure', 'export']
            and all(p.get('success') is True and p.get('returncode') == 0
                    and p.get('mapped_processes_final') == [] and p.get('output_mounts_final') == [] for p in report['phases'])
            and report.get('host_package_inventory_unchanged') is True
            and report.get('mapped_processes_final') == [] and report.get('output_mounts_final') == [], 'base pipeline chain failed')
    base_sources(shared, base, contents, report, builder)
    output = base / 'export-result'
    normalized = json.loads(contents['configs/host-userspace-profiles.lock.json'])['profiles']['normalized-20261106']
    receipt_data = held(output / 'receipt.json', owner=shared.UID_BASE, limit=1024**2)[0]
    receipt = json.loads(receipt_data)
    require(report['phases'][2].get('recipe_receipt_sha256') == pin(receipt_data)['sha256']
            and receipt.get('success') is True and receipt.get('phase') == 'final-userspace-export'
            and receipt.get('archive') == contract['base']['archive']
            and receipt.get('inventory') == normalized['inventory']
            and receipt.get('raw_tar') == normalized['raw_tar'] and receipt.get('members') == normalized['members']
            and receipt.get('timestamps', {}).get('epoch') == normalized['epoch']
            and receipt.get('timestamps', {}).get('native_source_metadata_verified') is True
            and receipt.get('exporter_sha256') == pin(contents['build/export-host-userspace.py'])['sha256'], 'base export chain failed')
    require(held(output / 'rootfs.tar.gz', owner=shared.UID_BASE, data=False)[1] == contract['base']['archive'], 'base archive pin differs')
    bom = held(output / 'rootfs-bom.json', owner=shared.UID_BASE, limit=contract['base']['bom']['bytes'])[0]
    require(pin(bom) == contract['base']['bom'], 'base BOM pin differs')
    addition = contract['addition']
    deb = held(extra, limit=addition['bytes'])[0]
    require(pin(deb) == {k: addition[k] for k in ('bytes', 'sha256')}, 'extra DEB pin differs')
    for name in ('arm-root', 'repo'):
        shared.validate_source_tree(base / name)
    root_info = (base / 'arm-root').lstat()
    require(root_info.st_uid == root_info.st_gid == shared.UID_BASE, 'native mapped-root directory required')
    d = module(DERIVER, contents[DERIVER])
    require(d.status_inventory(held(base / 'arm-root/var/lib/dpkg/status', owner=shared.UID_BASE, limit=16 * 1024**2)[0])
            == d.parse_lock(contents['configs/host-userspace-packages.lock.tsv']), 'native base inventory differs')
    require(not os.path.lexists(base / 'arm-root/usr/bin/qemu-aarch64-static'), 'base is not cleaned native tree')
    h = module('build/userspace-bootstrap-api.py', contents['build/userspace-bootstrap-api.py'])
    h.REPO = base / 'repo'
    repo_sha = held(h.REPO / 'manifest.json', owner=shared.UID_BASE, limit=1024**2)[1]['sha256']
    h.check_repository(repo_sha, owner=shared.UID_BASE)
    for index, name in ((0, 'foreign-receipt.json'), (1, 'configure-receipt.json')):
        raw, fact = held(base / name, owner=shared.UID_BASE, limit=1024**2)
        require(json.loads(raw).get('success') is True and fact['sha256'] == report['phases'][index].get('recipe_receipt_sha256'), 'base phase receipt differs')
    require(receipt.get('configure_receipt_sha256') == report['phases'][1].get('recipe_receipt_sha256'), 'base configure chain differs')
    return bom, deb, repo_sha, pin(receipt_data)


def base_sources(shared, base, contents, report, builder):
    pipeline = module(PIPELINE, contents[PIPELINE])
    manifest = json.loads(held(base.parent / 'source-manifest.json', owner=0, limit=1024**2)[0])
    names = {PIPELINE, SHARED, HOST_LOCK, pipeline.PROFILE_LOCK}.union(*map(set, pipeline.PHASE_FILES.values()))
    require(manifest == report.get('inputs') and set(manifest) == {'revision', 'files'}
            and re.fullmatch('[0-9a-f]{40}', manifest['revision']) and set(manifest['files']) == names,
            'base source manifest differs')
    for name, record in manifest['files'].items():
        require(set(record) == {'git_blob', 'bytes', 'sha256'} and re.fullmatch('[0-9a-f]{40}', record['git_blob'])
                and {key: record[key] for key in ('bytes', 'sha256')} == pin(contents[name]), 'base source pin differs')
        git_blob = hashlib.sha1(b'blob ' + str(len(contents[name])).encode() + b'\0' + contents[name]).hexdigest()
        require(record['git_blob'] == git_blob, 'base Git blob differs')
    require(builder is not None, 'authenticated base probe validator required')
    for phase in report['phases']:
        name = phase['name']
        slot = base.parent / name
        selected = pipeline.PHASE_FILES[name]
        snapshot = json.loads(held(slot / 'inputs/manifest.json', owner=0, limit=1024**2)[0])
        require(snapshot == {'revision': manifest['revision'], 'files': {key: manifest['files'][key] for key in selected}},
                'base phase manifest differs')
        for key in selected:
            require(held(slot / 'inputs/project' / key, owner=0, limit=16 * 1024**2)[1] == pin(contents[key]), 'base phase source differs')
        require(phase.get('command') == pipeline.command(shared, builder, base.parent, slot, name), 'base phase command differs')
        require(builder.validate_probe(held(slot / 'runtime.log', owner=0, limit=64 * 1024**2)[0]) == phase.get('probe'), 'base phase probe differs')
        if name == 'configure':
            require(phase.get('helper_returncode') == 0, 'base API helper failed')
            shared.validate_ready(phase['api_ready'], phase['api_profile'], shared.namespaces())


def tree_budget(shared, base):
    budget, records = 0, {}
    for name in ('arm-root', 'repo'):
        shared.validate_source_tree(base / name)
        for directory, dirs, files in os.walk(base / name, followlinks=False):
            for path in [Path(directory)] + [Path(directory) / n for n in files + dirs if (Path(directory) / n).is_symlink() or n in files]:
                item = path.lstat()
                records[str(path.relative_to(base))] = signature(item)
                budget += 4096 + (item.st_size if stat.S_ISREG(item.st_mode) else 0)
    return budget, records


PROP_HELPER = r'''import ctypes,json,os,pathlib,sys
expected=json.loads(sys.argv[1])
def need(ok):
    if not ok: raise ValueError('propagation helper identity/topology mismatch')
ns={n:(pathlib.Path('/proc/self/ns')/n).stat().st_ino for n in expected['namespaces']}
root=os.stat('/')
need(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==0)
need(pathlib.Path('/proc/self/uid_map').read_text().split()==['0','0','4294967295'])
need(pathlib.Path('/proc/self/gid_map').read_text().split()==['0','0','4294967295'])
need(ns==expected['namespaces'] and ns['mnt']!=expected['host_mount_namespace'])
need({'dev':root.st_dev,'ino':root.st_ino}==expected['root'])
libc=ctypes.CDLL(None,use_errno=True)
mount=libc.mount
mount.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_char_p,ctypes.c_ulong,ctypes.c_void_p]
mount.restype=ctypes.c_int
need(mount(None,b'/',None,(1<<18)|(1<<14),None)==0)
for line in pathlib.Path('/proc/self/mountinfo').read_text().splitlines():
    need(not any(v.startswith(('shared:','master:','propagate_from:')) for v in line.split(' - ',1)[0].split()[6:]))
print(json.dumps({'success':True,'nonce':expected['nonce'],'namespace_inodes':ns,'root':expected['root']},sort_keys=True))
'''

PROP_RUNNER = r'''import hashlib,json,os,pathlib,stat,time
W=pathlib.Path('/a733-export-output')
N=('mnt','pid','net','user','uts','ipc','cgroup')
def need(ok):
    if not ok: raise ValueError('propagation protocol failed')
def status():
    return {k:v.strip() for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line for k,v in [line.split(':',1)]}
def restricted():
    s=status()
    need(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==0 and s.get('NoNewPrivs')=='1')
    need(all(not int(s.get(k,'ffffffff'),16)&0xa631000 for k in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb')))
def read(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        b=os.fstat(fd)
        need(stat.S_ISREG(b.st_mode) and b.st_nlink==1 and b.st_uid==b.st_gid==65534 and stat.S_IMODE(b.st_mode)==0o444 and b.st_size<=1048576)
        data=f.read(1048577)
        a=os.fstat(fd)
        need((b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns,b.st_ctime_ns)==(a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns) and len(data)==b.st_size)
        return data
profile=json.loads(read(pathlib.Path('/a733-sources/api-profile.json')))
restricted()
initial_status=status()
ns={n:(pathlib.Path('/proc/self/ns')/n).stat().st_ino for n in N}
ready={'nonce':profile['nonce'],'pid':os.getpid(),'namespace_inodes':ns,'uid':0,'status':status(),'bootstrap_root':'/a733-export-output/arm-root'}
path=W/('propagation-'+STAGE+'-ready.json')
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o444)
with os.fdopen(fd,'w') as f:
    os.fchmod(f.fileno(),0o444); json.dump(ready,f,sort_keys=True); f.flush(); os.fsync(f.fileno())
continuation=W/('propagation-'+STAGE+'-continue.json')
deadline=time.monotonic()+120
while not os.path.lexists(continuation):
    need(time.monotonic()<deadline); time.sleep(0.05)
c=json.loads(read(continuation))
raw=read(W/('propagation-'+STAGE+'-receipt.json'))
r=json.loads(raw)
need(set(c)=={'nonce','helper_status','receipt_sha256'} and c['nonce']==profile['nonce'] and c['helper_status']==0 and c['receipt_sha256']==hashlib.sha256(raw).hexdigest())
need(r.get('success') is True and r.get('nonce')==profile['nonce'] and r.get('proof',{}).get('nonce')==profile['nonce'])
need(r['proof'].get('namespace_inodes',{}).get('mnt')==ns['mnt'])
for line in pathlib.Path('/proc/self/mountinfo').read_text().splitlines():
    need(not any(v.startswith(('shared:','master:','propagate_from:')) for v in line.split(' - ',1)[0].split()[6:]))
restricted()
need({n:(pathlib.Path('/proc/self/ns')/n).stat().st_ino for n in N}==ns)
need(all(status().get(k)==initial_status.get(k) for k in ('Uid','Gid','NoNewPrivs','CapInh','CapPrm','CapEff','CapBnd','CapAmb')))
'''


def propagation(shared, pipeline, slot, work, name, ready, profile, host_ns, step):
    pid = shared.select_payload(ready, profile, host_ns)
    status_keys = ('Uid', 'Gid', 'NoNewPrivs', 'CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')
    status_path = Path('/proc') / str(pid) / 'status'
    status = shared.status_fields(status_path)
    before_status = {key: status[key] for key in status_keys}
    root = shared.ROOT.stat()
    expected = {'nonce': profile['nonce'], 'namespaces': dict(ready['namespace_inodes'], user=host_ns['user']),
                'host_mount_namespace': host_ns['mnt'], 'root': {'dev': root.st_dev, 'ino': root.st_ino}}
    args = ['/usr/bin/nsenter', '--target', str(pid), '--mount', '--pid', '--net', '--uts', '--ipc',
            '--cgroup', '--root', '--wd', '--', '/usr/bin/python3', '-I', '-B', '-c', PROP_HELPER,
            json.dumps(expected, sort_keys=True)]
    proc = None
    try:
        with (slot / 'propagation.stdout').open('xb') as out, (slot / 'propagation.stderr').open('xb') as err:
            proc = subprocess.Popen(args, stdout=out, stderr=err, start_new_session=True, env=ENV)
            code = proc.wait(timeout=30)
        require(code == 0, 'propagation helper failed')
        require(shared.select_payload(ready, profile, host_ns) == pid, 'propagation payload changed')
        status = shared.status_fields(status_path)
        require({key: status[key] for key in status_keys} == before_status,
                'propagation payload credentials/capabilities changed')
        proof = json.loads(held(slot / 'propagation.stdout', owner=0, limit=16384)[0])
        require(proof == {'success': True, 'nonce': expected['nonce'], 'namespace_inodes': expected['namespaces'],
                          'root': expected['root']}, 'propagation helper proof differs')
        receipt = {'success': True, 'nonce': profile['nonce'], 'payload_pid': pid, 'proof': proof}
        path = work / ('propagation-' + name + '-receipt.json')
        shared.atomic_json(path, receipt)
        shared.atomic_json(work / ('propagation-' + name + '-continue.json'),
                           {'nonce': profile['nonce'], 'helper_status': 0, 'receipt_sha256': held(path, owner=0, limit=16384)[1]['sha256']})
        step['propagation'] = receipt
    finally:
        shared.stop_process(proc)


def stage_inputs(shared, pipeline, phase, name, manifest, contents, bom, deb, repo_sha):
    slot = phase / name
    slot.mkdir(mode=0o700)
    (slot / 'scratch').mkdir(mode=0o700)
    os.chown(slot / 'scratch', shared.UID_BASE, shared.UID_BASE)
    inputs = slot / 'inputs'
    inputs.mkdir(mode=0o755)
    shared.project_snapshot(inputs / 'project', contents)
    shared.atomic_json(inputs / 'manifest.json', manifest)
    pipeline.immutable_file(inputs / 'rootfs-bom.json', bom)
    pipeline.immutable_file(inputs / json.loads(contents[CONTRACT])['addition']['filename'], deb)
    profile = {'nonce': os.urandom(32).hex(), 'host_userns_inode': shared.namespaces()['user'],
               'host_driver_pid': os.getpid(), 'repo_manifest_sha256': repo_sha}
    shared.atomic_json(inputs / 'api-profile.json', profile)
    program = DERIVER if name == 'configure' else ADAPTER
    runner = 'STAGE=' + repr(name) + '\n' + PROP_RUNNER
    runner += ('import importlib.util,pathlib,json\ns=importlib.util.spec_from_file_location("recipe","/a733-sources/project/' + program + '")\n'
              'm=importlib.util.module_from_spec(s)\ns.loader.exec_module(m)\n')
    runner += 'm.main()\n' if name == 'configure' else ('print(json.dumps(m.export(pathlib.Path("/a733-export-output/export-timesync-result"),'
               + repr(held(phase / 'work/timesync-receipt.json', owner=shared.UID_BASE, limit=1024**2)[1]['sha256']) + '),sort_keys=True))\n')
    pipeline.immutable_file(inputs / 'run.py', runner.encode())
    os.chmod(inputs, 0o555)
    return slot, profile


def check_resources(sample, name, elapsed):
    require(sample['free_bytes'] >= RUNTIME_FLOOR and sample['available_memory_bytes'] >= 1_000_000_000
            and elapsed <= TIMEOUTS[name], 'resource floor or timeout exceeded')


def run_phase(shared, pipeline, builder, phase, base, slot, name, profile, step):
    require(shared.resource_sample()['free_bytes'] >= (CONFIG_FLOOR if name == 'configure' else 3_500_000_000), 'phase start disk floor exceeded')
    require(not shared.mapped_processes() and not mounts(shared, phase, base), 'unclean phase boundary')
    args = pipeline.command(shared, builder, phase, slot, name)
    step.update(command=args, resources=[], success=False)
    if profile is not None:
        step['api_profile'] = profile
    proc, setup, private = None, False, False
    try:
        host_ns = shared.namespaces()
        with (slot / 'runtime.log').open('xb') as stream:
            proc = subprocess.Popen(args, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True, env=ENV)
            start = time.monotonic()
            while True:
                sample = shared.resource_sample()
                step['resources'].append(sample)
                check_resources(sample, name, time.monotonic() - start)
                propagation_ready = phase / 'work' / ('propagation-' + name + '-ready.json')
                if not private and os.path.lexists(propagation_ready):
                    propagation(shared, pipeline, slot, phase / 'work', name,
                                json.loads(held(propagation_ready, owner=shared.UID_BASE, limit=16384)[0]),
                                profile, host_ns, step)
                    private = True
                ready = phase / 'work/api-ready.json'
                if name == 'configure' and private and not setup and os.path.lexists(ready):
                    shared.bootstrap_api(pipeline.WorkSlot(slot, phase / 'work'), shared.read_json(ready, owner=shared.UID_BASE), profile, host_ns, step)
                    setup = True
                if proc.poll() is not None:
                    break
                time.sleep(1)
            step['returncode'] = proc.wait(timeout=15)
            stream.flush()
            os.fsync(stream.fileno())
        require(proc.returncode == 0 and private and (name != 'configure' or setup), 'payload failed: ' + name)
        step['probe'] = builder.validate_probe((slot / 'runtime.log').read_bytes())
        path = phase / 'work' / ('timesync-receipt.json' if name == 'configure' else 'export-timesync-result/receipt.json')
        receipt, record = held(path, owner=shared.UID_BASE, limit=1024**2)
        require(json.loads(receipt).get('success') is True, 'recipe receipt failed')
        step.update(recipe_receipt_sha256=record['sha256'], success=True)
    finally:
        shared.stop_process(proc)
        step['mapped_processes_final'] = shared.mapped_processes()
        step['output_mounts_final'] = mounts(shared, phase, base)
        step['success'] = step['success'] and not step['mapped_processes_final'] and not step['output_mounts_final']
    require(step['success'], 'phase cleanup failed')


def verify_export(shared, phase, contents, manifest, configured_sha):
    output = phase / 'work/export-timesync-result'
    receipt = json.loads(held(output / 'receipt.json', owner=shared.UID_BASE, limit=1024**2)[0])
    source_pin = pin((json.dumps(manifest, sort_keys=True) + '\n').encode())
    configured_bytes, configured_pin = held(phase / 'work/timesync-receipt.json', owner=shared.UID_BASE, limit=1024**2)
    require(configured_pin['sha256'] == configured_sha, 'derivation receipt changed after configuration')
    configured = json.loads(configured_bytes)
    require(configured.get('success') is True and configured.get('configured_packages') == 221
            and configured.get('source_manifest') == source_pin and configured.get('inputs') == json.loads(contents[CONTRACT]), 'derivation provenance differs')
    adapter = module(ADAPTER, contents[ADAPTER])
    require(receipt.get('success') is True and receipt.get('phase') == 'final-timesync-userspace-export'
            and receipt.get('profile') == 'timesync-20261106' and receipt.get('derivation_receipt_sha256') == configured_sha
            and receipt.get('configure_receipt_sha256') == configured_sha
            and receipt.get('inventory') == {'packages': 221, 'lock_sha256': pin(contents['configs/host-timesync-packages.lock.tsv'])['sha256']}
            and receipt.get('provenance') == {'adapter': pin(contents[ADAPTER]), 'backend': adapter.BACKEND_PIN,
                  'deriver': adapter.DERIVER_PIN, 'contract': CONTRACT_PIN, 'source_manifest': source_pin}, 'export provenance differs')
    for filename, key in (('rootfs.tar.gz', 'archive'), ('rootfs-bom.json', 'bom')):
        require(held(output / filename, owner=shared.UID_BASE, data=False)[1] == receipt.get(key), 'export bytes differ: ' + key)
    return {'archive': receipt['archive'], 'bom': receipt['bom']}


def execute(shared, pipeline, builder, phase, base, manifest, contents, inputs, before, report):
    bom, deb, repo_sha, base_receipt = inputs
    budget, stable = tree_budget(shared, base)
    needed = max(MIN_INITIAL, CONFIG_FLOOR + budget + 2 * (sum(len(v) for v in contents.values()) + len(bom) + len(deb)) + HEADROOM)
    require(shared.resource_sample()['free_bytes'] >= needed, 'insufficient full-clone and configuration capacity')
    report.update(success=False, phases=[], base_export_receipt=base_receipt, capacity_required=needed)
    phase.mkdir(mode=0o700)
    handlers = {sig: signal.signal(sig, shared.interrupted) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    try:
        (phase / 'work').mkdir(mode=0o700)
        os.chown(phase / 'work', shared.UID_BASE, shared.UID_BASE)
        pipeline.immutable_file(phase / 'host-packages.before.tsv', before)
        shared.atomic_json(phase / 'source-manifest.json', manifest)
        for name in ('arm-root', 'repo'):
            subprocess.run(['/bin/cp', '-a', '--reflink=auto', '--', str(base / name), str(phase / 'work' / name)], check=True, env=ENV)
        require(tree_budget(shared, base)[1] == stable, 'base changed during clone')
        for name in ('arm-root', 'repo'):
            shared.validate_source_tree(phase / 'work' / name)
        for name in TIMEOUTS:
            slot, profile = stage_inputs(shared, pipeline, phase, name, manifest, contents, bom, deb, repo_sha)
            step = {'name': name}
            report['phases'].append(step)
            run_phase(shared, pipeline, builder, phase, base, slot, name, profile, step)
            after = subprocess.check_output(shared.INVENTORY, env=ENV)
            shared.validate_host_inventory(after, contents[HOST_LOCK])
            require(after == before, 'host package inventory changed')
        report['final_export'] = verify_export(shared, phase, contents, manifest, report['phases'][0]['recipe_receipt_sha256'])
        report['success'] = True
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            report['mapped_processes_final'] = shared.mapped_processes()
            report['output_mounts_final'] = mounts(shared, phase, base)
            after = subprocess.check_output(shared.INVENTORY, env=ENV)
            try:
                shared.validate_host_inventory(after, contents[HOST_LOCK])
            except ValueError as error:
                report.update(success=False, final_inventory_error=repr(error))
            report['host_package_inventory_unchanged'] = after == before
            report['success'] = report['success'] and after == before and not report['mapped_processes_final'] and not report['output_mounts_final']
            pipeline.immutable_file(phase / 'host-packages.after.tsv', after)
            shared.atomic_json(phase / 'receipt.json', report)
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    require(report['success'], 'final cleanup failed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--base-tag', required=True)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--extra-deb', required=True, type=Path)
    parser.add_argument('--plan', action='store_true')
    options = parser.parse_args(argv)
    phase, base = tag_path(options.tag), tag_path(options.base_tag, 'userspace-pipeline-') / 'work'
    absolute(options.extra_deb)
    manifest, contents = committed_sources(options.project)
    require(held(Path(__file__).absolute(), limit=16 * 1024**2)[0] == contents[SELF], 'executing source differs from committed launcher')
    report = plan(options.tag, options.base_tag, options.extra_deb, manifest)
    if options.plan:
        print(json.dumps(report, sort_keys=True))
        return
    shared, pipeline = module(SHARED, contents[SHARED]), module(PIPELINE, contents[PIPELINE])
    builder, before, facts = preflight(shared, pipeline, phase, base, contents)
    report.update(facts)
    inputs = base_inputs(shared, base, contents, options.extra_deb, builder)
    execute(shared, pipeline, builder, phase, base, manifest, contents, inputs, before, report)
    print(json.dumps({'success': True, 'phase': str(phase), 'export': str(phase / 'work/export-timesync-result')}))


if __name__ == '__main__':
    main()

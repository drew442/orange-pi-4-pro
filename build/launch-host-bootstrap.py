#!/usr/bin/env python3
"""Build a regular-file EL2 host bootstrap in the frozen offline builder.

Invoke with Python -I -B. Failed phases are retained. No devices, mounts,
installation, SPI operation or physical acceptance are part of this launcher.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

SELF = 'build/launch-host-bootstrap.py'
ROOTS = 'build/launch-host-roots.py'
STATE = 'build/launch-host-state.py'
VERIFIER = 'build/verify-host-roots.py'
PRODUCTION = 'docs/evidence/production-host-state-20261107.json'
BASELINE = 'docs/evidence/host-userspace-20261104.json'
EL2 = 'docs/evidence/el2-diagnostic-20261104.json'
BASE = Path('/var/lib/a733-clean-replay-20261106')
STATE_PHASE = BASE / 'host-state-timesync-02'
STATE_OUTPUT = STATE_PHASE / 'work/project/out/host-state-timesync-02'
STATE_UUID = 'fc8bbef6-9385-4ddb-90ba-e41eaccc035b'
STATE_RECEIPT_SHA = '3abeb7ba014eae05acebfd7af3b75dab7c4c58999b96d8c752163bb96a2ccd33'
BOOT_FILES = ('Image', 'board.dtb', 'kernel.config', 'initramfs.cpio.gz',
              'uImage', 'boot0_sdcard.fex', 'boot_package.fex')
IMAGE_BYTES = 256 * 1024**2
SD_BYTES = 1977614336
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C', 'TZ': 'UTC'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def phase_path(tag):
    require(isinstance(tag, str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', tag), 'unsafe tag')
    return BASE / ('host-bootstrap-' + tag)


def syntax_path(path):
    require(path.is_absolute() and str(path) == os.path.abspath(path) and
            not any(path == p or p in path.parents for p in map(Path, ('/dev', '/proc', '/sys'))),
            'canonical absolute non-pseudo input required')


def contract(roots, project, contents):
    state = roots.module(str(project / STATE), contents[STATE])
    state_policy = state.contract(roots, project, contents)
    producer = json.loads(contents[PRODUCTION])
    require(producer['schema'] == 1 and producer['phase'] == str(STATE_PHASE) and
            producer['output'] == 'work/project/out/host-state-timesync-02' and
            producer['state_uuid'] == STATE_UUID and producer['state_bytes'] == 8 * 1024**3 and
            producer['source_revision'] == 'e68f03531e3fab56deb5c883ccb7a5dca13baff8' and
            producer['profile'] == roots.PROFILE and producer['metadata_profile'] == roots.METADATA and
            producer['producer_records']['receipt.json']['sha256'] == STATE_RECEIPT_SHA and
            producer['readback']['filesystem']['full_namespace_metadata_content_readback'] is True and
            producer['readback']['input_identity_unchanged'] is True and
            producer['readback']['filesystem']['image'] == producer['state_image'] and
            producer['boot_selection'] == producer['verification']['boot_selection']['selector'],
            'committed production state identity/readback differs')
    baseline = json.loads(contents[BASELINE])
    require(baseline['schema'] == 1, 'baseline userspace schema differs')
    archive = baseline['files']['rootfs.tar.gz']
    diagnostic = json.loads(contents[roots.DIAGNOSTIC])
    el2 = json.loads(contents[EL2])
    require(diagnostic['schema'] == el2['schema'] == 1 and
            el2['handoff']['expected_service'] == '0x8000ff40' and
            el2['handoff']['expected_spsr'] == '0x3c9', 'EL2 evidence differs')
    pins = {name: diagnostic['files'][name] for name in BOOT_FILES}
    for name in BOOT_FILES:
        if name != 'initramfs.cpio.gz' and name != 'boot_package.fex':
            require(el2['files'][name] == pins[name], 'EL2 immutable diagnostic input differs')
    require(el2['files']['boot_package.fex']['bytes'] == pins['boot_package.fex']['bytes'] == 1392640 and
            el2['files']['boot_package.fex'] != pins['boot_package.fex'], 'EL2 boot package differs')
    pins['boot_package.fex'] = el2['files']['boot_package.fex']
    busybox = {'bytes': 1794520, 'sha256': json.loads(contents['configs/diagnostic-sources.lock.json'])['busybox']['binary_sha256']}
    verifier = roots.module(VERIFIER, contents[VERIFIER])
    for item in (archive, busybox, *pins.values()):
        verifier.validate_pin(item)
    assembly = roots.module(str(project / 'build/assemble-host.py'), contents['build/assemble-host.py'])
    sd_recipes = {name: roots.pin(contents[name]) for name in (*assembly.RECIPES, EL2)}
    init_recipes = {name: roots.pin(contents[name]) for name in (
        'build/host-initramfs.py', 'build/host-payload.py', 'build/host-storage-tools.py',
        'configs/host-storage-tools.lock.json', 'configs/package_state.py')}
    return {'archive': archive, 'diagnostic': pins, 'busybox': busybox, 'state_uuid': STATE_UUID,
            'sd_recipes': sd_recipes, 'initramfs_recipes': init_recipes,
            'state_producer': producer, 'state_policy': state_policy,
            'boot_profile': {'name': 'experimental-el2', 'target_service': '0x8000ff40',
                'expected_spsr': '0x3c9', 'physical_acceptance_pending': True,
                'evidence': {'path': EL2, **roots.pin(contents[EL2])}}}


def plan(options, manifest, policy):
    for path in (options.userspace, options.diagnostic, options.busybox, options.state_receipt):
        syntax_path(path)
    require(options.state_receipt == STATE_PHASE / 'receipt.json', 'unexpected state receipt path')
    require(options.state_receipt_sha256 == STATE_RECEIPT_SHA ==
            policy['state_producer']['producer_records']['receipt.json']['sha256'], 'state receipt SHA differs')
    return {'schema': 1, 'phase': str(phase_path(options.tag)), 'sources': manifest, 'policy': policy,
            'userspace_input': str(options.userspace), 'diagnostic_input': str(options.diagnostic),
            'busybox_input': str(options.busybox), 'state_receipt': str(options.state_receipt),
            'state_receipt_sha256': options.state_receipt_sha256, 'state_uuid': policy['state_uuid'],
            'userspace_profile': 'original-baseline-storage-tools-only', 'boot_profile': 'experimental-el2',
            'output': 'work/project/out/host-sd-' + options.tag,
            'initramfs_output': 'work/project/out/host-initramfs-' + options.tag, 'work_mode': 'disk',
            'initial_required_bytes': '4 GiB + capsule + Git staging bound',
            'minimum_initial_available_memory_bytes': 3 * 1024**3,
            'runtime_minimum_free_bytes': 3_000_000_000,
            'runtime_minimum_available_memory_bytes': 1_000_000_000, 'timeout_seconds': 3600,
            'image_bytes': IMAGE_BYTES, 'actual_sd_bytes': SD_BYTES,
            'hardware_acceptance': False, 'physical_storage_written': False,
            'reproducibility': 'not established'}


def authenticate_state(verifier, held, policy):
    producer = policy['state_producer']
    receipt = verifier.json_fd(held['state-receipt.json'])
    manifest = verifier.json_fd(held['state-manifest.json'])
    readback = verifier.json_fd(held['state-readback.json'])
    require(receipt['success'] is True and receipt['state'] == 'complete' and
            receipt['phase'] == str(STATE_PHASE) and receipt['output'] == producer['output'] and
            receipt['state_uuid'] == policy['state_uuid'] and receipt['state_bytes'] == 8 * 1024**3 and
            receipt['sources']['revision'] == producer['source_revision'] and
            receipt['userspace_profile'] == producer['profile'] and
            receipt['metadata_profile'] == producer['metadata_profile'] and
            receipt['policy'] == policy['state_policy'] and
            receipt['verification'] == producer['verification'] and
            receipt['verification']['manifest'] == held['state-manifest.json'].pin,
            'state producer receipt differs')
    require(manifest['schema'] == 1 and manifest['state_uuid'] == policy['state_uuid'] and
            manifest['state_bytes'] == 8 * 1024**3 and manifest['boot_selection'] == producer['boot_selection'] and
            manifest['files']['state.ext4'] == producer['state_image'] and
            manifest['sources']['rootfs.tar.gz'] == policy['state_policy']['archive'] and
            manifest['sources']['userspace_profile'] == policy['state_policy']['binding'] and
            manifest['sources']['timesync_state'] == policy['state_policy']['clock'],
            'state manifest differs')
    require(readback['schema'] == 1 and readback['success'] is True and readback['state'] == 'complete' and
            readback['full_namespace_metadata_content_readback'] is True and
            readback['input_identity_unchanged'] is True and
            readback['producer_receipt'] == held['state-receipt.json'].pin and
            readback['state_manifest'] == held['state-manifest.json'].pin and
            readback['source_policy'] == policy['state_policy'] and
            readback['parent_root_marker'] == producer['parent_roots']['readback_expected'] and
            readback['filesystem'] == producer['readback']['filesystem'] and
            readback['image'] == producer['state_image'], 'state independent readback proof differs')


def input_records(options, policy):
    producer = policy['state_producer']
    records = {'rootfs.tar.gz': (options.userspace, policy['archive']),
               'busybox': (options.busybox, policy['busybox']),
               'state-receipt.json': (options.state_receipt, producer['producer_records']['receipt.json']),
               'state-manifest.json': (STATE_OUTPUT / 'manifest.json', producer['verification']['manifest']),
               'state-readback.json': (Path(producer['readback']['output']) / 'receipt.json', producer['readback']['receipt'])}
    records.update({'diagnostic/' + name: (options.diagnostic / name, policy['diagnostic'][name])
                    for name in BOOT_FILES})
    return records


def budget(roots, manifest, project, held):
    objects = sum(int(row.split()[2]) for row in roots.git(project, 'cat-file', '--batch-all-objects',
                  '--batch-check=%(objectname) %(objecttype) %(objectsize)').decode().splitlines())
    require(objects <= 512 * 1024**2, 'Git closure exceeds bound')
    checkout = sum(row['bytes'] + 4096 for row in manifest['files'].values())
    capsule = sum(item.pin['bytes'] for item in held.values())
    git_bound = 3 * objects + checkout + 64 * 1024**2
    return {'recipe_bytes': 4 * 1024**3, 'capsule_bytes': capsule,
            'git_staging_bound_bytes': git_bound, 'required_free_bytes': 4 * 1024**3 + capsule + git_bound}


def scratch_anchor(shared):
    path = shared.ROOT / 'var/tmp'
    for parent in (shared.ROOT / 'var', path):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == shared.UID_BASE,
                'frozen scratch anchor identity differs')
    require(stat.S_IMODE(path.lstat().st_mode) == 0o1777 and not list(path.iterdir()),
            'frozen scratch anchor must be empty 1777 directory')


def preflight(roots, shared, contents, phase, required):
    require(shared.resource_sample()['available_memory_bytes'] >= 3 * 1024**3, 'need 3 GiB MemAvailable')
    builder, before = roots.preflight(shared, contents, phase, required)
    scratch_anchor(shared)
    return builder, before


def command(roots, shared, builder, phase):
    args = roots.command(shared, builder, phase)
    args.insert(-3, '--bind=' + str(phase / 'work/private-scratch') + ':/var/tmp')
    return args


def payload_program(tag, selected_uuid):
    phase_path(tag)
    require(selected_uuid == STATE_UUID, 'unexpected state UUID')
    return (r'''import importlib.util,json,os,pathlib
os.umask(0o022)
os.environ.update(SOURCE_DATE_EPOCH='1793750400',TMPDIR='/var/tmp',LC_ALL='C',TZ='UTC')
p=pathlib.Path('/a733-export-output/project')
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path)
 value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
init=module('host_initramfs',p/'build/host-initramfs.py')
assembly=module('host_assembly',p/'build/assemble-host.py')
inspection=module('host_inspection',p/'tests/verify_host_sd.py')
boot=pathlib.Path('/a733-debs/diagnostic')
init.build(pathlib.Path('/a733-debs/rootfs.tar.gz'),boot,pathlib.Path('/a733-debs/busybox'),UUID,p/INIT)
assembly.build(boot,p/INIT,p/OUTPUT,boot_profile='experimental-el2')
proof=inspection.verify(p/OUTPUT,p/INIT,boot_profile='experimental-el2')
readback=pathlib.Path('/a733-export-output/readback');readback.mkdir(mode=0o700)
with (readback/'bootstrap.json').open('x') as stream:
 json.dump(proof,stream,sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
'''.replace('UUID', repr(selected_uuid)).replace('INIT', repr('out/host-initramfs-' + tag))
        .replace('OUTPUT', repr('out/host-sd-' + tag))).encode()


def stage_inputs(roots, candidate, shared, project, phase, manifest, policy, held, tag):
    for item in held.values():
        item.unchanged()
    inputs = phase / 'inputs'; inputs.mkdir(mode=0o700)
    (inputs / 'diagnostic').mkdir(mode=0o700)
    scratch = phase / 'work/private-scratch'; scratch.mkdir(mode=0o700)
    os.chown(scratch, shared.UID_BASE, shared.UID_BASE)
    for name, item in held.items():
        candidate.authenticated_file(item.path, item.pin, destination=inputs / name)
    subprocess.run(['/usr/bin/git', '-C', str(project), 'bundle', 'create', str(inputs / 'project.bundle'), 'HEAD'],
                   env=ENV, check=True, timeout=120)
    require(roots.committed_sources(project)[0] == manifest, 'source changed during staging')
    shared.atomic_json(inputs / 'manifest.json', manifest)
    (inputs / 'run.py').write_bytes(roots.inner_program(manifest['revision'], tag))
    (inputs / 'payload.py').write_bytes(payload_program(tag, policy['state_uuid']))
    for item in inputs.rglob('*'):
        os.chown(item, 0, 0); os.chmod(item, 0o555 if item.is_dir() else 0o444)
    os.chmod(inputs, 0o555)
    for item in held.values():
        item.unchanged()
    return candidate.authenticated_file(inputs / 'project.bundle')[0]


def verify_capsule(candidate, inputs, held):
    require({str(p.relative_to(inputs)) for p in candidate.regular_tree_files(inputs)} == set(held),
            'private capsule namespace changed')
    for path in (inputs, inputs / 'diagnostic'):
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0 and
                stat.S_IMODE(info.st_mode) == 0o555, 'private capsule directory metadata differs')
    for item in held.values():
        require(item.before.st_uid == item.before.st_gid == 0 and
                stat.S_IMODE(item.before.st_mode) == 0o444, 'private capsule leaf metadata differs')
        item.unchanged()


def verify_manifest(candidate, output):
    manifest_pin, raw = candidate.authenticated_file(output / 'manifest.json', retain=True)
    value = json.loads(raw)
    require(value['schema'] == 1, 'output manifest schema differs')
    actual = {str(p.relative_to(output)) for p in candidate.regular_tree_files(output)} - {'manifest.json'}
    require(actual == set(value['files']), 'manifest file coverage differs')
    for name, expected in value['files'].items():
        candidate.safe_member(name); candidate.authenticated_file(output / name, expected)
    return manifest_pin, value


def verify_output(roots, candidate, output, init_output, policy, contents):
    sd_pin, sd = verify_manifest(candidate, output)
    init_pin, init = verify_manifest(candidate, init_output)
    require(sd['state_uuid'] == init['state_uuid'] == policy['state_uuid'] and
            sd['kernel_release'] == init['kernel_release'] == '6.6.98-a733-diag' and
            sd['package_state_profile'] == init['package_state_profile'] == 'a733-package-state-v1' and
            sd['boot_profile'] == policy['boot_profile'] and sd['board'] == 'Orange Pi 4 Pro A733' and
            sd['source_date_epoch'] == 1793750400 and
            sd['root_selection'] == init['root_selection'] ==
            'NVMe state boot/selected.conf; root A/B not embedded', 'bootstrap UUID/EL2/ABI differs')
    require(sd['boot_inputs'] == {name: policy['diagnostic'][name] for name in BOOT_FILES if name != 'initramfs.cpio.gz'} and
            init['boot_inputs'] == json.loads(contents[roots.DIAGNOSTIC])['files'], 'bootstrap diagnostic pins differ')
    require(set(sd['sources']) == set(sd['boot_inputs']), 'bootstrap source namespace differs')
    for name, expected in sd['boot_inputs'].items():
        require(sd['sources'][name] == {'path': '/a733-debs/diagnostic/' + name, **expected},
                'bootstrap source capsule path differs')
    require(sd['recipes'] == policy['sd_recipes'] and init['recipes'] == policy['initramfs_recipes'],
            'recipe source closure differs')
    require(sd['source_bom'] == json.loads(contents['configs/diagnostic-sources.lock.json']) and
            sd['boot_command'] == roots.pin(contents['configs/host-boot.cmd']), 'bootstrap source policy differs')
    for field, expected, name in (('host_initramfs', init['files']['initramfs.cpio.gz'], 'initramfs.cpio.gz'),
                                  ('trusted_host_manifest', init_pin, 'manifest.json')):
        namespace_path = '/a733-export-output/project/out/' + init_output.name + '/' + name
        require(sd[field] == dict(path=namespace_path, **expected), 'bootstrap initramfs binding differs')
    for name, source in (('init', 'configs/host-init'),
                          ('etc/host-storage-resolver.sh', 'configs/host-storage-resolver.sh')):
        require(init['entries'][name] == dict(mode=stat.S_IFREG | 0o755, **roots.pin(contents[source])),
                'bootstrap reviewed entry differs')
    require(sd['files']['a733-host-sd.img']['bytes'] == IMAGE_BYTES < SD_BYTES and
            set(sd['files']) == {'a733-host-sd.img', 'a733-host-sd.img.gz', 'uInitrd', 'assembly.log'} and
            set(init['files']) == {'initramfs.cpio.gz'}, 'bootstrap output geometry/files differ')
    return {'manifest': sd_pin, 'initramfs_manifest': init_pin, 'files_verified': len(sd['files']) + len(init['files']),
            'image_bytes': IMAGE_BYTES, 'fits_actual_sd': True, 'hardware_acceptance': False}


def verify_expectations(candidate, root, image_pin, selected_uuid):
    require({p.name for p in candidate.regular_tree_files(root)} == {'bootstrap.json'}, 'inspection namespace differs')
    proof_pin, raw = candidate.authenticated_file(root / 'bootstrap.json', retain=True)
    proof = json.loads(raw)
    require(proof['image'] == image_pin and proof['state_uuid'] == selected_uuid and
            proof['physical_storage_writes'] is False and
            type(proof['initramfs_entries_checked']) is int and proof['initramfs_entries_checked'] > 0 and
            not set(proof['legacy_ext4_features']) & {'has_journal', '64bit', 'metadata_csum', 'metadata_csum_seed'} and
            all(proof[field] == 'passed' for field in ('mbr_and_raw_blobs', 'pinned_kernel_and_dtb',
                'host_script_crc_and_contents', 'initrd_wrapper_and_payload', 'gzip_expansion')),
            'bootstrap raw image inspection differs')
    return {'inspection': proof_pin, 'checks': proof, 'hardware_acceptance': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('project', 'userspace', 'diagnostic', 'busybox', 'state-receipt'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('tag', 'state-receipt-sha256'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--plan', action='store_true')
    options = parser.parse_args(argv)
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    syntax_path(options.project)
    root_source = subprocess.check_output(['/usr/bin/git', '-C', str(options.project), 'show', 'HEAD:' + ROOTS], env=ENV)
    # Load only the committed root launcher before authenticating its full closure.
    import types
    roots = types.ModuleType('bootstrap_roots'); roots.__file__ = str(options.project / ROOTS)
    exec(compile(root_source, roots.__file__, 'exec'), roots.__dict__)
    manifest, contents = roots.committed_sources(options.project)
    require(roots.read_source(Path(__file__).absolute()) == contents[SELF] and root_source == contents[ROOTS],
            'executing launcher differs from committed source')
    policy = contract(roots, options.project, contents)
    report = plan(options, manifest, policy)
    if options.plan:
        print(json.dumps(report, sort_keys=True)); return
    state = roots.module(STATE, contents[STATE]); verifier = roots.module(VERIFIER, contents[VERIFIER])
    Held = state.held_class(verifier)
    shared = roots.module(roots.SHARED, contents[roots.SHARED])
    candidate = roots.module(roots.CANDIDATE, contents[roots.CANDIDATE])
    with contextlib.ExitStack() as stack:
        held = {name: stack.enter_context(Held(path, expected))
                for name, (path, expected) in input_records(options, policy).items()}
        authenticate_state(verifier, held, policy)
        required = budget(roots, manifest, options.project, held); report['initial_budget'] = required
        phase = phase_path(options.tag)
        builder, before = preflight(roots, shared, contents, phase, required)
        capsule = {}
        def stage(c, s, project, ph, m, pol, unused_u, unused_d, tag):
            bundle = stage_inputs(roots, c, s, project, ph, m, pol, held, tag)
            for path in c.regular_tree_files(ph / 'inputs'):
                capsule[str(path.relative_to(ph / 'inputs'))] = stack.enter_context(Held(path))
            verify_capsule(c, ph / 'inputs', capsule)
            return bundle
        output = phase / 'work/project/out' / ('host-sd-' + options.tag)
        init_output = output.parent / ('host-initramfs-' + options.tag)
        def final_check():
            for item in held.values(): item.unchanged()
            authenticate_state(verifier, held, policy)
            verify_capsule(candidate, phase / 'inputs', capsule)
            require(roots.committed_sources(options.project)[0] == manifest, 'source closure changed during build')
        def verify(c, actual_output, pol):
            require(actual_output == output, 'bootstrap output path differs')
            final_check()
            return verify_output(roots, c, output, init_output, pol, contents)
        def expectations(c, root):
            _, sd = verify_manifest(c, output)
            return verify_expectations(c, root, sd['files']['a733-host-sd.img'], policy['state_uuid'])
        roots.execute(shared, candidate, builder, options.project, phase, manifest, policy, before, report,
                      options.userspace, options.diagnostic, options.tag, output_name='host-sd-' + options.tag,
                      stage=stage, namespace_command=lambda s, b, ph: command(roots, s, b, ph),
                      verify=verify, expectations=expectations, final_check=final_check)
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()

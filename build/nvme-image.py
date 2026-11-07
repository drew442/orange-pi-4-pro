#!/usr/bin/env python3
"""Integrate host payloads into a new sparse NVMe disk file, never a device.

This is installation preparation, not a physical writer or hardware acceptance.
The caller supplies trusted build manifests, not arbitrary downloaded metadata.
"""
import argparse
import hashlib
import importlib.util
import json
import ipaddress
import re
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import zlib

REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


HOST = module('nvme_image_host', 'build/assemble-host.py')
SELECT = module('nvme_image_selection', 'build/root-selection.py')
LAYOUT = module('nvme_image_layout', 'scripts/nvme_layout.py')
STATE = module('nvme_image_state', 'build/host-state.py')
GIB = 1024**3
RECIPES = ('build/nvme-image.py', 'scripts/nvme_layout.py', 'build/assemble-host.py',
           'build/root-selection.py', 'build/host-initramfs.py', 'build/host-payload.py',
           'build/host-state.py', 'configs/host_config.py', 'configs/package_state.py',
           'configs/host-boot.cmd', 'configs/builder-packages.lock.tsv')


def metadata(directory):
    directory = HOST.safe_path(directory)
    if not directory.is_dir():
        raise ValueError('input directory required')
    data = HOST.read_regular(directory / 'manifest.json', 4 * 1024**2)
    return directory, HOST.json_object(data), HOST.record(data)


def canonical_uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value or uuid.UUID(value).int == 0:
        raise ValueError('canonical nonzero UUID required')
    return value


def debugfs(image, command):
    return subprocess.run(['debugfs', '-R', command, str(image)], check=True,
                          capture_output=True).stdout


def validate_roots(manifest):
    payload = SELECT.INIT.PAYLOAD
    profile = manifest.get('management_runtime', {})
    if (manifest.get('schema') != 1 or manifest.get('slot_bytes') != payload.SLOT_BYTES or
            manifest.get('slot_uuids') != payload.SLOT_UUIDS or
            manifest.get('kernel_release') != payload.KERNEL_RELEASE or
            profile.get('profile') != payload.management_profile(manifest) or
            set(profile.get('files', {})) != {v[0] for v in payload.MANAGEMENT_FILES.values()}):
        raise ValueError('incompatible managed root manifest')
    payload.PACKAGE.validate_profile(manifest.get('package_state'))
    for slot in ('A', 'B'):
        if manifest['files'][f'root-{slot}.ext4']['bytes'] != 4 * GIB:
            raise ValueError('root payload must fill its fixed slot')


def validate_pairing(roots, state, sd, diagnostic, boot_profile='baseline-el1'):
    if boot_profile not in HOST.BOOT_PROFILES:
        raise ValueError('unknown boot profile')
    selected = sd.get('boot_profile')
    if 'boot_profile' not in sd and boot_profile == 'baseline-el1':
        # Historical baseline manifests predate explicit profile metadata.
        pinned, _, _ = HOST.load_boot_profile(boot_profile)
        if any(sd.get('boot_inputs', {}).get(name) != pinned[name] for name in HOST.BLOBS):
            raise ValueError('historical SD boot profile locked inputs differ')
    elif not isinstance(selected, dict) or selected.get('name') != boot_profile:
        raise ValueError('SD boot profile differs from requested profile')
    else:
        pinned, expected, _ = HOST.load_boot_profile(boot_profile)
        if selected != expected or any(sd.get('boot_inputs', {}).get(name) != pinned[name] for name in HOST.BLOBS):
            raise ValueError('SD boot profile provenance or locked inputs differ')
    validate_roots(roots)
    payload = SELECT.INIT.PAYLOAD
    root_clock = payload.CONFIG.timesync_policy(roots)
    state_clock = payload.CONFIG.timesync_policy(state)
    if root_clock is not None or state_clock is not None:
        payload.validate_source_bindings(roots, state)
    state_uuid = canonical_uuid(state['state_uuid'])
    if state_uuid in roots['slot_uuids'].values() or state['files']['state.ext4']['bytes'] != 8 * GIB:
        raise ValueError('incompatible state identity/size')
    if sd.get('package_state_profile') != SELECT.INIT.PAYLOAD.PACKAGE.PROFILE:
        raise ValueError('bootstrap lacks required package-state ABI')
    if sd.get('state_uuid') != state_uuid or sd.get('kernel_release') != HOST.KERNEL_RELEASE:
        raise ValueError('state and immutable bootstrap differ')
    for name in ('Image', 'board.dtb', 'kernel.config'):
        if sd['boot_inputs'][name] != diagnostic[name]:
            raise ValueError('bootstrap and pinned vendor stack differ')
    if sd['boot_command'] != HOST.record((REPO / 'configs/host-boot.cmd').read_bytes()):
        raise ValueError('bootstrap boot command differs from current source')
    SELECT.parse(SELECT.serialize(state['boot_selection']))
    if state['boot_selection']['KERNEL_RELEASE'] != HOST.KERNEL_RELEASE:
        raise ValueError('state selector ABI differs')
    return state_uuid


def admin_fingerprints(state):
    """Accept historical single-key metadata and cross-check new ordered lists."""
    plural = state.get('admin_public_key_fingerprints')
    legacy = state.get('admin_public_key_fingerprint')
    values = plural if plural is not None else [legacy]
    if (not isinstance(values, list) or not 1 <= len(values) <= STATE.COMMON.MAX_PUBLIC_KEYS or
            any(not isinstance(value, str) or not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', value)
                for value in values) or len(set(values)) != len(values)):
        raise ValueError('invalid administration public-key fingerprints')
    if 'admin_public_key_fingerprint' in state and values != [legacy]:
        raise ValueError('legacy and plural administration fingerprints differ')
    if 'admin_public_key_fingerprints' in state and plural is None:
        raise ValueError('invalid administration public-key fingerprints')
    return values


def installation_intent(state, for_installation=False):
    """Record operator build intent; this is never physical-write authorization."""
    if type(for_installation) is not bool:
        raise ValueError('installation intent must be boolean')
    config = STATE.validate_config(json.dumps(state['management']))
    fingerprints = admin_fingerprints(state)
    if for_installation:
        documentation = ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24', '127.0.0.0/8', '224.0.0.0/3')
        network = ipaddress.IPv4Network(config['management_cidr'])
        if ('SHA256:zJDXpqimTuhgMi8BhG83V7SeYsDurn/2Dmt9g/R3Fzc' in fingerprints or
                config['management_mac'] in ('02:73:30:00:00:01', '02:11:22:33:44:55') or
                (config['management_cidr'] != '0.0.0.0/0' and
                 any(network.overlaps(ipaddress.IPv4Network(value)) for value in documentation))):
            raise ValueError('fixture credentials/network values cannot mark an installation candidate')
    return {'for_installation': for_installation,
            'administration_public_key_fingerprints': fingerprints,
            **({'administration_public_key_fingerprint': fingerprints[0]} if len(fingerprints) == 1 else {}),
            'management_mac': config['management_mac'], 'management_cidr': config['management_cidr']}


def validate_state_identity(image, state):
    actual = STATE.validate_config(debugfs(image, 'cat /config/management.json'))
    key_data = debugfs(image, 'cat /root/.ssh/authorized_keys')
    canonical, fingerprints = STATE.validate_public_keys(key_data)
    identity = debugfs(image, 'cat /identity/machine-id')
    if (key_data != canonical or actual != state['management'] or fingerprints != admin_fingerprints(state) or
            identity != (state['machine_id'] + '\n').encode() or
            re.fullmatch(rb'[0-9a-f]{32}\n', identity) is None or identity == b'0' * 32 + b'\n'):
        raise ValueError('captured state configuration, administrator or machine identity differs')
    for path in ('/config/management.json', '/root/.ssh/authorized_keys', '/identity/machine-id'):
        info = debugfs(image, 'stat ' + path).decode()
        # Retain validation of historical 0600 images; new seeds use readable
        # machine-id for unprivileged system services. Keys/config stay private.
        mode = r'(?:600|644)' if path == '/identity/machine-id' else r'600'
        if not re.search(r'Type:\s+regular\s+Mode:\s+0?' + mode + r'\b', info) or not re.search(r'User:\s+0\s+Group:\s+0\b', info):
            raise ValueError('state identity file metadata differs')
    info = debugfs(image, 'stat /identity/ssh').decode()
    if not re.search(r'Type:\s+directory\s+Mode:\s+0?700\b', info) or not re.search(r'User:\s+0\s+Group:\s+0\b', info):
        raise ValueError('SSH identity directory metadata differs')
    listing = debugfs(image, 'ls -p /identity/ssh').decode()
    names = []
    for line in listing.splitlines():
        if line.strip():
            fields = line.split('/')
            if len(fields) < 7:
                raise ValueError('unexpected SSH identity directory listing')
            names.append(fields[5])
    if sorted(names) != ['.', '..']:
        raise ValueError('installation state must not embed SSH host keys')
    validate_clock_directory(image, state)


def validate_clock_directory(image, state):
    if SELECT.INIT.PAYLOAD.CONFIG.timesync_policy(state) is None:
        return
    for path, owner in (('/var', 0), ('/var/lib', 0), ('/var/lib/systemd', 0),
                        ('/var/lib/systemd/timesync', 996)):
        info = debugfs(image, 'stat ' + path).decode()
        if (not re.search(r'Type:\s+directory\s+Mode:\s+0?755\b', info) or
                not re.search(rf'User:\s+{owner}\s+Group:\s+{owner}\b', info)):
            raise ValueError('state clock directory metadata differs')
    names = []
    for line in debugfs(image, 'ls -p /var/lib/systemd/timesync').decode().splitlines():
        if line.strip():
            fields = line.split('/')
            if len(fields) < 7:
                raise ValueError('unexpected clock directory listing')
            names.append(fields[5])
    if sorted(names) != ['.', '..']:
        raise ValueError('fresh installation state must not embed a saved clock')


def write_all(fd, data, offset):
    while data:
        count = os.pwrite(fd, data, offset)
        if count <= 0:
            raise OSError('short image write')
        offset += count
        data = data[count:]


def place_sparse(source, destination, offset, budget):
    """Only for a NEW zero-filled regular output; never use on physical media."""
    target = os.fstat(destination)
    if not stat.S_ISREG(target.st_mode) or target.st_nlink != 1:
        raise ValueError('sparse placement requires private regular destination')
    source = HOST.safe_path(source)
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_size != budget or offset < 0 or
                offset + budget > target.st_size):
            raise ValueError('partition/source bounds mismatch')
        # New mke2fs files and private captures are sparse. Copy allocated ranges;
        # holes remain zeros in the newly reserved destination. No device opens.
        cursor = 0
        while cursor < budget:
            try:
                start = os.lseek(fd, cursor, os.SEEK_DATA)
            except OSError as error:
                import errno
                if error.errno == errno.ENXIO:
                    break
                if error.errno not in (errno.EINVAL, errno.ENOTSUP):
                    raise
                start = cursor
                end = budget
            else:
                end = min(os.lseek(fd, start, os.SEEK_HOLE), budget)
            if not cursor <= start < end <= budget:
                raise ValueError('invalid sparse extent bounds')
            while start < end:
                data = os.pread(fd, min(1024**2, end - start), start)
                if not data:
                    raise ValueError('source shortened during placement')
                if data.strip(b'\0'):
                    write_all(destination, data, offset + start)
                start += len(data)
            cursor = end
    finally:
        os.close(fd)


def make_filesystem(image, size, label, identity, seed, legacy=False):
    with image.open('xb') as target:
        target.truncate(size)
    arguments = ['mke2fs', '-q', '-F', '-t', 'ext4', '-b', '4096', '-L', label,
                 '-U', identity, '-d', str(seed), '-E', 'lazy_itable_init=0,lazy_journal_init=0']
    if legacy:
        arguments += ['-O', '^has_journal,^metadata_csum,^64bit']
    else:
        arguments += ['-T', 'largefile4']
    subprocess.run([*arguments, str(image)], check=True, capture_output=True)
    root_mode = '040755' if legacy else '040700'
    subprocess.run(['debugfs', '-w', '-f', '-', str(image)],
                   input=f'set_inode_field / mode {root_mode}\nset_inode_field / uid 0\nset_inode_field / gid 0\n',
                   text=True, check=True, capture_output=True)
    details = debugfs(image, 'stat /').decode()
    import re
    if not re.search(r'Mode:\s+0?' + ('755' if legacy else '700') + r'\b', details) or not re.search(r'User:\s+0\s+Group:\s+0\b', details):
        raise ValueError('new filesystem root metadata differs')
    subprocess.run(['e2fsck', '-fn', str(image)], check=True, capture_output=True)


def root_selection(snapshot, slot, roots, diagnostic):
    with snapshot.open('rb') as source:
        marker, value = SELECT.INIT.root_marker(source, slot)
    if value.get('package_state') != roots.get('package_state'):
        raise ValueError('captured root package-state differs from trusted manifest')
    if value['management_runtime'] != roots['management_runtime']:
        raise ValueError('captured root runtime differs from trusted manifest')
    SELECT.INIT.PAYLOAD.validate_source_bindings(value, roots)
    for name in ('Image', 'board.dtb', 'kernel.config', 'initramfs.cpio.gz'):
        if value['sources'][name] != diagnostic[name]:
            raise ValueError('captured root boot stack differs')
    return {'SCHEMA': '1', 'SLOT': slot, 'ROOT_UUID': roots['slot_uuids'][slot],
            'ROOT_MARKER_SHA256': hashlib.sha256(marker).hexdigest(),
            'KERNEL_RELEASE': HOST.KERNEL_RELEASE}


def build(payload_directory, state_directory, sd_directory, diagnostic_directory,
          output_directory, size_bytes, logical_sector_bytes=512, physical_sector_bytes=4096, for_installation=False, boot_profile='baseline-el1'):
    diagnostic, profile, profile_recipes = HOST.load_boot_profile(boot_profile)
    if os.geteuid() != 0:
        raise ValueError('root required to seed root-owned filesystem metadata')
    # Geometry and all supplied manifests are checked before reserving output.
    gpt = LAYOUT.encode_gpt(size_bytes, logical_sector_bytes, physical_sector_bytes)
    payload_directory, roots, root_record = metadata(payload_directory)
    state_directory, state, state_record = metadata(state_directory)
    sd_directory, sd, sd_record = metadata(sd_directory)
    diagnostic_directory = HOST.safe_path(diagnostic_directory)
    state_uuid = validate_pairing(roots, state, sd, diagnostic, boot_profile)
    intent = installation_intent(state, for_installation)
    output = HOST.safe_path(output_directory)
    if (not Path(output_directory).is_absolute() or output.parent != REPO / 'out' or
            not output.name.startswith('nvme-image-') or output.exists()):
        raise ValueError('output must be a new absolute repo/out/nvme-image-* directory')
    packages = HOST.validate_builder_packages(REPO / 'configs/builder-packages.lock.tsv')
    storage = STATE.validate_workspace_storage(output.parent if output.parent.exists() else REPO)
    output.parent.mkdir(exist_ok=True)
    if shutil.disk_usage(output.parent).free < 22 * GIB:
        raise ValueError('at least 22 GiB fixed disk free required for staging and sparse metadata')
    output.mkdir(mode=0o700)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=output) as temporary:
        stage = Path(temporary)
        snapshots = {}
        selections = {}
        for slot in ('A', 'B'):
            name = f'root-{slot}.ext4'
            snapshot = stage / name
            SELECT.capture_root(HOST.safe_path(payload_directory / name), roots['files'][name], snapshot)
            selections[slot] = root_selection(snapshot, slot, roots, diagnostic)
            snapshots[slot] = snapshot
        state_image = stage / 'state.ext4'
        SELECT.capture_root(HOST.safe_path(state_directory / 'state.ext4'), state['files']['state.ext4'], state_image)
        superblock = state_image.open('rb')
        with superblock:
            superblock.seek(1024)
            sb = superblock.read(1024)
        if (sb[56:58] != b'\x53\xef' or str(uuid.UUID(bytes=sb[104:120])) != state_uuid or
                not struct.unpack_from('<I', sb, 92)[0] & 4 or struct.unpack_from('<I', sb, 100)[0] & 0x1000):
            raise ValueError('captured state UUID/ext4 format differs')
        validate_state_identity(state_image, state)
        selection = state['boot_selection']
        if (selection != selections[selection['SLOT']] or
                debugfs(state_image, 'cat /boot/selected.conf') != SELECT.serialize(selection) or
                debugfs(state_image, 'cat /.a733-state') != f'a733-state-v1\n{state_uuid}\n'.encode()):
            raise ValueError('captured state selector/marker differs')
        boot_seed = stage / 'boot-seed'
        (boot_seed / 'boot/dtb/allwinner').mkdir(parents=True)
        for name, destination in (('uImage', 'boot/uImage'), ('board.dtb', 'boot/dtb/allwinner/sun60i-a733-orangepi-4-pro.dtb')):
            (boot_seed / destination).write_bytes(HOST.read_regular(diagnostic_directory / name, diagnostic[name]['bytes'], diagnostic[name]))
        initrd = HOST.read_regular(sd_directory / 'uInitrd', 64 * 1024**2, sd['files']['uInitrd'])
        header = bytearray(initrd[:64])
        if len(header) != 64:
            raise ValueError('short host initrd wrapper')
        header[4:8] = bytes(4)
        if (struct.unpack_from('>I', initrd)[0] != 0x27051956 or
                struct.unpack_from('>I', initrd, 4)[0] != zlib.crc32(header) or
                struct.unpack_from('>I', initrd, 12)[0] != len(initrd) - 64 or
                struct.unpack_from('>I', initrd, 24)[0] != zlib.crc32(initrd[64:]) or
                initrd[28:32] != bytes((5, 2, 3, 1)) or
                HOST.record(initrd[64:]) != {k: sd['host_initramfs'][k] for k in ('bytes', 'sha256')}):
            raise ValueError('host initrd wrapper/payload differs')
        (boot_seed / 'boot/uInitrd').write_bytes(initrd)
        command = (REPO / 'configs/host-boot.cmd').read_bytes()
        (boot_seed / 'boot/boot.cmd').write_bytes(command)
        environment = dict(os.environ, SOURCE_DATE_EPOCH=HOST.EPOCH)
        subprocess.run(['mkimage', '-C', 'none', '-A', 'arm', '-T', 'script', '-d',
                        str(boot_seed / 'boot/boot.cmd'), str(boot_seed / 'boot/boot.scr')],
                       check=True, capture_output=True, env=environment)
        guest_seed = stage / 'guest-seed'
        guest_seed.mkdir(mode=0o700)
        boot_uuid, guests_uuid = str(uuid.uuid4()), str(uuid.uuid4())
        boot, guests = stage / 'boot.ext4', stage / 'guests.ext4'
        parts = gpt['layout']['partitions']
        make_filesystem(boot, parts[0]['size_bytes'], 'A733BOOT', boot_uuid, boot_seed, legacy=True)
        make_filesystem(guests, parts[4]['size_bytes'], 'A733GUESTS', guests_uuid, guest_seed)
        image = output / 'nvme.img'
        fd = os.open(image, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.ftruncate(fd, size_bytes)
            for extent in gpt['extents']:
                write_all(fd, extent['data'], extent['offset_bytes'])
            for part, source in zip(parts, (boot, snapshots['A'], snapshots['B'], state_image, guests)):
                place_sparse(source, fd, part['start_bytes'], part['size_bytes'])
            os.fsync(fd)
        finally:
            os.close(fd)
        # Hash all logical bytes, including zeros; allocated-byte hashes alone do
        # not describe a sparse disk image. This can take several minutes.
        image_record = HOST.file_record(image)
        result = {'schema': 1, 'classification': ('per-installation NVMe candidate; hardware acceptance pending' if for_installation else 'integrated NVMe regular-file fixture; no physical installation or hardware acceptance'),
                  'deployment_intent': intent, 'boot_profile': profile,
                  'state_uuid': state_uuid, 'boot_selection': selection, 'kernel_release': HOST.KERNEL_RELEASE,
                  'logical_sector_bytes': logical_sector_bytes, 'physical_sector_bytes': physical_sector_bytes,
                  'layout': gpt['layout'], 'disk_guid': gpt['disk_guid'], 'partition_guids': gpt['partition_guids'],
                  'filesystem_uuids': {'boot': boot_uuid, 'root-a': roots['slot_uuids']['A'], 'root-b': roots['slot_uuids']['B'], 'state': state_uuid, 'guests': guests_uuid},
                  'inputs': {'roots_manifest': root_record, 'state_manifest': state_record, 'sd_manifest': sd_record},
                  'partition_payloads': {'root-a': roots['files']['root-A.ext4'], 'root-b': roots['files']['root-B.ext4'], 'state': state['files']['state.ext4']},
                  'boot_files': {str(p.relative_to(boot_seed)): HOST.file_record(p) for p in boot_seed.rglob('*') if p.is_file()},
                  'gpt_extents': [{'name': e['name'], 'offset_bytes': e['offset_bytes'], **HOST.record(e['data'])} for e in gpt['extents']],
                  'builder_packages': packages, 'workspace_storage': storage, 'recipes': {**{name: HOST.file_record(REPO / name) for name in RECIPES}, **{name: HOST.record(data) for name, data in profile_recipes.items()}},
                  'files': {'nvme.img': image_record}, 'allocated_bytes': image.stat().st_blocks * 512,
                  'build_seconds': round(time.monotonic() - started, 1),
                  'reproducibility': 'fresh disk/partition/boot/guests UUIDs and filesystem metadata; byte identity not claimed',
                  'limitations': [('Per-installation state must not be cloned.' if for_installation else 'Fixture state must not be deployed or cloned.'), 'No physical target identity or write authorization implied.', 'No SPI firmware or NVMe-boot capability established.', 'Zero SD writes and actual KVM guest acceptance require board evidence.']}
        (output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('payload_directory', 'state_directory', 'sd_directory', 'diagnostic_directory', 'new_output_directory'):
        parser.add_argument(name)
    parser.add_argument('--size-bytes', type=int, required=True)
    parser.add_argument('--for-installation', action='store_true', help='mark a candidate using real administrator/network inputs; never authorizes storage writes')
    parser.add_argument('--logical-sector-bytes', type=int, choices=(512, 4096), default=512)
    parser.add_argument('--physical-sector-bytes', type=int, default=4096)
    parser.add_argument('--boot-profile', choices=HOST.BOOT_PROFILES, default='baseline-el1')
    args = parser.parse_args()
    try:
        result = build(args.payload_directory, args.state_directory, args.sd_directory, args.diagnostic_directory,
                       args.new_output_directory, args.size_bytes, args.logical_sector_bytes, args.physical_sector_bytes, args.for_installation, boot_profile=args.boot_profile)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        parser.exit(1, f'NVMe image: {error}\n')
    print(json.dumps(result, indent=2))

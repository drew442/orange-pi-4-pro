#!/usr/bin/env python3
"""Stream the pinned public-configuration roots and STATE into a regular NVMe image.

This distribution is unpersonalized and UNBOOTED. No device access is performed.
"""
import argparse
from contextlib import ExitStack
import copy
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import sys
import time
import zlib

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('public_config_base', REPO / 'build/protected-state-nvme-image.py')
BASE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BASE)
RUNTIME, HELP, require = BASE.RUNTIME, BASE.HELP, BASE.require
CHUNK, ADMISSION = BASE.CHUNK, BASE.ADMISSION
OUTPUT_BOUND, SCRATCH_BUDGET = BASE.OUTPUT_BOUND, BASE.SCRATCH_BUDGET
RECIPES = ('build/public-config-nvme-image.py',) + BASE.RECIPES
WIRE = {'bytes': 545636009, 'sha256': '584a958c63fb01f62c2162c733aeb004082088952c6e428d6406087492e315e2'}
MANIFEST = {'bytes': 50191, 'sha256': 'f19b5b58c12030422b36c203471026209731fa413ea1ce8d9196121861cf8eb5'}
RAW = {'bytes': 256060514304, 'sha256': '675d6ba87e53daaf3517f331948a3be5924d79ec21a5143c7bc9f5dda4f0b386'}
ROOTS_MANIFEST = {'bytes': 32613, 'sha256': '1f82b9e85c5fbcbac65af695b699ee0048fb397faeba2e3eeb652eeb584adb6c'}
STATE_MANIFEST = {'bytes': 15468, 'sha256': '0c6a76a38743924cf5919ac0ba9f5cf2a4eae818b0b312a01c2d4c590283ae03'}
NEW = {
    'root-a': {'bytes': 4294967296, 'sha256': '299b1f2f74e11c71c89b9ee249dfd43bdc741ac3e90addc763a9626bbbb5aed6'},
    'root-b': {'bytes': 4294967296, 'sha256': '48b9876f72f28d4a10f7458dd81a96b95da00d13ae00783b39f7c30067fa94f7'},
    'state': {'bytes': 8589934592, 'sha256': '2e9d3dcf4dcabfaead65e125e7778c0aed092eeb665e67f3559719d08e3564ec'}}
ROOT_PROFILES = ('public-dirs-20261109', 'deterministic-20261109')
DETERMINISTIC_ROOTS_MANIFEST = {'bytes': 33481, 'sha256': 'c1bcc7d23d22bd487534ba17ea447055881a044350829608360aa92ca24283f2'}
DETERMINISTIC_ROOTS = {
    'root-a': {'bytes': 4294967296, 'sha256': 'a9715b819348a8220ec8b3b1f41ef27db677baaa96014f5d1dfb7a22597decdd'},
    'root-b': {'bytes': 4294967296, 'sha256': '42fb2becf66c25a4cbb7844a6ab84ba12ab3f53c0d5d283acc8d42e63b124ab2'}}
OPERATOR_INPUTS = {
    'operator_config': {'bytes': 136, 'sha256': '382ccdfb6273614a23f99034dd269740166c2786d65a001ec51deecbe4683491'},
    'operator_public_key': {'bytes': 663, 'sha256': 'ded86d3be3f910e5b33d7938d21245a149e2b19dc517f0ac4f582b5b0c52f68b'},
    'boot_selection_input': {'bytes': 179, 'sha256': '3c76b96c328b4508ff407c8594352e04389664e97ced6f0a09284cade5bd027b'}}
SELECTION = {'SCHEMA': '1', 'SLOT': 'A', 'ROOT_UUID': 'a7330000-0000-4000-8000-00000000000a',
             'ROOT_MARKER_SHA256': '286494073ce3a0b69443f96389dbbce11ab015705f7e9f05c7a7cf06503da866',
             'KERNEL_RELEASE': '6.6.98-a733-diag'}


def root_inputs(profile):
    require(type(profile) is str and profile in ROOT_PROFILES, 'unregistered root profile')
    manifest, components = copy.deepcopy(ROOTS_MANIFEST), copy.deepcopy(NEW)
    if profile == 'deterministic-20261109':
        manifest = copy.deepcopy(DETERMINISTIC_ROOTS_MANIFEST)
        components.update(copy.deepcopy(DETERMINISTIC_ROOTS))
    return manifest, components


def output_path(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'absolute output path required')
    require(path.parent == REPO / 'out' and re.fullmatch(
        r'public-config-nvme-image-[A-Za-z0-9][A-Za-z0-9_-]{0,63}', path.name),
        'output must be fresh out/public-config-nvme-image-TAG')
    RUNTIME.safe_regular_path(path)
    require(not path.exists() and not path.is_symlink(), 'output already exists; failed tags cannot be reused')
    return path


def replacement_extents(base):
    parts = base['layout']['partitions']
    require(len(parts) == 5 and [p['name'] for p in parts] ==
            ['boot', 'root-a', 'root-b', 'state', 'guests'], 'partition roles mismatch')
    result = {}
    for index, name in enumerate(NEW, 1):
        part = parts[index]
        require(part['number'] == index + 1 and part['size_bytes'] == NEW[name]['bytes'], 'replacement size/partition mismatch')
        require(part['start_bytes'] == parts[index - 1]['start_bytes'] + parts[index - 1]['size_bytes'],
                'replacement extents must be contiguous')
        result[name] = (part['start_bytes'], part['size_bytes'])
    require(parts[4]['start_bytes'] == sum(result['state']), 'GUESTS boundary mismatch')
    return result


def extents(base, replacements, size):
    start = replacements['root-a'][0]
    end = sum(replacements['state'])
    require(0 < start < end < size, 'replacement bounds mismatch')
    result = {'prefix': (0, start), 'suffix': (end, size - end)}
    for part in base['layout']['partitions']:
        result['partition:' + part['name']] = (part['start_bytes'], part['size_bytes'])
    for item in base['gpt_extents']:
        require(0 < item['bytes'] <= 16384 and 0 <= item['offset_bytes'] <= size - item['bytes'], 'GPT capture bounds')
        result['gpt:' + item['name']] = (item['offset_bytes'], item['bytes'])
    return result


def unchanged(before, after):
    excluded = {'partition:' + name for name in NEW}
    require({k: v for k, v in before.items() if k not in excluded} ==
            {k: v for k, v in after.items() if k not in excluded}, 'bytes outside replacement interval changed')


class EncodedWriter(BASE.BoundedWriter):
    """Bind the generated single-member wire, including its header and trailer."""
    def __init__(self, stream, maximum):
        super().__init__(stream, maximum)
        self.digest = hashlib.sha256()

    def write(self, data):
        count = super().write(data)
        self.digest.update(data)
        return count

    def record(self):
        return {'bytes': self.total, 'sha256': self.digest.hexdigest()}


def derive(source_fd, replacements, target, expected, old_pins, new_pins, ranges, monitor,
           output_bound=OUTPUT_BOUND):
    """Replace bounded extents without full-raw staging or per-byte comparisons."""
    original, candidate = hashlib.sha256(), hashlib.sha256()
    before, after = BASE.Ranges(ranges), BASE.Ranges(ranges)
    total = 0
    with target.open('xb') as wire:
        bounded = EncodedWriter(wire, output_bound)
        with gzip.GzipFile(filename='', fileobj=bounded, mode='wb', compresslevel=9, mtime=0) as encoded:
            for data in BASE.decoded_blocks(source_fd, expected['bytes'], monitor):
                original.update(data)
                before.update(data, total)
                patched = data
                for name, (fd, start, length) in replacements.items():
                    left, right = max(total, start), min(total + len(data), start + length)
                    if left < right:
                        replacement = os.pread(fd, right - left, left - start)
                        require(len(replacement) == right - left, 'short replacement ' + name)
                        patched = patched[:left - total] + replacement + patched[right - total:]
                candidate.update(patched)
                after.update(patched, total)
                encoded.write(patched)
                total += len(data)
        wire.flush()
        os.fsync(wire.fileno())
    require({'bytes': total, 'sha256': original.hexdigest()} == expected, 'original raw hash/size mismatch')
    before, after = before.records(), after.records()
    for name in replacements:
        key = 'partition:' + name
        require(before[key] == old_pins[name] and after[key] == new_pins[name], 'replacement extent pin mismatch: ' + name)
        require(before[key]['sha256'] != after[key]['sha256'], 'replacement changed no bytes: ' + name)
    unchanged(before, after)
    return {'bytes': total, 'sha256': candidate.hexdigest()}, before, after, bounded.record()


def validate_metadata(base, roots, state, components=None):
    components = NEW if components is None else components
    require(base['files']['nvme.img.gz'] == WIRE and base['files']['nvme.img'] == RAW, 'base authority mismatch')
    require(base['protected_boot'] == BASE.NEW_BOOT, 'protected BOOT mismatch')
    require(base['state_uuid'] == state['state_uuid'] == RUNTIME.STATE_UUID, 'STATE UUID mismatch')
    require(base['kernel_release'] == roots['kernel_release'] == SELECTION['KERNEL_RELEASE'], 'kernel mismatch')
    require(state['boot_selection'] == SELECTION, 'matched STATE boot selection mismatch')
    require(roots['slot_uuids'] == {slot: base['filesystem_uuids']['root-' + slot.lower()] for slot in ('A', 'B')}, 'root UUID mismatch')
    require(roots['sources']['rootfs.tar.gz'] == state['sources']['rootfs.tar.gz'], 'source userspace mismatch')
    require(roots['sources']['userspace_profile'] == state['sources']['userspace_profile'], 'userspace profile mismatch')
    require(roots['files']['root-A.ext4'] == components['root-a'] and roots['files']['root-B.ext4'] == components['root-b'] and
            state['files']['state.ext4'] == components['state'], 'new component manifest mismatch')
    for name, pin in OPERATOR_INPUTS.items():
        require(state['sources'][name] == pin, 'original operator/selection input mismatch: ' + name)
    require(state['management'] == {'schema': 1, 'dhcp': True, 'hostname': 'a733-host',
                                  'management_mac': '10:f4:ae:90:5e:57', 'management_cidr': '0.0.0.0/0'},
            'original fixed management profile mismatch')
    intent = base['deployment_intent']
    require(state['admin_public_key_fingerprints'] == intent['administration_public_key_fingerprints'], 'operator keys mismatch')
    require(state['management']['management_mac'] == intent['management_mac'] and
            state['management']['management_cidr'] == intent['management_cidr'], 'operator management mismatch')


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(output, partial, result):
    # A final gzip without the last-published manifest is explicitly incomplete.
    partial.rename(output / 'nvme.img.gz')
    fsync_directory(output)
    manifest = output / 'manifest.json.partial'
    with manifest.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    complete = output / 'manifest.json'
    manifest.rename(complete)
    try:
        fsync_directory(output)
        fsync_directory(output.parent)
    except OSError:
        # A failed durability step must not leave a successful completion marker.
        complete.unlink()
        try:
            fsync_directory(output)
        except OSError:
            pass
        raise


def build(base_gzip, base_manifest, roots_manifest, root_a, root_b, state_manifest, state_image, output,
          root_profile=ROOT_PROFILES[0]):
    roots_pin, component_pins = root_inputs(root_profile)
    started = time.monotonic()
    output = output_path(output)
    require(output.parent.is_dir(), 'repository out directory required')
    admitted = BASE.admission(output.parent)
    monitor = HELP.Monitor(output.parent)
    monitor.check('admission', force=True)
    with ExitStack() as stack:
        held = []
        def hold(path, maximum, pin=None):
            item = stack.enter_context(RUNTIME.HeldInput(path, maximum, pin))
            require(all(item.initial_identity[:2] != prior.initial_identity[:2] for prior in held), 'aliased inputs')
            held.append(item)
            return item
        source = hold(base_gzip, WIRE['bytes'], WIRE)
        authority = hold(base_manifest, MANIFEST['bytes'], MANIFEST)
        roots_authority = hold(roots_manifest, roots_pin['bytes'], roots_pin)
        state_authority = hold(state_manifest, STATE_MANIFEST['bytes'], STATE_MANIFEST)
        components = {name: hold(path, component_pins[name]['bytes'], component_pins[name]) for name, path in
                      (('root-a', root_a), ('root-b', root_b), ('state', state_image))}
        recipes = {name: hold(REPO / name, CHUNK).record for name in RECIPES}
        # Authenticate all fixed inputs and recipe closure before interpreting JSON.
        base, roots, state = [json.loads(item.read()) for item in (authority, roots_authority, state_authority)]
        validate_metadata(base, roots, state, component_pins)
        replacement_ranges = replacement_extents(base)
        ranges = extents(base, replacement_ranges, RAW['bytes'])
        output.mkdir(mode=0o700)
        fsync_directory(output.parent)
        partial = output / 'nvme.img.gz.partial'
        replacements = {name: (components[name].fd, *extent) for name, extent in replacement_ranges.items()}
        raw, before, after, generated_wire = derive(source.fd, replacements, partial, RAW, base['partition_payloads'], component_pins, ranges, monitor)
        proof = BASE.verify_output(partial, raw, ranges, monitor)
        require(proof.records() == after, 'independent output ranges differ')
        BASE.validate_gpt(proof.gpt, base, RAW['bytes'])
        for item in base['gpt_extents']:
            require(before['gpt:' + item['name']] == {'bytes': item['bytes'], 'sha256': item['sha256']}, 'GPT authority mismatch')
        for name, pin in base['partition_payloads'].items():
            require(before['partition:' + name] == pin, 'original partition authority mismatch')
        require(before['partition:boot'] == after['partition:boot'] == BASE.NEW_BOOT, 'protected BOOT changed')
        require(before['partition:guests'] == base['derivation']['after_ranges']['partition:guests'], 'GUESTS authority mismatch')
        unchanged(before, after)
        wire = BASE.BOOT.VGIC.file_record(partial)
        require(wire == generated_wire, 'output differs from generated single-member wire')
        require(wire['bytes'] <= OUTPUT_BOUND, 'compressed output exceeds scratch bound')
        result = {key: copy.deepcopy(base[key]) for key in BASE.PAYLOAD_METADATA if key in base}
        result['boot_selection'] = copy.deepcopy(state['boot_selection'])
        result['partition_payloads'].update(copy.deepcopy(component_pins))
        result.update(classification='public configuration full NVMe distribution; UNBOOTED',
                      hardware_acceptance=False, physical_storage_written=False,
                      deployment='unpersonalized distribution only; never overwrite the personalized running board',
                      files={'nvme.img': raw, 'nvme.img.gz': wire}, sources=recipes,
                      base_gzip=WIRE, base_manifest=MANIFEST, roots_manifest=roots_pin, state_manifest=STATE_MANIFEST,
                      root_profile=root_profile,
                      source_manifest=base, root_component_metadata=roots, state_component_metadata=state,
                      derivation={'replaced_extents': {name: {'offset_bytes': start, 'bytes': length} for name, (start, length) in replacement_ranges.items()},
                                  'replaced_extent_bytes': sum(length for _, length in replacement_ranges.values()),
                                  'before_ranges': before, 'after_ranges': after,
                                  'base_crc_eof_raw_hash_verified': True, 'both_gpt_tables_verified': True,
                                  'output_gzip_full_expansion_verified': True, 'generated_single_member_wire_verified': True,
                                  'all_inputs_held_reauthenticated': True,
                                  'gzip': {'level': 9, 'mtime': 0, 'filename': ''}},
                      tool_versions={'python': sys.version, 'zlib': zlib.ZLIB_RUNTIME_VERSION})
        for item in held:
            monitor.check('reauthenticate', force=True)
            item.verify()
        monitor.check('publication', force=True)
        result['resources'] = {'admission': admitted, 'admission_guard': ADMISSION, 'output_bound': OUTPUT_BOUND,
                               'scratch_budget': SCRATCH_BUDGET, 'disk_floor': HELP.DISK_FLOOR,
                               'memory_floor': HELP.MEMORY_FLOOR, 'minimum_free_bytes': monitor.minimum_free,
                               'minimum_available_memory_bytes': monitor.minimum_memory}
        result['build_seconds_before_publication'] = time.monotonic() - started
        publish(output, partial, result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base-gzip', 'base-manifest', 'roots-manifest', 'root-a', 'root-b', 'state-manifest', 'state', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--root-profile', choices=ROOT_PROFILES, default=ROOT_PROFILES[0])
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.base_gzip, args.base_manifest, args.roots_manifest, args.root_a,
                               args.root_b, args.state_manifest, args.state, args.output, args.root_profile), indent=2))
    except (ValueError, OSError, KeyError, TypeError, UnicodeError, EOFError, zlib.error, struct.error) as error:
        parser.exit(1, f'public configuration full NVMe packaging failed: {error}\n')


if __name__ == '__main__':
    main()

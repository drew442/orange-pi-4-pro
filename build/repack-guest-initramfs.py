#!/usr/bin/env python3
"""Replace only /init in the authenticated first-guest archive; never execute it."""
import argparse
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('assemble_host', REPO / 'build/assemble-host.py')
HOST = importlib.util.module_from_spec(spec)
spec.loader.exec_module(HOST)
ORIGINAL = {'bytes': 974226, 'sha256': '2f3360a8f6365b84c352cae99949a5ae92a81001efe28dbae221f334805f854b'}
MAX_RAW = 4 * 1024**2


def entries(raw):
    """Validate bounded newc framing, names, trailer and the sole init entry."""
    if not raw or len(raw) > MAX_RAW:
        raise ValueError('archive size outside budget')
    result, seen, offset = [], set(), 0
    while True:
        start = offset
        header = raw[offset:offset + 110]
        if len(header) != 110 or header[:6] != b'070701' or not re.fullmatch(b'[0-9a-fA-F]{104}', header[6:]):
            raise ValueError('invalid or truncated newc header')
        fields = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
        mode, size, namesize = fields[1], fields[6], fields[11]
        if not 1 <= namesize <= 4096:
            raise ValueError('invalid name length')
        name_end = offset + 110 + namesize
        name_bytes = raw[offset + 110:name_end]
        if len(name_bytes) != namesize or name_bytes[-1:] != b'\0' or b'\0' in name_bytes[:-1]:
            raise ValueError('invalid or truncated name')
        try:
            name = name_bytes[:-1].decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('invalid name encoding') from exc
        normalized = name[2:] if name.startswith('./') else name
        if normalized != '.' and (not normalized or normalized.startswith('/') or any(p in ('', '.', '..') for p in normalized.split('/'))):
            raise ValueError('unsafe entry name')
        if normalized in seen:
            raise ValueError('duplicate entry')
        seen.add(normalized)
        data_start = (name_end + 3) & ~3
        data_end = data_start + size
        offset = (data_end + 3) & ~3
        if offset > len(raw):
            raise ValueError('truncated entry data')
        item = {'name': normalized, 'mode': mode, 'start': start, 'data_start': data_start,
                'data_end': data_end, 'end': offset, 'data': raw[data_start:data_end]}
        result.append(item)
        if normalized == 'TRAILER!!!':
            if size or any(raw[offset:]):
                raise ValueError('invalid trailer or trailing data')
            break
    init = [item for item in result if item['name'] == 'init']
    if len(init) != 1 or init[0]['mode'] != stat.S_IFREG | 0o755:
        raise ValueError('require exactly one regular 0755 init')
    return result


def replace_init(raw, replacement):
    original_entries = entries(raw)
    item = next(item for item in original_entries if item['name'] == 'init')
    header = bytearray(raw[item['start']:item['data_start']])
    header[54:62] = f'{len(replacement):08x}'.encode('ascii')
    updated = (raw[:item['start']] + header + replacement + b'\0' * (-len(replacement) % 4) + raw[item['end']:])
    new_entries = entries(updated)
    preserved = []
    for old, new in zip(original_entries, new_entries, strict=True):
        if old['name'] != new['name']:
            raise ValueError('entry order changed')
        if old['name'] != 'init':
            if raw[old['start']:old['end']] != updated[new['start']:new['end']]:
                raise ValueError('non-init entry changed')
            preserved.append({'name': old['name'], 'mode': old['mode'], **HOST.record(old['data'])})
    return updated, preserved


def build(source, output):
    source_data = HOST.read_regular(source, ORIGINAL['bytes'], ORIGINAL)
    with gzip.GzipFile(fileobj=io.BytesIO(source_data)) as stream:
        raw = stream.read(MAX_RAW + 1)
    if len(raw) > MAX_RAW:
        raise ValueError('decompressed archive exceeds budget')
    init = HOST.read_regular(REPO / 'configs/guest-init', 65536)
    updated, preserved = replace_init(raw, init)
    packed = io.BytesIO()
    with gzip.GzipFile(fileobj=packed, mode='wb', filename='', mtime=0, compresslevel=9) as stream:
        stream.write(updated)
    output = HOST.safe_path(output)
    if output.parent != REPO / 'out' or not re.fullmatch(r'guest-initramfs-[A-Za-z0-9][A-Za-z0-9._-]*', output.name):
        raise ValueError('output must be a new direct repo/out/guest-initramfs-NAME directory')
    if output.exists() or output.is_symlink():
        raise ValueError('output already exists')
    manifest = {'schema': 1, 'hardware_acceptance': 'pending', 'original_initramfs': ORIGINAL,
                'initramfs': HOST.record(packed.getvalue()), 'init': HOST.record(init),
                'recipe': HOST.record(HOST.read_regular(Path(__file__), 65536)),
                'all_other_entries_preserved': True, 'preserved_entries': preserved}
    output.mkdir(mode=0o755)
    for name, data in [('initramfs.cpio.gz', packed.getvalue()),
                       ('manifest.json', (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode())]:
        descriptor = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original_initramfs', type=Path)
    parser.add_argument('new_output', type=Path)
    args = parser.parse_args()
    try:
        result = build(args.original_initramfs, args.new_output)
    except (ValueError, OSError, EOFError) as exc:
        parser.exit(1, f'{exc}\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()

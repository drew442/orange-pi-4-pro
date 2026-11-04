#!/usr/bin/env python3
"""Opt-in staging transaction test on a newly created private file-backed loop.

Only an authenticated candidate directory can be supplied, never a target.
The live NVMe policy and TTY authorization are explicitly replaced by fixtures;
actual block opens, locks, ioctls, copying and readback remain production code.
"""
import argparse
import contextlib
import copy
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('update_loop_transaction', REPO / 'scripts/update-host.py')
UPDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPDATE)
SIZE = 4 * 1024**3


def require(condition, message):
    if not condition:
        raise ValueError(message)


def record(path):
    data = Path(path).read_bytes()
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def owned_loop(loop, backing):
    require(re.fullmatch(r'/dev/loop[0-9]+', loop) is not None, 'unexpected loop name')
    info = backing.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
            and info.st_size == SIZE, 'private backing metadata differs')
    node = Path('/sys/class/block') / Path(loop).name
    value = (node / 'loop/backing_file').read_text().strip()
    require(Path('/' + value.lstrip('/')).resolve(strict=True) == backing,
            'loop backing identity differs')
    require(not list((node / 'holders').iterdir()), 'private loop has holders')
    return node


def verify(candidate, manifest_pin):
    require(os.geteuid() == 0, 'root required for private loop test')
    require(shutil.which('udevadm') is not None, 'udevadm required to settle loop probes')
    with UPDATE.PREP.safe_parent(Path('/var/lib')) as parent:
        UPDATE.storage_guard(parent)
    require(shutil.disk_usage('/var/lib').free >= SIZE + 2 * 1024**3,
            '6 GiB available on fixed builder storage required')
    source = UPDATE.Source(candidate, manifest_pin, 'B')
    results = {}
    loop = None
    @contextlib.contextmanager
    def workspace():
        directory = Path(tempfile.mkdtemp(prefix='a733-update-loop-', dir='/var/lib'))
        try:
            yield directory
        finally:
            if loop is None:
                shutil.rmtree(directory)
            else:
                print(f'Retained private workspace after loop cleanup failure: '
                      f'loop={loop} backing={directory / "inactive-root.img"}', file=sys.stderr)
    try:
        with workspace() as root:
            boot = root / 'state-boot'
            boot.mkdir(mode=0o700)
            previous = UPDATE.PREP.SELECT.serialize({
                'SCHEMA':'1', 'SLOT':'A', 'ROOT_UUID':UPDATE.PREP.INIT.PAYLOAD.SLOT_UUIDS['A'],
                'ROOT_MARKER_SHA256':'a' * 64,
                'KERNEL_RELEASE':UPDATE.PREP.INIT.PAYLOAD.KERNEL_RELEASE})
            selector = boot / 'selected.conf'
            selector.write_bytes(previous)
            selector.chmod(0o600)
            sentinels = [root / 'guest-data-sentinel', root / 'boot-stack-sentinel']
            for path in sentinels:
                path.write_bytes(b'fixture only: must remain unchanged\n')
                path.chmod(0o400)
            before = {path.name: record(path) for path in [selector, *sentinels]}
            backing = root / 'inactive-root.img'
            with backing.open('xb') as target:
                target.truncate(SIZE)
                target.seek(SIZE - 4096)
                target.write(b'\xa5' * 4096)
            backing.chmod(0o600)
            try:
                loop = subprocess.check_output(['losetup','--find','--show',str(backing)],
                                               text=True).strip()
                node = owned_loop(loop, backing)
                subprocess.run(['udevadm','settle','--timeout=10'], check=True,
                               capture_output=True, timeout=15)
                dev = os.stat(loop).st_rdev
                state_device = boot.stat().st_dev
                disk = {'requested_path':loop, 'canonical_path':loop,
                        'major_minor':f'{os.major(dev)}:{os.minor(dev)}',
                        'model':'PRIVATE LOOP FIXTURE', 'serial':'NO-PHYSICAL-TARGET',
                        'size_bytes':SIZE,
                        'logical_sector_bytes':int((node / 'queue/logical_block_size').read_text()),
                        'physical_sector_bytes':int((node / 'queue/physical_block_size').read_text())}
                context = {'disk':disk, 'target':dict(disk, partition_number=3,
                               partition_uuid='a7330000-0000-4000-8000-000000000003'),
                           'active':{'slot':'A','root_source':'fixture-only-active-root',
                                     'root_major_minor':'fixture-only','marker_sha256':'a'*64},
                           'state':{'source':'fixture-directory-only',
                                    'major_minor':f'{os.major(state_device)}:{os.minor(state_device)}',
                                    'uuid':'a7330000-0000-4000-8000-000000000004',
                                    'boot_directory':str(boot),
                                    'selector':UPDATE.PREP.SELECT.parse(previous),
                                    'selector_sha256':hashlib.sha256(previous).hexdigest()},
                           'gpt':{'disk_guid':'fixture-only-no-GPT', 'partition_guids':[],
                                  'sha256':'b'*64}}
                args = SimpleNamespace(target=loop, expected_model=disk['model'],
                                       expected_serial=disk['serial'],expected_bytes=SIZE,slot='B')
                real_pwrite = os.pwrite
                real_ioctl = UPDATE.fcntl.ioctl
                writes, ioctls, exclusive_refusals = [], [], []
                interrupt = True
                def write(held, data, offset):
                    info = os.fstat(held)
                    require(stat.S_ISBLK(info.st_mode) and info.st_rdev == dev,
                            'write escaped owned private loop')
                    owned_loop(loop, backing)
                    if not writes:
                        try:
                            other = os.open(loop, os.O_RDWR | os.O_EXCL | os.O_NOFOLLOW)
                        except OSError as error:
                            require(error.errno == errno.EBUSY, 'unexpected exclusive-open error')
                            exclusive_refusals.append(True)
                        else:
                            os.close(other)
                            raise ValueError('second exclusive block claim succeeded')
                    if interrupt and len(writes) >= 2:
                        raise OSError(errno.EIO, 'intentional private-loop write interruption')
                    count = real_pwrite(held, data, offset)
                    writes.append({'offset':offset,'bytes':count})
                    return count
                def ioctl(held, command, *arguments):
                    if command == UPDATE.WRITER.BLKRRPART:
                        raise AssertionError('staging must never refresh the partition table')
                    if command == UPDATE.WRITER.BLKFLSBUF:
                        require(os.fstat(held).st_rdev == dev, 'flush escaped owned loop')
                        ioctls.append('BLKFLSBUF')
                    return real_ioctl(held, command, *arguments)
                def snapshot(unused):
                    owned_loop(loop, backing)
                    require(selector.read_bytes() == previous, 'fixture selector changed')
                    return copy.deepcopy(context)
                # Direct replacements avoid Mock's call history retaining all
                # 4 GiB of write buffers for this otherwise bounded copy.
                with mock.patch.object(UPDATE, 'snapshot', new=snapshot), \
                     mock.patch.object(UPDATE, 'authorize', new=lambda unused: None), \
                     mock.patch.object(UPDATE.WRITER.os, 'pwrite', new=write), \
                     mock.patch.object(UPDATE.fcntl, 'ioctl', new=ioctl):
                    plan = UPDATE.make_plan(args, source)
                    partial = UPDATE.apply(args, source, plan)
                    require(not partial['success'] and partial['target_modified']
                            and partial['bytes_written'] == 8 * 1024**2
                            and partial['bytes_verified'] == 0 and not partial['activation_ready'],
                            'partial report is not conservative')
                    require(not partial['selector_writes_performed'] and not ioctls,
                            'partial stage performed unexpected publication or flush')
                    with backing.open('rb') as raw:
                        raw.seek(SIZE - 4096)
                        require(raw.read(4096) == b'\xa5' * 4096, 'unwritten old tail changed')
                    require({path.name:record(path) for path in [selector,*sentinels]} == before,
                            'partial stage changed a sentinel')
                    # Quiesce builder device events before starting a separate
                    # transaction; retain the production nonblocking lock.
                    subprocess.run(['udevadm','settle','--timeout=10'], check=True,
                                   capture_output=True, timeout=15)
                    interrupt = False
                    writes.clear()
                    retry_plan = UPDATE.make_plan(args, source)
                    complete = UPDATE.apply(args, source, retry_plan)
                    require(complete['success'] and complete['target_modified']
                            and complete['bytes_written'] == complete['bytes_verified'] == SIZE
                            and complete['activation_ready'],
                            'complete retry failed: ' + json.dumps(complete, sort_keys=True))
                    require(complete['readback_sha256'] == source.expected['sha256']
                            and not complete['selector_writes_performed']
                            and ioctls == ['BLKFLSBUF'], 'stage readback/flush contract differs')
                source.check()
                require({path.name:record(path) for path in [selector,*sentinels]} == before,
                        'complete stage changed selection or unrelated sentinel')
                require(sum(item['bytes'] for item in writes) == SIZE
                        and writes[0]['offset'] == 0 and writes[-1]['offset'] + writes[-1]['bytes'] == SIZE,
                        'retry did not write the full slot')
                require(len(exclusive_refusals) == 2, 'exclusive claims were not tested on both attempts')
                results = {'partial_report':partial, 'explicit_full_retry_report':complete,
                           'complete_bytes_written_including_zero_ranges':SIZE,
                           'selector_and_unrelated_sentinels_unchanged':True,
                           'production_candidate_authentication':True,
                           'production_block_open_locks_ioctls_write_readback':True,
                           'second_exclusive_claim_refused_each_attempt':len(exclusive_refusals) == 2,
                           'partition_table_refresh_calls':0,
                           'source_files_unchanged':True}
            finally:
                if loop is not None:
                    owned_loop(loop, backing)
                    subprocess.run(['losetup','--detach',loop], check=True)
                    attribute = Path('/sys/class/block') / Path(loop).name / 'loop/backing_file'
                    for unused in range(50):
                        if not attribute.exists():
                            break
                        time.sleep(0.1)
                    else:
                        raise ValueError('owned loop detach did not complete')
                    loop = None
        results['cleanup'] = 'owned loop detached and private target/state/sentinel files deleted'
        results['classification'] = 'native private-loop staging transaction only; no live NVMe policy, TTY authorization, activation, rollback or hardware proof'
        results['source_root'] = source.expected
        results['sources'] = {name:record(REPO / name) for name in
                             ('scripts/update-host.py','scripts/host-update-target.py',
                              'tests/verify_host_update_loop.py')}
        return results
    finally:
        source.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate_directory', type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.candidate_directory,arguments.manifest_sha256),indent=2))

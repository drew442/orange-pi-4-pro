#!/usr/bin/env python3
"""Replay the baseline with an explicit locked-account date transformation.

The unchanged historical pipeline supplies resource, namespace, configuration,
and cleanup checks. This driver stages a separate authenticated export adapter;
it does not claim execution of the historical launcher or hardware acceptance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import types

SELF = 'build/launch-account-dated-userspace.py'
PIPELINE = 'build/launch-userspace-pipeline.py'
PIPELINE_PIN = {'bytes': 20436, 'sha256': '3338fe97521287124cae5c61b2631deee977ad575e5d713b6ab4d1a6385c22e7'}
ADAPTER = 'build/export-account-dated-userspace.py'
HELPER = 'build/account-dates.py'
CONTRACT = 'configs/host-account-dates-replay.lock.json'
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C', 'TZ': 'UTC'}


def require(value, message):
    if not value:
        raise ValueError(message)


def pin(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def source_bytes(path):
    require(path.is_absolute() and str(path) == os.path.abspath(path), 'physical absolute source required')
    for parent in path.parents:
        require(stat.S_ISDIR(parent.lstat().st_mode), 'physical source ancestors required')
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


def private_module(name, data):
    result = types.ModuleType(name.replace('/', '_'))
    result.__file__ = name
    exec(compile(data, name, 'exec'), result.__dict__)
    return result


def authenticated_pipeline(project):
    data = source_bytes(project / PIPELINE)
    require(pin(data) == PIPELINE_PIN, 'historical pipeline component pin differs')
    revision = subprocess.check_output(['/usr/bin/git', '-C', str(project), 'rev-parse', '--verify', 'HEAD'], env=ENV).decode().strip()
    require(re.fullmatch('[0-9a-f]{40}', revision), 'invalid Git revision')
    committed = subprocess.check_output(['/usr/bin/git', '-C', str(project), 'show', revision + ':' + PIPELINE], env=ENV)
    require(committed == data, 'historical pipeline differs from Git blob')
    return private_module(PIPELINE, data)


def configure_pipeline(pipeline):
    """Create an explicit replay variant in memory; historical files stay intact."""
    require(pipeline.SELF == PIPELINE, 'unexpected historical pipeline identity')
    original_stage = pipeline.stage_inputs
    original_verify = pipeline.verify_export
    pipeline.SELF = SELF
    pipeline.PHASE_FILES = dict(pipeline.PHASE_FILES)
    pipeline.PHASE_FILES['export'] = tuple(dict.fromkeys(
        pipeline.PHASE_FILES['export'] + (ADAPTER, HELPER, CONTRACT, pipeline.PROFILE_LOCK, PIPELINE, SELF)))

    def stage(shared, phase, name, manifest, contents, index):
        if name != 'export':
            return original_stage(shared, phase, name, manifest, contents, index)
        configured = shared.read_json(phase / 'work/configure-receipt.json', owner=shared.UID_BASE)
        require(configured.get('success') is True, 'configuration receipt did not pass')
        slot = phase / name
        slot.mkdir(mode=0o700)
        (slot / 'scratch').mkdir(mode=0o700)
        os.chown(slot / 'scratch', shared.UID_BASE, shared.UID_BASE)
        inputs = slot / 'inputs'
        inputs.mkdir(mode=0o755)
        selected = pipeline.PHASE_FILES[name]
        shared.project_snapshot(inputs / 'project', {key: contents[key] for key in selected})
        shared.atomic_json(inputs / 'manifest.json', {'revision': manifest['revision'],
                           'files': {key: manifest['files'][key] for key in selected}})
        expected = shared.sha(phase / 'work/configure-receipt.json')
        runner = ('import importlib.util,json,pathlib\n'
                  'p="/a733-sources/project/' + ADAPTER + '"\n'
                  's=importlib.util.spec_from_file_location("replay_export",p)\n'
                  'm=importlib.util.module_from_spec(s)\ns.loader.exec_module(m)\n'
                  'print(json.dumps(m.export(pathlib.Path("/a733-export-output/export-result"),'
                  + repr(expected) + ',profile="baseline"),sort_keys=True))\n')
        pipeline.immutable_file(inputs / 'run.py', runner.encode())
        os.chmod(inputs, 0o555)
        return slot, None

    def verify(shared, phase, contents):
        actual = original_verify(shared, phase, contents)
        output = phase / 'work/export-result'
        receipt = json.loads(shared.regular_bytes(output / 'receipt.json', owner=shared.UID_BASE,
                                                  limit=1024**2))
        contract = validate_sources(contents)
        expected = contract['profiles']['baseline']
        for name in ('archive', 'raw_tar', 'members', 'inventory', 'bom'):
            require(receipt.get(name) == expected[name], 'replay export ' + name + ' differs')
        bom = shared.regular_bytes(output / 'rootfs-bom.json', owner=shared.UID_BASE)
        require(pin(bom) == expected['bom'], 'replay BOM bytes differ')
        replay = receipt.get('account_date_replay', {})
        require(replay.get('schema') == 1 and replay.get('profile') == 'baseline'
                and replay.get('adapter') == pin(contents[ADAPTER])
                and replay.get('helper') == pin(contents[HELPER])
                and replay.get('contract') == pin(contents[CONTRACT])
                and replay.get('input_receipt_sha256') == shared.sha(phase / 'work/configure-receipt.json')
                and replay.get('source_manifest') == pin(shared.regular_bytes(phase / 'export/inputs/manifest.json', owner=0)),
                'replay transformation provenance differs')
        return actual

    pipeline.stage_inputs = stage
    pipeline.verify_export = verify
    return pipeline


def validate_sources(contents):
    adapter = private_module(ADAPTER, contents[ADAPTER])
    require(adapter.CONTRACT == CONTRACT and pin(contents[CONTRACT]) == adapter.CONTRACT_PIN,
            'replay contract pin differs')
    contract = adapter.validate_contract(contents[CONTRACT])
    for name in ('build/export-host-userspace.py', HELPER, 'configs/host-userspace-profiles.lock.json'):
        require(pin(contents[name]) == contract['components'][name], 'replay component differs: ' + name)
    profiles = json.loads(contents['configs/host-userspace-profiles.lock.json'])['profiles']
    baseline = contract['profiles']['baseline']
    require(all(baseline[name] == profiles['normalized-20261106'][name]
                for name in ('archive', 'raw_tar', 'members', 'inventory', 'epoch')),
            'replay historical baseline pins differ')
    return contract


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--project', type=Path, default=Path(__file__).absolute().parent.parent)
    parser.add_argument('--plan', action='store_true')
    options = parser.parse_args(argv)
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    pipeline = configure_pipeline(authenticated_pipeline(options.project))
    phase = pipeline.tag_path(options.tag)
    manifest, contents = pipeline.committed_sources(options.project)
    require(source_bytes(Path(__file__).absolute()) == contents[SELF], 'executing replay driver differs from committed source')
    validate_sources(contents)
    report = pipeline.plan(options.tag, manifest)
    report['account_date_replay'] = {
        'schema': 1, 'profile': 'baseline', 'driver': pin(contents[SELF]),
        'pipeline_component': PIPELINE_PIN, 'adapter': pin(contents[ADAPTER]), 'contract': pin(contents[CONTRACT]),
        'classification': 'new replay using historical components; not historical launcher execution'}
    if options.plan:
        print(json.dumps(report, sort_keys=True))
        return
    shared = pipeline.shared_module(contents)
    builder, before, index, facts = pipeline.preflight(shared, phase, contents)
    report.update(facts)
    pipeline.execute(shared, builder, phase, manifest, contents, index, before, report)
    print(json.dumps({'success': True, 'phase': str(phase), 'export': str(phase / 'work/export-result')}))


if __name__ == '__main__':
    main()

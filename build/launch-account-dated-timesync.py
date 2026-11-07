#!/usr/bin/env python3
"""Derive a time profile from the authenticated account-date baseline replay.

Historical launchers remain unchanged. This driver composes their safety checks
with the explicit account-date export contract; it establishes no hardware gate.
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

SELF = 'build/launch-account-dated-timesync.py'
CORE = 'build/launch-timesync-userspace.py'
CORE_PIN = {'bytes': 33819, 'sha256': '12bca34664c99b6621378b43807da69e1265dff4fe0b9d4f42251aa78f1ee787'}
BASE_DRIVER = 'build/launch-account-dated-userspace.py'
BASE_DRIVER_PIN = {'bytes': 8956, 'sha256': '65b507b671ae2fb80a1e72cb47ce06d7833656b2f7e5e5c7613542a10fec0eba'}
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


def module(name, data):
    result = types.ModuleType(name.replace('/', '_'))
    result.__file__ = name
    exec(compile(data, name, 'exec'), result.__dict__)
    return result


def authenticated_core(project):
    """Authenticate both bootstrap files before executing historical code."""
    require(project.is_absolute() and str(project) == os.path.abspath(project), 'absolute project required')
    revision = subprocess.check_output(['/usr/bin/git', '-C', str(project), 'rev-parse', '--verify', 'HEAD'], env=ENV).decode().strip()
    require(re.fullmatch('[0-9a-f]{40}', revision), 'invalid Git revision')
    contents = {}
    for name in (SELF, CORE):
        data = source_bytes(project / name)
        fields = subprocess.check_output(['/usr/bin/git', '-C', str(project), 'ls-tree', revision, '--', name], env=ENV).decode().split()
        require(len(fields) == 4 and fields[0] in ('100644', '100755') and fields[1] == 'blob'
                and fields[3] == name, 'uncommitted bootstrap source: ' + name)
        require(subprocess.check_output(['/usr/bin/git', '-C', str(project), 'cat-file', 'blob', fields[2]], env=ENV) == data,
                'bootstrap source differs from Git blob: ' + name)
        contents[name] = data
    require(source_bytes(Path(__file__).absolute()) == contents[SELF], 'executing driver differs from committed source')
    require(pin(contents[CORE]) == CORE_PIN, 'historical time launcher pin differs')
    return module(CORE, contents[CORE])


def dated_pipeline(time_core, contents):
    require(pin(contents[BASE_DRIVER]) == BASE_DRIVER_PIN, 'baseline replay driver pin differs')
    baseline = module(BASE_DRIVER, contents[BASE_DRIVER])
    require(pin(contents[time_core.PIPELINE]) == baseline.PIPELINE_PIN, 'historical baseline pipeline pin differs')
    pipeline = baseline.configure_pipeline(module(time_core.PIPELINE, contents[time_core.PIPELINE]))
    return baseline, pipeline


def validate_sources(time_core, contents):
    require(pin(contents[CORE]) == CORE_PIN, 'historical time launcher pin differs')
    baseline, pipeline = dated_pipeline(time_core, contents)
    contract = baseline.validate_sources(contents)
    for name, expected in contract['components'].items():
        require(pin(contents[name]) == expected, 'replay component differs: ' + name)
    profiles = json.loads(contents[pipeline.PROFILE_LOCK])['profiles']
    require(profiles['timesync-20261106']['sources'][CORE] == CORE_PIN, 'historical time launcher profile pin differs')
    return contract


def baseline_metadata(baseline, contents):
    return {'schema': 1, 'profile': 'baseline', 'driver': pin(contents[BASE_DRIVER]),
            'pipeline_component': baseline.PIPELINE_PIN, 'adapter': pin(contents[ADAPTER]),
            'contract': pin(contents[CONTRACT]),
            'classification': 'new replay using historical components; not historical launcher execution'}


def baseline_runner(name, configured_sha):
    """Exact runner text of the pinned baseline component/dated export stage."""
    if name == 'export':
        return ('import importlib.util,json,pathlib\n'
                'p="/a733-sources/project/' + ADAPTER + '"\n'
                's=importlib.util.spec_from_file_location("replay_export",p)\n'
                'm=importlib.util.module_from_spec(s)\ns.loader.exec_module(m)\n'
                'print(json.dumps(m.export(pathlib.Path("/a733-export-output/export-result"),'
                + repr(configured_sha) + ',profile="baseline"),sort_keys=True))\n').encode()
    program = {'foreign': 'prepare-host-userspace.py', 'configure': 'configure-host-userspace.py'}[name]
    return ('import importlib.util,json,pathlib\np="/a733-sources/project/build/' + program + '"\n'
            's=importlib.util.spec_from_file_location("recipe",p)\nm=importlib.util.module_from_spec(s)\ns.loader.exec_module(m)\n'
            'm.main()\n').encode()


def base_sources(time_core, shared, base, contents, report, builder):
    """Accept only the new dated baseline, retaining all historical source gates."""
    baseline, pipeline = dated_pipeline(time_core, contents)
    require(report.get('account_date_replay') == baseline_metadata(baseline, contents),
            'base replay driver provenance differs')
    manifest = json.loads(time_core.held(base.parent / 'source-manifest.json', owner=0, limit=1024**2)[0])
    names = {pipeline.SELF, pipeline.SHARED, pipeline.HOST_LOCK, pipeline.PROFILE_LOCK}.union(
        *map(set, pipeline.PHASE_FILES.values()))
    require(manifest == report.get('inputs') and set(manifest) == {'revision', 'files'}
            and isinstance(manifest['revision'], str) and re.fullmatch('[0-9a-f]{40}', manifest['revision'])
            and set(manifest['files']) == names, 'base source manifest differs')
    for name, record in manifest['files'].items():
        require(set(record) == {'git_blob', 'bytes', 'sha256'} and type(record['bytes']) is int
                and isinstance(record['git_blob'], str) and re.fullmatch('[0-9a-f]{40}', record['git_blob'])
                and {key: record[key] for key in ('bytes', 'sha256')} == pin(contents[name]), 'base source pin differs')
        blob = hashlib.sha1(b'blob ' + str(len(contents[name])).encode() + b'\0' + contents[name]).hexdigest()
        require(record['git_blob'] == blob, 'base Git blob differs')
    require(builder is not None, 'authenticated base probe validator required')
    for phase in report['phases']:
        name = phase['name']
        slot = base.parent / name
        selected = pipeline.PHASE_FILES[name]
        snapshot = json.loads(time_core.held(slot / 'inputs/manifest.json', owner=0, limit=1024**2)[0])
        require(snapshot == {'revision': manifest['revision'], 'files': {key: manifest['files'][key] for key in selected}},
                'base phase manifest differs')
        for key in selected:
            require(time_core.held(slot / 'inputs/project' / key, owner=0, limit=16 * 1024**2)[1]
                    == pin(contents[key]), 'base phase source differs')
        configured_sha = report['phases'][1].get('recipe_receipt_sha256')
        require(time_core.held(slot / 'inputs/run.py', owner=0, limit=1024**2)[0]
                == baseline_runner(name, configured_sha), 'base phase runner differs')
        require(phase.get('command') == pipeline.command(shared, builder, base.parent, slot, name), 'base phase command differs')
        require(builder.validate_probe(time_core.held(slot / 'runtime.log', owner=0, limit=64 * 1024**2)[0])
                == phase.get('probe'), 'base phase probe differs')
        if name == 'configure':
            require(phase.get('helper_returncode') == 0, 'base API helper failed')
            shared.validate_ready(phase['api_ready'], phase['api_profile'], shared.namespaces())
    # This invokes the dated driver's original archive/BOM and provenance checks.
    pipeline.verify_export(shared, base.parent, contents)


def configure_timesync(time_core):
    require(time_core.SELF == CORE, 'unexpected historical time launcher identity')
    original_stage, original_verify = time_core.stage_inputs, time_core.verify_export
    time_core.SELF = SELF
    time_core.SOURCE_FILES = tuple(dict.fromkeys(time_core.SOURCE_FILES + (
        CORE, SELF, BASE_DRIVER, ADAPTER, HELPER, CONTRACT)))

    def stage(shared, pipeline, phase, name, manifest, contents, bom, deb, repo_sha):
        if name != 'export':
            return original_stage(shared, pipeline, phase, name, manifest, contents, bom, deb, repo_sha)
        slot = phase / name
        slot.mkdir(mode=0o700)
        (slot / 'scratch').mkdir(mode=0o700)
        os.chown(slot / 'scratch', shared.UID_BASE, shared.UID_BASE)
        inputs = slot / 'inputs'
        inputs.mkdir(mode=0o755)
        shared.project_snapshot(inputs / 'project', contents)
        shared.atomic_json(inputs / 'manifest.json', manifest)
        pipeline.immutable_file(inputs / 'rootfs-bom.json', bom)
        pipeline.immutable_file(inputs / json.loads(contents[time_core.CONTRACT])['addition']['filename'], deb)
        profile = {'nonce': os.urandom(32).hex(), 'host_userns_inode': shared.namespaces()['user'],
                   'host_driver_pid': os.getpid(), 'repo_manifest_sha256': repo_sha}
        shared.atomic_json(inputs / 'api-profile.json', profile)
        runner = 'STAGE=' + repr(name) + '\n' + time_core.PROP_RUNNER
        runner += ('import importlib.util,pathlib,json\ns=importlib.util.spec_from_file_location("recipe","/a733-sources/project/'
                   + ADAPTER + '")\nm=importlib.util.module_from_spec(s)\ns.loader.exec_module(m)\n')
        expected = time_core.held(phase / 'work/timesync-receipt.json', owner=shared.UID_BASE, limit=1024**2)[1]['sha256']
        runner += ('print(json.dumps(m.export(pathlib.Path("/a733-export-output/export-timesync-result"),'
                   + repr(expected) + ',profile="time"),sort_keys=True))\n')
        pipeline.immutable_file(inputs / 'run.py', runner.encode())
        os.chmod(inputs, 0o555)
        return slot, profile

    def verify(shared, phase, contents, manifest, configured_sha):
        actual = original_verify(shared, phase, contents, manifest, configured_sha)
        expected = validate_sources(time_core, contents)['profiles']['time']
        output = phase / 'work/export-timesync-result'
        receipt = json.loads(time_core.held(output / 'receipt.json', owner=shared.UID_BASE, limit=1024**2)[0])
        for key in ('archive', 'raw_tar', 'members', 'inventory', 'bom'):
            require(receipt.get(key) == expected[key], 'time replay export ' + key + ' differs')
        require(receipt.get('timestamps', {}).get('epoch') == expected['epoch'], 'time replay export epoch differs')
        replay = receipt.get('account_date_replay', {})
        staged = time_core.held(phase / 'export/inputs/manifest.json', owner=0, limit=1024**2)[0]
        require(json.loads(staged) == manifest, 'time replay staged manifest differs')
        require(replay.get('schema') == 1 and replay.get('profile') == 'time'
                and replay.get('adapter') == pin(contents[ADAPTER]) and replay.get('helper') == pin(contents[HELPER])
                and replay.get('contract') == pin(contents[CONTRACT])
                and replay.get('source_manifest') == pin(staged)
                and replay.get('input_receipt_sha256') == configured_sha, 'time replay transformation provenance differs')
        return actual

    time_core.base_sources = lambda shared, base, contents, report, builder: base_sources(
        time_core, shared, base, contents, report, builder)
    time_core.stage_inputs, time_core.verify_export = stage, verify
    return time_core


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--base-tag', required=True)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--extra-deb', required=True, type=Path)
    parser.add_argument('--plan', action='store_true')
    options = parser.parse_args(argv)
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    time_core = configure_timesync(authenticated_core(options.project))
    phase = time_core.tag_path(options.tag)
    base = time_core.tag_path(options.base_tag, 'userspace-pipeline-') / 'work'
    time_core.absolute(options.extra_deb)
    manifest, contents = time_core.committed_sources(options.project)
    require(source_bytes(Path(__file__).absolute()) == contents[SELF], 'executing driver differs from committed source')
    require(pin(contents[CORE]) == CORE_PIN, 'historical time launcher changed')
    validate_sources(time_core, contents)
    report = time_core.plan(options.tag, options.base_tag, options.extra_deb, manifest)
    report['account_date_replay'] = {
        'schema': 1, 'profile': 'time', 'driver': pin(contents[SELF]), 'time_pipeline_component': CORE_PIN,
        'baseline_driver': BASE_DRIVER_PIN, 'adapter': pin(contents[ADAPTER]), 'helper': pin(contents[HELPER]),
        'contract': pin(contents[CONTRACT]),
        'classification': 'new replay using historical components; not historical launcher execution'}
    if options.plan:
        print(json.dumps(report, sort_keys=True))
        return
    shared = time_core.module(time_core.SHARED, contents[time_core.SHARED])
    pipeline = time_core.module(time_core.PIPELINE, contents[time_core.PIPELINE])
    builder, before, facts = time_core.preflight(shared, pipeline, phase, base, contents)
    report.update(facts)
    inputs = time_core.base_inputs(shared, base, contents, options.extra_deb, builder)
    time_core.execute(shared, pipeline, builder, phase, base, manifest, contents, inputs, before, report)
    print(json.dumps({'success': True, 'phase': str(phase), 'export': str(phase / 'work/export-timesync-result')}))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Build only the pinned host FEL version probe in the frozen private builder."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import types

SELF = 'build/launch-fel-version.py'
ROOTS = 'build/launch-host-roots.py'
VERIFIER = 'build/verify-host-roots.py'
COMMIT = '34dfa20f8284273085666436e4cac09af19646c8'
TREE = '9394e0d05f62c99645cbd6ebd5189d81d5635e97'
BUNDLE = {'bytes': 763416, 'sha256': 'b1692eb0752c8e930fadd715d783ff40ef5ef4003f2497c1a3e9efc44ccedb3d'}
ARCHIVE_SHA = 'ed18650ce80d4c44b53f59fb55f9608253a35ae41f75ccc0863462c4b56eee77'
ENV = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'TZ': 'UTC', 'GIT_CONFIG_NOSYSTEM': '1',
       'GIT_CONFIG_GLOBAL': '/dev/null'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def phase_path(tag):
    require(isinstance(tag, str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', tag), 'unsafe tag')
    return Path('/var/lib/a733-clean-replay-20261106') / ('fel-version-' + tag)


def payload_program(tag):
    phase_path(tag)
    return ('''import importlib.util,os,pathlib,subprocess
os.umask(0o022)
p=pathlib.Path('/a733-export-output/project')
u=pathlib.Path('/a733-export-output/upstream')
env={'PATH':'/usr/bin:/bin','LC_ALL':'C','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null'}
subprocess.run(['git','clone','--no-checkout','/a733-debs/upstream.bundle',str(u)],env=env,check=True)
subprocess.run(['git','-C',str(u),'checkout','--detach',@COMMIT@],env=env,check=True)
assert subprocess.check_output(['git','--no-replace-objects','-C',str(u),'rev-parse','HEAD^{tree}'],env=env).decode().strip()==@TREE@
spec=importlib.util.spec_from_file_location('version_recipe',p/'build/fel-version.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
assert r.COMMIT==@COMMIT@
r.build(u,p/OUTPUT)
'''.replace('@COMMIT@', repr(COMMIT)).replace('@TREE@', repr(TREE))
        .replace('OUTPUT', repr('out/fel-version-' + tag))).encode()


def verify_output(roots, candidate, output, contents):
    manifest_pin, raw = candidate.authenticated_file(output / 'manifest.json', retain=True)
    value = json.loads(raw)
    require(value['source_commit'] == COMMIT and value['hardware_tested'] is False,
            'upstream or hardware result differs')
    files = {str(p.relative_to(output)) for p in candidate.regular_tree_files(output)}
    require(files == set(value['output_hashes']) | {'manifest.json'}, 'output coverage differs')
    pins = {}
    for name, expected in value['output_hashes'].items():
        candidate.safe_member(name)
        pins[name] = candidate.authenticated_file(output / name)[0]
        require(pins[name]['sha256'] == expected, 'output hash differs: ' + name)
    require(pins['sunxi-tools-source.tar']['sha256'] == ARCHIVE_SHA and
            pins['fel-version.c'] == roots.pin(contents['build/fel-version.c']) and
            pins['fel-version.py'] == roots.pin(contents['build/fel-version.py']) and
            pins['builder-replay-packages-20261106.lock.tsv'] == roots.pin(contents[roots.HOST_LOCK]),
            'retained source/recipe/dependency pins differ')
    recipe = roots.module(str(output.parents[1] / 'build/fel-version.py'), contents['build/fel-version.py'])
    require(set(value['dependencies']) == set(recipe.PACKAGES) and
            set(value['needed']) == {'libusb-1.0.so.0', 'libc.so.6'}, 'dependency coverage differs')
    lock = {row.split('\t')[0]: row.split('\t')[1] for row in contents[roots.HOST_LOCK].decode().splitlines()}
    require(all(version == lock[name] for name, version in value['dependencies'].items()), 'dependency pin differs')
    require(set(value['source_hashes']) == {name.removeprefix('source/') for name in files if name.startswith('source/')},
            'complete source coverage differs')
    require(all(pins['source/' + name]['sha256'] == expected for name, expected in value['source_hashes'].items()),
            'retained source hash differs')
    return {'manifest': manifest_pin, 'binary': pins['fel-version'], 'files_verified': len(pins),
            'upstream_commit': COMMIT, 'upstream_archive_sha256': ARCHIVE_SHA, 'hardware_acceptance': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--upstream-bundle', required=True, type=Path)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--plan', action='store_true')
    options = parser.parse_args(argv)
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    for path in (options.project, options.upstream_bundle):
        require(path.is_absolute() and str(path) == os.path.abspath(path) and
                not any(path == root or root in path.parents for root in map(Path, ('/dev', '/proc', '/sys'))),
                'absolute non-pseudo input required')
    source = subprocess.check_output(['/usr/bin/git', '-C', str(options.project), 'show', 'HEAD:' + ROOTS], env=ENV)
    roots = types.ModuleType('fel_version_roots'); roots.__file__ = str(options.project / ROOTS)
    exec(compile(source, roots.__file__, 'exec'), roots.__dict__)
    manifest, contents = roots.committed_sources(options.project)
    require(roots.read_source(Path(__file__).absolute()) == contents[SELF] and source == contents[ROOTS],
            'executing source differs from committed closure')
    require(roots.module(str(options.project / 'build/fel-version.py'), contents['build/fel-version.py']).COMMIT == COMMIT,
            'recipe upstream differs')
    phase = phase_path(options.tag)
    report = {'schema': 1, 'phase': str(phase), 'sources': manifest, 'upstream_bundle': BUNDLE,
              'upstream_commit': COMMIT, 'upstream_tree': TREE, 'hardware_acceptance': False,
              'physical_storage_written': False, 'usb_access_performed': False,
              'reproducibility': 'One compilation; hardware and full recovery unproven.',
              'work_mode': 'disk', 'timeout_seconds': roots.TIMEOUT}
    if options.plan:
        print(json.dumps(report, sort_keys=True)); return
    verifier = roots.module(str(options.project / VERIFIER), contents[VERIFIER])
    shared = roots.module(roots.SHARED, contents[roots.SHARED])
    candidate = roots.module(roots.CANDIDATE, contents[roots.CANDIDATE])
    with contextlib.ExitStack() as stack:
        upstream = stack.enter_context(verifier.Held(options.upstream_bundle, BUNDLE))
        objects = sum(int(row.split()[2]) for row in roots.git(options.project, 'cat-file', '--batch-all-objects',
                      '--batch-check=%(objectname) %(objecttype) %(objectsize)').decode().splitlines())
        require(objects <= 512 * 1024**2, 'Git staging bound exceeded')
        git_bound = 3 * objects + sum(v['bytes'] + 4096 for v in manifest['files'].values()) + 64 * 1024**2
        budget = {'recipe_bytes': 4 * 1024**3, 'capsule_bytes': BUNDLE['bytes'],
                  'git_staging_bound_bytes': git_bound, 'required_free_bytes': 4 * 1024**3 + BUNDLE['bytes'] + git_bound}
        report['initial_budget'] = budget
        builder, before = roots.preflight(shared, contents, phase, budget)
        capsule = {}
        def final_check():
            upstream.unchanged()
            for held in capsule.values(): held.unchanged()
            if capsule:
                require(set(capsule) == {str(p.relative_to(phase / 'inputs')) for p in candidate.regular_tree_files(phase / 'inputs')},
                        'capsule namespace changed')
            require(roots.committed_sources(options.project)[0] == manifest, 'source closure changed')
        def stage(c, s, project, ph, m, policy, unused_u, unused_d, tag):
            upstream.unchanged()
            inputs = ph / 'inputs'; inputs.mkdir(mode=0o700)
            c.authenticated_file(upstream.path, BUNDLE, destination=inputs / 'upstream.bundle')
            subprocess.run(['/usr/bin/git', '-C', str(project), 'bundle', 'create', str(inputs / 'project.bundle'), 'HEAD'], env=ENV, check=True)
            s.atomic_json(inputs / 'manifest.json', m)
            (inputs / 'run.py').write_bytes(roots.inner_program(m['revision'], tag))
            (inputs / 'payload.py').write_bytes(payload_program(tag))
            for path in inputs.iterdir(): os.chown(path, 0, 0); os.chmod(path, 0o444)
            os.chmod(inputs, 0o555)
            for path in c.regular_tree_files(inputs):
                capsule[str(path.relative_to(inputs))] = stack.enter_context(verifier.Held(path))
            final_check()
            return c.authenticated_file(inputs / 'project.bundle')[0]
        output = phase / 'work/project/out' / ('fel-version-' + options.tag)
        roots.execute(shared, candidate, builder, options.project, phase, manifest, {}, before, report,
                      options.upstream_bundle, options.upstream_bundle, options.tag,
                      output_name=output.name, stage=stage,
                      verify=lambda c, path, policy: verify_output(roots, c, path, contents),
                      expectations=lambda c, root: {'hardware_acceptance': False}, final_check=final_check)
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()

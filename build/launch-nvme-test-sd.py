#!/usr/bin/env python3
"""Build the registered NVMe-first EL2 firmware transport SD, never program SPI.

This authenticated adapter preserves the production-state bootstrap producer and
its isolation/readback checks. It changes only the explicit firmware selection.
"""
import hashlib
from pathlib import Path
import subprocess
import sys
import types

SELF = 'build/launch-nvme-test-sd.py'
PARENT = 'build/launch-host-bootstrap.py'
PARENT_SHA = 'bcadf141568ccfa77d875ca1a335fb00792125c831cc4f02aef7b8a1bd6cdc9f'
PROFILE = 'nvme-first-el2-test'
ENV = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
       'GIT_CONFIG_GLOBAL': '/dev/null'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def adapt(parent):
    """Install explicit hooks over the exact authenticated historical producer."""
    original_contract = parent.contract
    original_program = parent.payload_program
    original_plan = parent.plan

    def contract(roots, project, contents):
        policy = original_contract(roots, project, contents)
        assembly = roots.module(str(project / 'build/assemble-host.py'), contents['build/assemble-host.py'])
        pinned, metadata, recipes = assembly.load_boot_profile(PROFILE)
        require(metadata['name'] == PROFILE and metadata['target_service'] == '0x8000ff40' and
                metadata['expected_spsr'] == '0x3c9' and metadata['physical_acceptance_pending'] is True,
                'registered test profile handoff differs')
        require(all(pinned[name] == policy['diagnostic'][name]
                    for name in parent.BOOT_FILES if name != 'boot_package.fex'),
                'NVMe firmware test changes immutable diagnostic input')
        policy['diagnostic']['boot_package.fex'] = pinned['boot_package.fex']
        policy['boot_profile'] = metadata
        # The parent adds EL2 evidence to its recipe set. The new profile owns its
        # exact evidence set instead, while baseline RECIPES remain unchanged.
        policy['sd_recipes'] = {name: roots.pin(contents[name]) for name in assembly.RECIPES}
        policy['sd_recipes'].update({name: roots.pin(data) for name, data in recipes.items()})
        return policy

    def program(tag, selected_uuid):
        raw = original_program(tag, selected_uuid)
        require(raw.count(b"boot_profile='experimental-el2'") == 2,
                'historical payload selection anchors differ')
        return raw.replace(b"boot_profile='experimental-el2'",
                           b"boot_profile='nvme-first-el2-test'")

    def plan(options, manifest, policy):
        result = original_plan(options, manifest, policy)
        result['boot_profile'] = PROFILE
        result['firmware_test_scope'] = ('SD transports registered NVMe-first U-Boot; '
            'default payload comes from NVMe, manual SD fallback retains original host script; '
            'SPI, physical boot and KVM acceptance remain pending.')
        return result

    parent.contract, parent.payload_program, parent.plan = contract, program, plan


def main(argv=None):
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    # The parent owns CLI parsing and authenticated full-source closure. Locate
    # --project only to load that exact committed parent before delegating.
    argv = list(sys.argv[1:] if argv is None else argv)
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--project', required=True, type=Path)
    options, _ = parser.parse_known_args(argv)
    project = options.project
    require(project.is_absolute() and project.resolve() == project,
            'project must be a physical absolute path')
    def committed(name):
        return subprocess.check_output(['/usr/bin/git', '-C', str(project), 'show', 'HEAD:' + name], env=ENV)
    require(Path(__file__).read_bytes() == committed(SELF), 'adapter differs from committed source')
    raw = committed(PARENT)
    require(hashlib.sha256(raw).hexdigest() == PARENT_SHA and
            (project / PARENT).read_bytes() == raw, 'historical bootstrap controller differs')
    parent = types.ModuleType('nvme_test_sd_bootstrap')
    parent.__file__ = str(project / PARENT)
    exec(compile(raw, parent.__file__, 'exec'), parent.__dict__)
    adapt(parent)
    parent.main(argv)


if __name__ == '__main__':
    main()

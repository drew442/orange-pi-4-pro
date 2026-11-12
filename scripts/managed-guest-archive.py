#!/usr/bin/python3
"""Offline historical managed-cohort validation; never restore authority.

The caller still needs an out-of-band trusted whole-archive hash, matching disk
and artifact hashes, and fresh actual destination authority before restoration.
Source storage/boot identities here are historical, never live host identities.
Authentication of this decoder and its packaged reviewed catalogue is also a
caller prerequisite; archive-supplied catalogues are never accepted.
No capability, file descriptor, process observation or publication is produced.
"""
import copy
import importlib.util
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


FORMAT = _module('managed_archive_format', 'configs/guest_managed_backup.py')
CP = _module('managed_archive_checkpoint', 'scripts/managed-guest-checkpoint.py')
R, HEAD, LIFECYCLE = CP.RECORDS, CP.HEAD, CP.LIFECYCLE
require = FORMAT.require


# Read the packaged catalogue once, bounded and independent of live host state.
with CP.FIXTURE.LOCK_PATH.open('rb') as _catalogue_stream:
    CATALOGUE_DATA = _catalogue_stream.read(4097)
require(0 < len(CATALOGUE_DATA) <= 4096, 'packaged catalogue size differs')
CP.FIXTURE.validate_lock(CATALOGUE_DATA)
CATALOGUE_SHA256 = CP.digest(CATALOGUE_DATA)


def validate_cohort(manifest_data, lifecycle_data):
    """Return detached plain historical data after complete legal cohort replay.

    Mirrors CP._cohort_records and CP._check_completion's artifact predicates.
    Reuses only pure schemas/replay/evidence helpers; never manufactures custody
    or invokes root runtime APIs. JSON evidence cannot establish hardware truth.
    """
    envelope = FORMAT.validate_lifecycle(lifecycle_data, manifest_data)
    registry_raw, head_raw = (CP.decoded(envelope[key]) for key in ('registry', 'head'))
    registry, head = CP.REGISTRY.validate_registry(registry_raw), HEAD.validate_head(head_raw)
    manifest = CP.MANIFEST.validate_manifest(manifest_data)
    entry = next(item for item in registry['guests'] if item['name'] == head['name'])
    records = {name: CP.decoded(ref) for name, ref in envelope['records'].items()}
    result = dict(registry=registry, head=head, entry=entry, manifest=manifest,
                  registry_data=registry_raw, head_data=head_raw, manifest_data=manifest_data,
                  records=records, run=None, intent=None, outcome=None, completion=None,
                  permit=None, fixture=None, heads=[])
    # FORMAT's exact initial ready-head exemption also requires zero records.
    if head['sequence'] == 0:
        return copy.deepcopy(result)
    prefix = 'runs/' + head['run_id']
    admission_raw = records[prefix + '.json']
    run = LIFECYCLE.validate_run(admission_raw)
    require(LIFECYCLE.canonical_run(run) == admission_raw and
            all(run[k] == head[k] for k in ('storage', 'name', 'uid', 'gid', 'run_id')) and
            run['boot_id'] == head['last_boot_id'], 'historical admission/head identity differs')
    intent_raw, outcome_raw, completion_raw = (records[prefix + suffix] for suffix in
        ('.intent.json', '.outcome.json', '.completed.json'))
    intent, outcome, completion = (R.validate_intent(intent_raw), R.validate_outcome(outcome_raw),
                                   R.validate_completion(completion_raw))
    require(CP.common(intent) == CP.common(run) and
            intent['admission_sha256'] == CP.digest(admission_raw) and
            intent['old_manifest']['sha256'] == run['manifest_sha256'] and
            intent['registry']['observed_generation'] >= run['registry_generation'],
            'historical intent/admission differs')
    require(CP.common(outcome) == CP.common(run) and
            all(outcome[k] == run[k] == entry[k] for k in ('shutdown_profile', 'fixture_profile')),
            'historical outcome/admission/profile differs')
    console = records[prefix + '.console']
    old = CP.MANIFEST.validate_manifest(CP.decoded(intent['old_manifest']))
    permit = R.PERMIT.validate_permit(CP.decoded(outcome['permit']))
    require(permit['config'] == old['config'] and
            permit['manifest_sha256'] == run['manifest_sha256'],
            'historical permit original manifest/config differs')
    heads = CP._heads(run, intent_raw, intent)
    require(head == heads[5] and
            permit['head_sha256'] == CP.digest(HEAD.canonical(heads[1])),
            'historical ready/permit admitted head differs')
    for previous, new in zip(heads, heads[1:]):
        previous_raw, new_raw = HEAD.canonical(previous), HEAD.canonical(new)
        name = 'transitions/' + CP.digest(previous_raw) + '-' + CP.digest(new_raw) + '.json'
        require(records.get(name) == LIFECYCLE.head_transition(previous_raw, new_raw),
                'historical legal immutable head edge missing/differs')
    require(CP.common(completion) == CP.common(run) and
            CP.decoded(completion['ready_head']) == head_raw,
            'historical completion cohort differs')
    # FORMAT already checks exact canonical outcome/intent/completion bytes,
    # console/outcome/intent pins, new manifest and bounded registry generations;
    # it also enforces same-generation registry hash and the exact member set.
    proof = None
    if intent['fixture'] is not None:
        proof_raw = records[prefix + '.fixture.json']
        proof = CP._proof(proof_raw)
        require(all(proof.get(k) == run[k] for k in ('storage', 'name', 'run_id', 'boot_id')) and
                proof.get('admission_sha256') == CP.digest(admission_raw) and
                proof.get('old_manifest_sha256') == run['manifest_sha256'] and
                proof.get('permit_sha256') == outcome['permit']['sha256'] and
                proof.get('outcome') == outcome['shutdown'] and
                proof.get('stopped_disk') == intent['disk'] and
                dict(bytes=proof.get('console_bytes'), sha256=proof.get('console_sha256')) == intent['console'] and
                proof.get('kernel') == CP.FIXTURE.KERNEL and proof.get('initrd') == CP.FIXTURE.INITRD and
                proof.get('profile_sha256') == CATALOGUE_SHA256 and
                type(proof.get('previous_counter')) is int and type(proof.get('counter')) is int and
                0 <= proof['previous_counter'] < 99999999 and
                proof['counter'] == proof['previous_counter'] + 1,
                'historical fixture cross-record binding differs')
        CP._fixture_evidence(proof, old, console)
    result.update(run=run, intent=intent, outcome=outcome, completion=completion,
                  permit=permit, fixture=proof, heads=list(heads))
    return copy.deepcopy(result)

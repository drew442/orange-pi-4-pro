> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Managed checkpoint publication and reconciliation

Coordinator contract, 2026-11-12. Implementation and physical acceptance are
pending. This refines [the lifecycle design](../../guest-autostart-design.md), preserving
[manual checkpoint authorization](../../guest-checkpoints.md). It does not introduce an
operator attestation bypass or treat a current disk hash as a trusted starting pin.

## Authority transfer

A live `Supervisor.StoppedRun` proves the retained direct-child/QMP/console
observations, not filesystem cleanliness or checkpoint authority. It is valid
only in its creating process/thread, while the original head remains `stopping`.
The checkpoint wrapper must consume it before advancing the head. Later guards
use authenticated durable records; they must not weaken `StoppedRun.recheck()` to
accept a different head.

Use a private-token `CheckpointCustody.from_outcome()` factory in the checkpoint
module. It checks the actual outcome/run/inspection in one capability module
family (`FIXTURE.SUPERVISOR.LIFECYCLE.POLICY`), captures its creating PID/thread,
and borrows the live context, lease, guest lock and protected stores. Its guards
authenticate these resources, the immutable admission/outcome/intent and the
exact intended current head, independently of the now-stale run/outcome guards.
Do not retarget the old run. A distinct recovery constructor authenticates durable
cohorts without inventing live objects. Custody quarantines against its own exact
current head and never closes borrowed run/supervisor/lease resources.

Separate nonrecursive physical/lock guards from explicit logical head checks.
`RootDirectory.publish` calls its guard after rename: a guard pinned exclusively
to the old head would reject every successful CAS. The custody transition method
must bind one exact legal old/new pair, use physical/record guards during its
publication, verify the new head at the acknowledgement boundary and only then
advance its expected pin. Exceptions poison it while retaining the old expected
pin and receipts. Authenticate records through independent retained readers with
physical guards; never recurse from a guarded store read back into that same
store's guarded reader.

The root wrapper requires that actual outcome, the retained managed run and its
lease/guest lock, selected storage, a supported shutdown profile and a fresh
stopped `POLICY.Inspection` using the independently trusted old manifest pin.
No synthetic TTY, environment override, `authorized=True`, supplied outcome JSON
or software exit code can substitute for these objects and checks.

For `managed-power-key-v1`, the actual fixture validator must authenticate its
reviewed kernel/initrd/catalog, pre-run disk/counter, live KVM descriptors and
QEMU-owned loopback listener, CPU/disk/HTTP confirmation, continued execution
until host powerdown, explicit guest sync and persisted stopped counter.
Its `attest_stopped(outcome, inspection)` runs while the original outcome is
still valid. Preserve the canonical proof as a separate immutable record.
The `none` fixture profile describes a configured production guest supporting
the selected normal shutdown protocol; it grants no fixture acceptance or
application/filesystem integrity claim. Unknown profiles refuse.

## Retained records

Use the already authenticated per-guest p5 head directory and private `runs/`
store. Every record is root:root 0400, single-link, bounded, no-follow and
immutable after publication. File and directory fsync plus successful close
acknowledgements are required. Exact existing retries require revalidation and
synchronization; partial/unsafe/conflicting files are never repaired or adopted.

Names derive only from the canonical admitted run UUID:

| Record | Contents and bound |
| --- | --- |
| `<run>.console` | Complete captured console, at most 8 MiB; overflow refuses |
| `<run>.outcome.json` | Canonical durable envelope below; at most 10 MiB |
| `<run>.fixture.json` | Optional exact canonical managed-fixture proof; at most 256 KiB |
| `<run>.intent.json` | Exact prepared checkpoint transition below; at most 2 MiB |
| `<run>.completed.json` | Exact intent/outcome, resulting head and registry publication acknowledgement; at most 64 KiB |

The intent has schema 1 and profile `a733-managed-checkpoint-intent-v1`. Its
closed field set binds storage, name/UID/GID, run/boot UUIDs, admission hash,
canonical stopping-head bytes/hash, outcome/console/optional-fixture references,
old and proposed manifest bytes/hashes, observed stopped disk size/hash,
changed/no-op classification, optional exact history bytes/hash, and observed
registry generation with the intended old/new pin change. Base64 encodings must
be canonical and bounded before decoding. Validate all cross-record identities,
versions, file sizes, pins and transitions before any checkpoint mutation.

The outcome envelope has schema 1 and profile `a733-managed-outcome-v1`, with a
closed field set: schema/profile, storage, name/UID/GID, run/boot UUIDs,
admission hash, shutdown/fixture profiles, canonical permit bytes/hash/size,
exact supervised shutdown evidence and its canonical hash, console hash/size,
and optional fixture-proof hash/size. The full permit retains child/parent
PID/start ticks, QEMU identity, namespace and lock bindings after RAM is gone.
The fixture's embedded shutdown evidence must equal the envelope's evidence;
the envelope binds the complete fixture proof. Produce the proof first, then
the envelope, then the manifest/intent: no cyclic final-envelope hash is required
inside the proof. Ordinary parsing grants no authority.

Bind `qmp-guest-shutdown-v1` explicitly in new admission records. Reject other
shutdown profiles and fixture profiles outside `none`/`managed-power-key-v1`
before a new run record or checkpoint is published. The legacy HTTP fixture label
may remain parseable as registry policy but cannot enter this managed lifecycle.
The limits include framing/base64 overhead: two manifest payloads up to 64 KiB,
history up to 512 KiB, and a stopping head up to 64 KiB fit the 2 MiB intent;
an 8 MiB shutdown body plus a 64 KiB permit and envelope fit 10 MiB. Enforce actual
serialized sizes and every referenced limit before checkpoint-head mutation.

Preserve the exact old manifest bytes, even if their JSON is noncanonical.
For changed disks the new manifest is canonical schema 2 with managed shutdown
provenance, and history uses `a733-managed-guest-checkpoint-v2`. Provenance hashes
the outcome that precedes it; it does not hash the intent containing the proposed
manifest, avoiding a digest cycle. For unchanged disks preserve the exact old
manifest bytes/pin, omit history and still complete a durable run transition.
Do not manufacture a manual `clean_shutdown_attested` field.

## Publication order

Keep the existing global lease and per-guest EX lock throughout. Acquire the
stopped inspection/disk flock, then take registry EX only for its bounded read
or final merge; never acquire another guest/disk lock while holding registry EX.

1. Recheck actual shutdown, fixture and stopped inspection. Retain exact console,
   outcome and optional fixture records durably before deriving the manifest.
2. Read the latest registry under registry EX. Bind the guest identity and old
   manifest pin. Record the observed generation, then release registry EX.
3. Publish the complete immutable intent. Publish the immutable head transition
   and CAS the head from the exact stopping head to `checkpoint`, binding the
   intent hash. Only then may the shared history/manifest engine mutate metadata.
4. Run the existing publication engine with the retained inspection, exact plan
   and schema-2 history, plus an authorization guard that authenticates storage,
   locks, durable outcome/intent and the exact checkpoint head. Normal raw exit,
   guest-owned SHUTDOWN and fixture requirements remain mandatory. Unchanged
   disks skip history/manifest writes and retain a no-op completion path.
5. Authenticate the exact published new manifest/history and the retained stopped
   disk. Publish the immutable checkpoint-to-ready head transition and CAS the
   head to the intended new pin with `last_completed_intent_sha256` retained.
6. Reread the latest registry under registry EX. Merge only this guest's old pin
   to its new pin, preserving current enabled flags and unrelated entries.
   An already matching new pin is a synchronized no-op. Conflicts refuse.
7. Publish/synchronize the immutable completion acknowledgement. Only a verified
   coherent ready cohort permits another launch. Close retained resources and
   report cleanup failures; an uncertain acknowledgement never becomes success.

Prepare a typed stopped-disk publication handoff before step 4. It retains the
original disk flock and kernel/initrd/directory FDs. Original `Inspection.recheck`
semantics remain unchanged and deliberately reject the old enrollment inode
after replacement. The publication handoff then authenticates only the exact
intent-authorized new manifest/history, stable disk inode/metadata/hash, storage,
accounts, service and consumer census, without releasing/reacquiring the flock.
There is no generic ignore-manifest flag or adoption of any current inode.
Recovery already on the intended new manifest can open its fresh ordinary
inspection directly; it does not open a second competing disk lock.

A concurrent disable remains disabled. Another guest's global generation advance
is not a reason to overwrite the registry with stale intent bytes. Failed
publication preserves partial receipts and immutable records; no automatic local
rollback, arbitrary hash adoption or retry of a possibly reused FD/PID occurs.

Completion bytes are a historical acknowledgement, not a fresh registry snapshot
on every retry. If a completion record exists, validate/synchronize those exact
bytes and authenticate the current registry separately. If absent, hold registry
EX through merging, capture that exact observed publication and create the
immutable acknowledgement before releasing EX. Later unrelated updates and
disable operations do not regenerate the acknowledgement.

Lifecycle success also requires observable cleanup acknowledgement. In particular,
current `Supervisor.close()` records owned-resource errors in `receipt.errors`
and can return normally; the wrapper must inspect/surface them, not treat a
normal return as success. Close the supervisor's owned resources only after
durable authority transfer; retain the borrowed context/lease/guest lock and disk
custody through required publications. A completion record acknowledges the
historical cohort publication, not a later failed close. Surface all such failures
and require fresh coherent verification/synchronization before reporting recovery
success; never silently replay a mutation because cleanup acknowledgement failed.

## Recovery decisions

Reconciliation uses a new root context and retained lease/guest/disk locks, not
a forged live shutdown object. Trust only the protected admission, exact complete
intent/outcome/proof, head chain and manifest history on the authenticated p5.
Synchronize exact visible records after an ambiguous durability acknowledgement.
Inspect the actual stopped disk against the intent's hash; never replace that
hash with newly observed unexplained bytes.

| Actual state | Permitted action |
| --- | --- |
| Exact recorded stopping head; complete outcome/intent; old manifest and intended disk | Publish the recorded checkpoint-head edge, then complete that exact prepared transaction |
| Exact checkpoint head bound to intent; old manifest and intended disk | Finish only the recorded history/manifest transaction |
| Exact checkpoint head; intended new manifest, exact history and intended disk | Finish head/registry/completion publication without rewriting disk or manufacturing history |
| Ready head with the same completed intent/new pin and matching complete cohort | Finish a missing registry/completion acknowledgement; a complete repeat changes no sequence, generation or checkpoint number |
| Admitted/running head without a complete matching shutdown intent | Quarantine; never infer a clean run from current disk bytes |
| Conflicting intent/head/history/disk, unsupported schema or unavailable authority | Quarantine where authenticated publication is possible; otherwise leave automation disabled and management available |
| Quarantined head | No automatic clearing or force option; verified backup restoration and separately reviewed recovery are required |

A later run/head is not an invitation to replay an earlier intent. Preserve all
failed/partial attempts. Regressions must cover interruption before/after every
record, history, manifest, head, registry and completion publication; real fsync
and close failures; concurrent disable/unrelated updates; unchanged runs; stale
STATE and mismatched backup cohorts. Old STATE alone is not a supported restore.
Backup/restore must carry matching disk, manifests/history, heads, runs and
registry policy; restoration needs its own reviewed head/cohort recovery path.

Admission, enable and boot preparation must share `authenticate_ready_cohort`.
Only explicit initial enrollment is exempt. A completed ready head requires its
exact admission, intent, outcome/proof, manifest/history, disk and synchronized
completion record before a later run. Recompute legal `HEAD.mark_checkpoint` and
`HEAD.complete` edges; sequence/identity-only head-chain parsing is insufficient.
This closes the interruption window between registry and completion publication.

Host checks do not satisfy the physical gate: actual nonroot A733 KVM fixture
execution, host-requested guest shutdown, persisted counter, checkpoint/restart,
boot shutdown ordering, failure quarantine and independent deployment reproduction
remain required. Reuse accepted endurance evidence; cap planned tests at eight
hours and do not rerun qualification already supported by retained data.

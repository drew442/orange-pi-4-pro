# Unattended guest lifecycle design

Coordinator design, 2026-11-12; implementation and hardware acceptance pending.
This refines phase 4 of the [production plan](spi-production-readiness-plan.md).
The registry schema is only a pure validation foundation. It does not start a
VM, authorize a checkpoint or authenticate live storage.

## Persistent authority and rollback

NVMe STATE holds `/run/a733-state/guests/autostart.json` under a root-owned 0700
parent; the registry is root:root 0400, bounded, versioned and atomically published.
It binds the STATE UUID, guest filesystem UUID and NVMe serial. Entries bind
name, stable UID/GID, trusted manifest pin, enabled flag and explicit shutdown
and fixture profiles. Missing registry means no automation. Unsupported schema
or invalid storage leaves management running and reports automation unavailable.
Enrollment is opt-in and requires the independently trusted enrollment/checkpoint
pin. Keep `firstkvm` manual; use a separately enrolled disposable automation guest.

The guest filesystem separately retains authoritative lifecycle heads below
`/run/a733-guests/.a733-lifecycle/<name>/`, root:root 0700 with 0400 records.
Keep this outside individual guest directories to preserve the existing strict
artifact inventory and checkpoint history format. Bind each head to storage,
guest identity, generation, trusted manifest and run/transition status. Opening
an enabled registry entry requires agreement with its head and current enrollment;
registry restoration cannot move the head backwards. No timestamps select trust.
The first head is established only by explicit enrollment using a trusted pin.

A/B host rollback preserves the newest disk/manifest/history/head cohort.
Older roots without this controller leave automation off. Compatible roots
validate the same schema; unsupported versions do not attempt migration at boot.
Guest metadata restoration requires matching verified guest disk backup and
root/STATE pairing. Restoring an old STATE alone must fail closed when its
registry disagrees with the authoritative head. Restoring every authority together
requires the backup/recovery procedure's trusted cohort provenance; local bytes
cannot prove freshness after all trust anchors have been replaced.

## Coordination and boot preparation

The installer must explicitly record the reviewed storage selection before the
first registry can be created. Reserve
`/run/a733-state/config/guest-storage.json`, root:root 0400 below the existing
root:root 0700 config directory on selected STATE. The
`a733-guest-storage-policy-v1` data binds the fixed NVMe target, expected model
and byte size, and STATE UUID/guest UUID/NVMe serial. It contains no enabled
guests or launch permission. Do not populate it from unreviewed device discovery
or treat a missing registry as permission to choose whichever disk is present.

The pure [policy schema](../configs/guest_storage_policy.py) is implemented;
its protected reader, installer publication and bootstrap hook remain pending.
The reader must retain nofollow descriptors, authenticate ownership/modes,
selected-STATE placement and unchanged bytes, and revalidate live identities.
Canonical parsing alone cannot establish provenance or authorize mounting.
Missing or invalid policy leaves automation unavailable and management running.
Publish policy through the reviewed installation/setup transaction, separately
from the operator's later opt-in guest enrollment.

Use the two-stage RAM admission protocol specified below, ordered before
existing account, activation, enrollment, checkpoint and disk locks. Runtime
launch/checkpoint admission participates in it; host update/selector/recovery
operations take exclusive admission and require all managed runs stopped and
checkpoint transitions resolved. Do not hold an exclusive admission lock while
waiting for a guest to shut down. Review each existing entrypoint: a new lock used
only by the controller cannot exclude older writers. Existing retained descriptor,
namespace, storage placement and disk-consumer checks remain necessary.

Boot preparation runs after management preparation and exact STATE/p5 activation.
Validate root/STATE pairing and the registry before creating locked RAM accounts
through the existing account planner. Render services in RAM from authenticated
current enrollment. Preparation is idempotent; registry publication uses expected
hash/generation checks under locks. Invalid guest entries quarantine that guest;
invalid global registry disables automation. Neither outcome blocks SSH/NTP.

Use a separate root supervisor service for each enabled guest. It launches the
existing KVM-only launcher as the locked guest UID/GID with only the KVM group,
zero capabilities and no-new-privileges. Retain memory/task limits, native mount
namespace and isolated networking. The supervisor owns protected evidence and
lifecycle metadata; the QEMU child cannot edit them. One launch per admitted run,
with no automatic restart initially, prevents crash loops. Verify the final
unprivileged QEMU credentials, retained inputs and actual VM/vCPU/VGIC descriptors
on hardware.

## Shutdown evidence and durable transitions

Before spawning QEMU, durably record a random run ID, boot ID, old manifest,
storage/config identity and planned supervision endpoint. Failure to publish
prevents launch. Extend the launcher through a constrained managed interface;
never accept arbitrary extra QEMU arguments. Preserve manual launcher/unit behavior.

The supervisor establishes QMP observation for the admitted QEMU before permitting
guest execution. Start QEMU paused (`-S`), complete the QMP greeting/capability
handshake and process binding, durably admit the run, then issue `cont`.
Use a pathname UNIX QMP socket under a per-run root:guest 0730 RAM directory.
Verify `SO_PEERCRED` against the unreaped direct child PID and expected UID, then
verify that child has execed the authenticated QEMU binary. A pathname or
socket owner alone is insufficient. Bound parser sizes, timeouts, event count and
console/evidence output; retain raw QEMU exit directly from the supervised child.

Production stop requests issue only `system_powerdown`. Command acceptance does
not establish clean shutdown. Require a correlated QMP `SHUTDOWN` event declaring
guest-owned shutdown and the supported reason, plus normal raw QEMU exit zero,
no remaining VM/disk consumers and subsequent flush/checkpoint inspection.
This QMP predicate does not prove filesystem/application cleanliness by itself.
The shutdown profile requires a supported guest OS shutdown path; the disposable
fixture must retain its explicit sync and poweroff evidence.
A missing event, SIGTERM, QMP `quit`, host crash, lost supervisor or ambiguous
process/run identity quarantines the disk. Do not infer clean shutdown from exit
zero, current hash or console text alone. Guests must support the selected
shutdown profile; failure stays explicit and requires operator recovery.

The disposable HTTP fixture gets a dedicated validator: verify real KVM,
CPU/disk/network and its expected counter before exactly one HTTP confirmation.
The `managed-power-key-v1` fixture keeps HTTP and the VM alive after confirmation;
QMP `system_powerdown` delivers the virtual GPIO power key, and the guest syncs
before requesting poweroff. Its poweroff must satisfy the same QMP/raw-exit contract. Fixture behavior
cannot silently become a production guest shutdown assumption.

After clean exit, use the existing stopped-guest inspection and disk lock. Flush
the stopped disk, derive the proposed checkpoint, and durably publish an intent
binding exact old/new manifests, disk hash, run/evidence digest, expected registry
generation and intended update. Reuse the existing history/manifest transaction
engine through a distinct evidence-authorized wrapper. The manual checkpoint
command keeps its operator attestation; no synthetic prompt input or monkeypatch
bypasses that contract.
The [managed checkpoint contract](managed-checkpoint-design.md) specifies durable
outcome/intent/completion records, custody transfer and interrupted publication
reconciliation. Its runtime writer and ready-cohort verification remain pending.

Keep manual manifests and checkpoint histories at their existing schema 1 and
exact byte format. A changed managed disk instead produces schema 2,
`a733-guest-enrollment-managed-v2`, with `shutdown_provenance` naming the run,
boot, supported shutdown/fixture profiles and evidence hash. It never sets
`clean_shutdown_attested`. Managed history uses
`a733-managed-guest-checkpoint-v2` and preserves exact before/after bytes, including
the first schema-1-to-2 transition. Older roots reject the new schema and leave
automation off. These fields describe provenance; only the retained supervisor
evidence and durable run authority can authorize publication. Unchanged disks
retain their manifest and require a separate durable run-completion record.
The provenance hashes evidence that precedes the manifest, avoiding a digest
cycle with the transition intent that contains the proposed manifest hash.

Publish history and manifest durably, advance the p5 head, then update the STATE
registry reference and complete the intent. Regenerate units only after completed
reconciliation. On restart, resume only the exact provable recorded transition:
old manifest plus complete trusted evidence may complete its prepared transaction;
new manifest plus exact durable history may finish its pending head/registry
publication. Check every generation and digest. Missing/conflicting evidence or
unexplained disk content quarantines the guest. Preserve partial receipts.

Stopping the host requests guest shutdown early enough for bounded checkpoint
completion before STATE/p5 unmount. Timeout cannot bless the disk or retry a
mutating request automatically. The failure action is quarantine, forced cleanup and an explicit failed
lifecycle result, following the numeric protocol below. Record the precise final shutdown ordering and failure behavior in the
reviewed implementation and prove it physically.

## Concrete v1 protocol

The registry's `enabled=true` is its fixed boot policy: one automatic attempt per
host boot, no retry. Preserve the last admitted boot ID in the p5 head even after
clean completion, so repeated boot preparation cannot relaunch it. Explicit
`start` may admit a new run from ready state after completed reconciliation.
Malformed registry bytes disable all automation; a syntactically valid entry that
fails a live check quarantines that guest and leaves other entries independent.
`disable` only changes desired state through registry CAS and prevents future
launches; it requires valid registry syntax/storage but no valid guest manifest.
It neither clears quarantine nor silently stops a current run. Stop is explicit.

Heads use a distinct per-guest sequence, separate from the registry's global
generation. Store exact canonical previous/new head bytes in an immutable chain
before atomic head publication. Head states and allowed transitions are:

| From | To | Required durable evidence |
| --- | --- | --- |
| absent | ready | Explicit enrollment, independently trusted manifest and exact storage |
| ready | admitted | New run UUID, host boot UUID, old head/manifest pins and launch intent |
| admitted | running | Direct child identity, bound QMP, paused start; persist before `cont` |
| running | stopping | At most one issued stop request with command ID, or observed guest self-shutdown |
| running/stopping | checkpoint | Guest-owned SHUTDOWN plus normal raw wait status, stopped disk and exact checkpoint intent |
| checkpoint | ready | Durable checkpoint/history or unchanged-run record, head then registry reconciliation |
| any incomplete run | quarantined | Missing/uncertain evidence, timeout or conflicting metadata; retain failure record |

An admitted run without proven completion never becomes ready merely because the
host rebooted or no process remains. Quarantine recovery requires explicit
restoration from a verified matching disk/manifest/history backup, retained
restore receipt and a separately reviewed head-reset transaction. It cannot
approve unexplained current disk bytes. Manual checkpoint/enrollment/restore of
managed names refuses with lifecycle recovery guidance; the original manual
attestation contract remains available for unmanaged guests.

Run admission and immutable outcome records bind schema/profile, storage tuple,
name/UID/GID, run/boot UUIDs, previous head/manifest hashes, child PID/start ticks,
QEMU identity, exact QMP event data, command IDs and raw `waitpid` status. Only
`WIFEXITED(status)` with `WEXITSTATUS(status)==0`, and QMP `SHUTDOWN` data
`guest=true, reason=guest-shutdown`, can authorize checkpoint inspection. Preserve
unknown/out-of-order events; a matching command acknowledgement is distinct from
shutdown. Missing evidence quarantines. Source verification and physical QMP
shutdown checks remain required for the pinned QEMU version.

The checkpoint intent binds old/new canonical manifest bytes and hashes, disk
size/hash, run/outcome hash, prior head hash/sequence and intended registry pin.
On changed disks require the existing exact checkpoint history. On unchanged
disks the existing checkpoint engine creates no new history: retain a durable
unchanged-run completion record binding old==new manifest and verified unchanged
disk, then advance the head once. Reconciliation consumes an intent idempotently;
repeating it changes no head sequence, registry generation or checkpoint number.

Serialize registry updates under a separate root-only `registry.lock` and reread the latest
registry. If unrelated guests advanced the global generation, preserve all current
entries and enabled flags and compare this entry's old pin before applying only
its pending new pin. A concurrent disable must remain disabled. If this entry is
already at the recorded new pin, do not increment the generation again. Any
other same-entry conflict quarantines; never overwrite it with stale intent bytes.

### Admission and legacy callers

Controller-private `/run/a733-lifecycle/` is root:root 0700. Admission uses
`/run/a733-admission/`, root:KVM-group 0710, so the nonroot launcher can read
protected records and take advisory locks. `gate.lock` and `leases.lock` are
retained root:KVM-group 0640 single-link regular files on tmpfs, opened nofollow
with ancestor and named-inode checks. Nonroot callers open them read-only; Linux
flock permits this and the named files remain unwritable to them. `gate.json` is
an atomic root:KVM-group 0440 open/closed record with a
closure nonce and reason; missing/unsafe state refuses new managed admission.
Global acquisition order is gate EX, lease SH/EX, per-guest EX, existing subsystem
lock, disk flock, then root-only registry EX. Registry publication never acquires
a guest/disk lock while holding registry EX. Locks use nonblocking admission or
bounded explicit waiting; do not wait on a conflicting lease while holding gate
EX. Never reacquire the same lock through a nested helper.

Bootstrap is a distinct root-only transaction: hold gate EX and lease EX,
initialize/retain its own closed boot gate, validate root/STATE, activate exact p5, validate
heads, publish managed-name markers and prepare accounts. Use factored activation
and account cores with a retained typed admission-owner capability; no CLI/env
bypass or nested lease acquisition. Publish the open gate only after all required
preparation is durable/verified. Bootstrap cannot take ownership of or reopen a
failed-maintenance/shutdown closure with another nonce. Failure leaves it closed
and management available. `rename(open)` is the admission commit boundary,
after persistent readiness has already completed. A kill after that rename can
leave admission open without an acknowledgement. No caller may mutate prepared
state after calling open. Ordinary publication errors attempt to close and poison
the owner; report uncertainty if rollback cannot be proven, rather than claiming
the gate is closed or durable.
A repeated preparation in the same initialized boot with active leases performs
only a verified no-op; it cannot remount storage, recreate live identities or
admit a duplicate run. New enrollment/start uses normal controller transactions.

Gate-only closure is a special monotonic operation permitted while holding an
existing shared lease/per-guest lock: it never takes another lease, guest or disk
lock. Acquire gate EX only through bounded acquisition; any operation already
holding gate EX must take downstream locks nonblocking and release immediately
on conflict, so closure cannot form a lock-order cycle. A shutdown closure cannot
be reopened by another maintenance owner.

A normal operation holds gate EX briefly, verifies open admission, takes a shared
lease nonblocking and releases gate EX. Hold that lease through its mutation or QEMU lifetime.
Managed supervisors retain it through stop/checkpoint publication. For a managed
launch, persist the p5 admitted state while holding per-guest EX before spawning.
Manual launch also retains its lease through QEMU exec; the launcher explicitly
retains only its own validated lease FD in addition to authenticated input FDs.
Per-guest locks are also root:KVM-group 0640 under a protected 0710
`/run/a733-admission/guests/` directory. Root account/enrollment preparation
creates them once and never removes/replaces them during that boot. Every manual
launcher takes its guest lock EX nonblocking before checking managed-name markers
and holds it through QEMU exit. Root enrollment/enable/restore takes the same
lock, so publication of a head/marker cannot race a previously unmanaged launch.
Unknown/unprepared lock names fail closed. Unrelated guests remain independent.

The root supervisor passes its validated shared lease and per-guest lock FDs to
its direct child. The managed permit is root:KVM-group 0440 in the protected
admission tree and binds run/boot/manifest, child PID/start ticks, parent PID/start
ticks and both lock inode identities. Use a pre-exec barrier so the parent can
publish the exact child permit before releasing it to setpriv/launcher exec.
The permit also binds native PID/user/mount namespace device/inode identities.
Root publication verifies them against itself and PID1, including the initial
PID/user namespace IDs. The nonroot child compares its own accessible namespace
links against those protected pins: Linux denies it access to root-parent/PID1
namespace links. The root QEMU witness then rechecks actual parent/child
membership against the permit. No extra child capabilities are granted.
The nonroot launcher validates that permit against itself, inherited FDs and
current storage, retaining only those two lock FDs and its input descriptors into
QEMU. PID/start-tick checks prevent another process reusing a permit. Root holds
its copies through checkpoint completion. No QMP FD is inherited with the chosen
pathname transport. Root prepares `/run/a733-admission/qmp/` as root:KVM 0710
and its `<run UUID>/` child as root:guest 0730. QEMU creates the initially absent
`qmp.sock` there; the launcher checks retained ancestors and refuses an existing
socket or symlink. The root-private controller directory stays separate.
Boot preparation
publishes root:KVM-group 0440 managed-name markers in the admission directory
from p5 authority before opening the gate. Manual launch refuses those names;
the root supervisor supplies a narrowly bound per-run managed permit. The permit
binds its child/run/manifest and does not replace disk or QMP process checks.
If safe marker preparation fails, leave admission closed.

Maintenance closes the gate under gate EX, records its nonce, releases gate EX,
requests existing managed supervisors to stop, then waits for lease EX. Existing
lease holders can finish shutdown/checkpoint without reopening admission. Active
manual guests must be stopped explicitly; maintenance refuses rather than killing
them. No new operation can take a lease while closed. After successful maintenance
only its matching owner nonce may reopen admission; shutdown never reopens it.
A failed maintenance operation leaves a visible closed gate for explicit recovery.

Integrate this admission in accounts, activation, enrollment, manual/managed
launch, checkpoint, backup/restore, update-host, select-host-root,
recover-host-root and restore-known-host-state. Manual guest mutations and the legacy backup exporter refuse managed names;
the lifecycle backup wrapper includes heads and transition/evidence records. Factor transaction cores so authenticated outer callers retain
one lease without nested lock acquisition. Direct helpers still validate storage,
descriptors and consumers. A closed shutdown gate still permits its already
admitted supervisors to finish; no new launch is allowed when PID1 reports
stopping. This is cooperation among trusted root tools, not protection from
hostile privileged writers or older unmodified recovery media.

### Supervision, budgets and packaging

Name root supervisors `a733-managed-<name>.service`, separate from the existing
manual `a733-guest-<name>.service`. Keep the latter inactive during managed runs.
The existing stopped inspection therefore retains its manual-unit checks; the
managed wrapper additionally proves its direct child reaped and no VM/disk
consumers. Spawn one child through pinned `setpriv`, dropping to the locked UID/GID,
only KVM supplementary group, all bounding/inheritable/ambient capabilities off
and no-new-privileges, then exec the existing launcher and QEMU without changing
PID. Root retains raw wait ownership and protected metadata. The supervisor's
own capability bound must include only demonstrated needs for dropping child
credentials/capabilities, protected file/socket access, ownership, child signals
and `/proc` inspection; the child still has zero capabilities. Do not use mount
sandbox directives that move the child out of the required native mount namespace. Validate the final
credentials and executable before `cont`; failure quarantines and kills the child.

QMP greeting/command acknowledgement limits are 10 seconds each; messages 64 KiB,
4096 events and 8 MiB total per run. Console/evidence storage is bounded separately
to 8 MiB each; overflow quarantines rather than dropping acceptance evidence.
Graceful stop budget is 60 seconds, followed by 10 seconds for raw exit and
300 seconds for stopped inspection/flush/checkpoint. Timeout or failure writes
quarantine when possible, sends SIGTERM to the child, waits five seconds, then
SIGKILL and waits five seconds. Forced exit never authorizes checkpointing.
Use `KillMode=mixed`, `KillSignal=SIGTERM`, `TimeoutStopSec=420`, `SendSIGKILL=yes`:
initial systemd stop reaches the supervisor; the final timeout contains the whole
cgroup. At admission reserve host memory: enabled aggregate MemoryMax must leave at least
2 GiB of physical RAM for the host, and a new run needs MemAvailable at least its
MemoryMax plus 1 GiB. Failure declines that guest while preserving management.
Use identity-planner batches of at most 16 under the same bootstrap/maintenance
transaction when preparing a larger registry; each batch preserves other accounts.
A supervisor crash leaves admitted/running authority that quarantines on
recovery. An unkillable consumer prevents a clean unmount/pass; forced power loss
is explicitly outside clean shutdown acceptance.

The initramfs already prepares management before PID1; do not add a guest failure
to that prerequisite. Its RAM links enable `a733-guest-prepare.service`, ordered
after local-fs and STATE availability. A separate oneshot
`a733-guest-storage.service` validates/mounts p5 through the existing activation
transaction and remains active. Preparation is after storage, renders managed
units in RAM and starts them without blocking SSH/NTP. Managed units order after
preparation/storage and require the existing STATE/p5 mounts. Their stop ordering
therefore precedes mount teardown. When PID1 reports stopping, supervisor stop first closes the shutdown gate
before requesting guest shutdown. An explicit single-guest stop while PID1 is
running does not close admission globally; its retained per-guest lock prevents
a competing launch. Maintenance uses the global closure protocol.
Storage service stop verifies no remaining VM/disk consumers before normal unmount.
Document and test actual systemd mount unit dependencies generated from live mounts;
do not claim unmount safety from a service name alone.

Package services under `/usr/lib/systemd/system/`, controllers and all imported
configs/scripts in the closed `/usr/lib/a733-tools/` maintenance tree. Add their
source closure to `build/maintenance-tools.py`; add boot units/link preparation to
`build/host-payload.py` and `configs/host-management.py`. Include the new service,
controller and helper hashes in root provenance and inactive-root reproduction.

## Implementation and acceptance

Implement protected I/O/admission first, then managed supervision, checkpoint
reconciliation and boot/package integration. Include registry plus lifecycle heads
and exact transition/evidence records in coherent guest backups; keep compatibility
rules explicit for older backup formats. Test every publication/fsync boundary,
metadata/path substitution, stale registry, concurrent maintenance, unknown schema,
account collision and missing storage/KVM. Tests must also cover host rollback
after a completed checkpoint and restoration of older STATE metadata.

Independent review must cover disk admission, evidence authenticity, transitions,
shutdown order and rollback before deployment. Build into the inactive root and
retain the working slot. Physically demonstrate opt-in boot/start/clean stop,
checkpoint/restart, disable and host rollback with SSH available, followed by the
final SPI boot series. Existing endurance remains accepted; all planned tests
are capped at eight hours. Host tests cannot replace those hardware gates.

# Guest admission during host maintenance

This describes the current source candidate. The controllers are uninstalled;
bootstrap, RAM recovery and physical deployment validation remain pending under
[the production plan](spi-production-readiness-plan.md). Use the pinned released
maintenance tree for the existing installation until a new matched root is
validated. [Lifecycle design](guest-autostart-design.md) defines the protocol.

## Bootstrap preparation boundary

Initialization holds a protected root-owned RAM interlock while creating or
opening the admission runtime. It releases that interlock before returning;
the original fresh bootstrap owner keeps the separate exclusive admission lease.
Existing runtime is validated and never reset. Portable RAM recovery must join
the same initialization interlock before treating a missing runtime as safe.
The current portable candidate retains that actual interlock and, when present,
the closed admission record and exclusive lease through writes and cleanup.
It cannot adopt, reopen or reset the runtime. Final consumer census and owned
descriptor cleanup precede success. Physical deployment remains pending.

Paired raw host restoration additionally probes the exact p5 under block-level
read-only protection in a private child mount namespace. It requires supported
clean ext4 with no recovery/orphan work and refuses any `.a733-lifecycle` marker,
including a dangling symlink. It never treats an unreadable or dirty filesystem
as absence. Unknown cleanup retains block RO and reports failure. A managed
guest requires coherent restore of its matching authority and disk; deleting
the marker or replacing old STATE is not recovery.

The boot-state producer requires that original closed bootstrap owner and
physical STATE/guest-storage context. It preserves an existing canonical registry
or durably creates an empty generation-zero registry, then prepares managed
exclusions for registered and orphan lifecycle names. It rejects reserved
`firstkvm` before marker preparation, including a disabled registry entry.
Invalid heads remain unavailable observations. Preparation grants no run
permission and does not create/repair heads, open admission or start a guest.

The candidate `prepare-guest-boot.py` now composes protected policy observation,
storage activation, registry/markers, RAM accounts, same-family ready-cohort
authentication, RAM managed units and manual namespace publication under the
original bootstrap EX owner. It closes preparation sources/contexts and checks
consumers before opening admission. A repeated open runtime only verifies the
prepared record and units; it performs no mutations or launches, including when
a shared lease is active. Existing closed nonces cannot be adopted. Runtime
files must not be deleted or recreated to bypass a failed preparation.

The fixed policy is `/run/a733-state/config/guest-storage.json`, root:root 0400
on selected STATE. The reader retains descriptors and checks native root/STATE,
NVMe identity, mount placement and exact canonical bytes before initialization.
The candidate root recipes package boot services, dispatch, teardown and initial
policy publication. No new controller
is installed or enabled on the board by these source checks.
The current manual unit rendering procedure remains explicit.

The [initial policy publisher source](../scripts/configure-guest-storage.py) now
passes [separate focused review/checks](evidence/initial-policy-validation-20261112.json).
Its explicit expected identity must match
the selected installed storage. Initialization EX protects the entire publication
and cleanup; any existing runtime refuses initial setup. A different existing
policy is never overwritten. An identical policy gets file/parent durability
barriers, and cleanup uncertainty clears top-level durability while retaining
the actual publication receipt. The tool does not initialize admission, mount
storage, launch guests or start services. Use it only after runtime packaging and
the installed procedure are validated; it is not a command for the current board.

The reviewed account producer reserves identities from all protected published
manual and managed enrollments, including disabled guests, before applying
sorted account batches. It detects cross-guest conflicts before any account
write and preserves `firstkvm` as manual. Account preparation grants no launch
permission. The candidate preparer calls it before opening admission.

## Boot service and shutdown boundary

`a733-guest-storage.service` runs the entire preparation transaction in one
process. `a733-guest-prepare.service` then validates the protected prepared record,
fresh enabled configuration, unit bytes, PID1 properties and mount dependencies.
It durably publishes a create-once dispatch intent before one nonblocking start
request. Queueing never waits for units ordered after that same preparation
service. A repeated request observes the intent and does not queue again,
including after a lost acknowledgement. New or changed configuration without
matching prepared units cannot silently start through stale unit settings.

Both boot services are conditional on the explicit policy file. Missing policy
skips automation. Their RAM wants do not make management services depend on guest
success. The storage service orders after p5's mount unit without requiring it
at startup, allowing the preparer to activate p5; reverse shutdown order places
storage teardown before that mount's stop. Actual A733 PID1 ordering remains a
deployment check.

The storage service invokes only `ExecStopPost` for normal stop and failed
startup cleanup, avoiding a second automatic unmount after an uncertain first
attempt. See [systemd service semantics](https://www.freedesktop.org/software/systemd/man/252/systemd.service.html).
Teardown closes shutdown admission monotonically, requests managed stops, then
takes immediate readonly lease EX without acquiring nonce ownership or reopening.
Manual/shared or other exclusive holders cause refusal. It validates the exact
policy/GPT/UUID/mount and checks visible VM, file, raw block, mapping and process
references before one normal unmount. It never uses force or lazy unmount.

Failure observes the mount read-only and reports acknowledgement separately from
observed presence/absence. Shutdown stays closed. Cleanup must complete before
success; only native PID1 mount removal is claimed, not removal of foreign
namespace clones. Inspect failed receipts and actual state before another
explicit operation. [Software evidence](evidence/guest-service-validation-20261112.json)
retains resolved findings, native probes, private systemd graph checks and the
corrected full-suite result. No physical unmount or managed KVM result is supplied
by those checks.

## Explicit guest controls in the candidate

`manage-guests.py` is packaged in the reviewed candidate and awaits physical
deployment and hardware validation. Do not copy it into the running released
host or treat these examples as instructions to start the pending deployment.
Once the matched candidate is installed, invoke its fixed packaged entrypoint:

```sh
python3 -I -B /usr/lib/a733-tools/scripts/manage-guests.py status
python3 -I -B /usr/lib/a733-tools/scripts/manage-guests.py enroll autokvm --manifest-sha256 MANIFEST_SHA256 --fixture-profile managed-power-key-v1
python3 -I -B /usr/lib/a733-tools/scripts/manage-guests.py enable autokvm --manifest-sha256 MANIFEST_SHA256
python3 -I -B /usr/lib/a733-tools/scripts/manage-guests.py start autokvm
python3 -I -B /usr/lib/a733-tools/scripts/manage-guests.py stop autokvm
python3 -I -B /usr/lib/a733-tools/scripts/manage-guests.py disable autokvm
```

Use the independent trusted current manifest hash for the named, already
published enrollment. `enroll` here registers an existing enrollment for managed
lifecycle; it does not create a guest disk or import an archive. It initially
disables automatic launch. Fixture enrollment additionally requires its explicit
supported `--fixture-profile`; ordinary guests default to `none`. `firstkvm`
remains manually managed. Account creation belongs to the bootstrap preparation
workflow. If the assigned locked RAM account is missing, start refuses before
unit/request publication and directs the operator to reboot through preparation
or finish the supported account workflow before managed enrollment.

Start authenticates the current stopped ready cohort under actual shared
admission, guest/disk exclusion and final registry lock. It checks every enabled
guest's resource budget, publishes a new fixed RAM unit or verifies identical
bytes, and checks the loaded PID1 contract. Different existing unit bytes refuse;
regenerate them through fresh boot preparation. The request binds the current
boot, storage, manifest, ready head and unit hash. After preparation cleanup,
one nonblocking start request is queued while shared admission is retained.
The service consumes the request once under its own fresh locks before run
admission. This permits an explicit same-boot restart; automatic starts remain
limited to one attempt per boot.

An existing pending request is a verified no-op and is never queued again.
Consumed requests for an unchanged ready head refuse another attempt, including
after a lost acknowledgement. Inspect the receipt and protected lifecycle state
before recovery; do not delete RAM records to force a retry. A start receipt
acknowledges queueing, not successful VM execution or hardware acceptance.

Stop issues one nonblocking stop and observes bounded completion for up to
420 seconds, reporting progress. Failed/unknown acknowledgement, timeout or
service failure requires inspection rather than automatic repetition. Disable
changes future launch policy and does not stop an existing run; use stop when
that is intended. Status requires available storage and open shared admission;
an unavailable status reports that boundary and does not prove a guest failed.
All commands emit structured receipts and report cleanup failures.

## Installed update and selection

The candidate update and selector commands require an initialized protected RAM
admission runtime, the selected STATE, mounted guest storage and its protected
managed registry. An empty registry is valid. Preserve the normal reviewed plan
and interactive authorization before beginning maintenance.

The controller closes admission and requests managed supervisor shutdown before
attempting an exclusive lease. Existing holders can finish shutdown/checkpoint;
an active manual lease causes refusal. The controller authenticates guest
readiness and actual VM consumers before changing the target. It retains the
closure through target and subsystem-lock cleanup, then checks readiness again
before reopening. Failure leaves admission closed and reports actual write or
publication progress. It does not automatically repeat the transaction.

[Independent review and Ubuntu/private-loop checks](evidence/installed-maintenance-admission-validation-20261112.json)
cover the wiring. Earlier physical update/rollback results apply to the released
software pinned in their own receipts.

## Activate guest storage

Normal installed activation requires the prepared runtime and protected registry.
After the existing plan authorization, it compares the registry's STATE UUID,
guest UUID and NVMe serial with the reviewed target. Pre-p5 custody authenticates
selected STATE and closes admission before managed shutdown and exclusive lease
acquisition. It retains custody throughout the original guarded mount, journal
replay and permission transaction.

After all target, directory and subsystem cleanup succeeds, explicit storage
binding authenticates mounted p5 and the guest cohorts. Final checks and resource
cleanup precede overall success. An unbound operation cannot reopen admission.
The separate recurring bootstrap API still borrows its original closed owner;
normal activation cannot initialize or adopt that owner.

[Independent reviews and private-loop activation](evidence/guest-activation-admission-validation-20261112.json)
pass. Physical validation and the complete boot coordinator remain pending.
If activation fails with p5 absent, use a fresh boot or guarded recovery to restore
the required context; the installed reopen command below requires mounted guest
storage. Preserve failed receipts and avoid bypassing the closed gate.

## Recover a failed maintenance closure

After the candidate is deployed and its boot preparation has succeeded, inspect
both the failed transaction receipt and the protected current gate:

```sh
cat /run/a733-admission/gate.json
```

Inspect the transaction's actual target/selector state before choosing whether
to prepare a new transaction. Reopening admission does not repair an incomplete
inactive root or change the selected next-boot root. Keep an incomplete slot
unselected.

For an existing closed gate whose reason is `maintenance`, take its exact
`owner_nonce` and run as root:

```sh
python3 -I -B /usr/lib/a733-tools/scripts/reopen-guest-admission.py \
  --expected-nonce EXACT-RECORDED-UUID
```

The command authenticates that protected record before requesting managed stops.
It requires an exclusive lease, physical installed storage and authenticated
ready guest cohorts. A live exclusive owner, active manual lease, changed nonce,
quarantine or unresolved guest state causes refusal. It never initializes a
missing runtime or adopts a bootstrap/shutdown closure. It can reconcile an
already authenticated checkpoint intent; it cannot infer clean shutdown from a
disk hash or clear quarantine.

Success requires acknowledged reopening and completed owned-resource cleanup.
If reopening was acknowledged but cleanup failed, the command exits nonzero and
reports `success:false`, preserving `open_acknowledged`, `gate_state` and the
original transaction receipt under `maintenance`. Inspect that evidence before
another action; an error does not imply the gate stayed closed. A repeated
request against an already open gate refuses.

Keep the JSON receipt and UART output with the failed maintenance record. This
recovery command grants no authorization to rerun a host write, restore stale
STATE or change physical targets.

## Single managed run boundary

The candidate `run-managed-guest.py` is the controller for the future managed
unit. It accepts one enabled guest name, acquires retained admission/guest custody
and uses the same checkpoint, supervisor and fixture family throughout. It starts
one supervised child, accepts only supported guest-owned shutdown evidence and
publishes a checkpoint before releasing custody. Overall success also requires
completed supervisor/run/controller/lease cleanup.

SIGTERM/SIGINT set a stop flag. Before requesting shutdown the controller reads
PID1 state; when PID1 is stopping it closes the global shutdown gate, which cannot
reopen. An explicit guest stop on a running host leaves global admission open.
Healthy polling uses the flag, QMP and raw child status without repeatedly
spawning PID1 queries. A stop during bounded synchronous fixture verification is
ambiguous and fails conservatively; it does not qualify that fixture.

[Native composition and independent probes](evidence/managed-service-validation-20261112.json)
pass with physical/QEMU observations explicitly substituted. The controller
provides no automatic restart or standalone deployment acceptance. Startup units,
aggregate resource checks and actual KVM qualification remain unfinished.

## Managed backup boundary

The candidate `backup-managed-guest.py` exports one stopped ready managed guest
under an actual shared admission lease and exclusive guest/disk custody. It
requires root, an explicit name and trusted `--manifest-sha256`. Output must be
a pipe or socket; stderr carries the successful archive byte count/hash and
source manifest, registry and lifecycle pins. Every partial stream must be
discarded. Receiver acceptance also requires a successful final source receipt.

The distinct `a733-managed-guest-backup-v1` archive contains exactly
`enrollment.json`, `checkpoint-history.json`, `lifecycle.json`, `Image`,
`initramfs.cpio.gz` and `disk.raw`. Its registry snapshot includes historical
unrelated entries and enabled flags; restoration must merge only the intended
guest into current destination authority. Retain the trusted full archive hash
and source receipt off-device. Parsing an archive does not establish live
shutdown, source authority or destination admission.

Capture the complete encoded stream in a private file and retain the successful
sender receipt separately. On a backup destination with the candidate tool tree,
use that receipt's archive hash and the independently trusted manifest pin:

```sh
python3 -I -B /usr/lib/a733-tools/scripts/verify-managed-guest-backup.py \
  /ABSOLUTE/CAPTURE.tar.gz \
  --archive-sha256 TRUSTED-ARCHIVE-SHA256 \
  --manifest-sha256 TRUSTED-MANIFEST-SHA256 \
  --new-output /ABSOLUTE/NEW-ARTIFACT-DIRECTORY
```

The parent must already exist on supported backup storage. The destination must
be new. The receiver authenticates encoded bytes before decoding, checks the
historical cohort and payloads, then publishes immutable artifacts without
replacing an existing directory. Require exit zero and a successful durable
receipt. A publication observation failure reports uncertainty and retains
artifacts while closing descriptors; inspect the recorded paths before another
action. Do not infer that a failed command left the destination absent.

The local archive reader retains a root-owned 0400 archive and validates the
entire encoded file before decoding. Its caller must already hold the appropriate
admission before archive access. It supplies no destination guest authority.
[Decoder/receiver/source validation](evidence/managed-archive-validation-20261112.json)
includes actual native sender integration with physical/shutdown observations
explicitly substituted. All three remain uninstalled.

The coherent managed restore publisher and HTTPS source integration remain
unfinished. The legacy five-member verifier/restore path continues to serve
manual guests and cannot restore this six-member managed profile. The original
`firstkvm` remains manual. Validate the complete managed backup/restore workflow
with the separate disposable guest and actual KVM before production signoff.

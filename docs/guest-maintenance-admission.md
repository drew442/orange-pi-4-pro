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
the same initialization interlock before treating a missing runtime as safe;
that recovery integration remains unfinished.

The boot-state producer requires that original closed bootstrap owner and
physical STATE/guest-storage context. It preserves an existing canonical registry
or durably creates an empty generation-zero registry, then prepares managed
exclusions for registered and orphan lifecycle names. It rejects reserved
`firstkvm` before marker preparation, including a disabled registry entry.
Invalid heads remain unavailable observations. Preparation grants no run
permission and does not create/repair heads, open admission or start a guest.

The boot coordinator still needs to compose storage activation, RAM accounts,
completed-cohort authentication, unit rendering and manual namespace publication
before opening admission. [Initialization/boot-state checks](evidence/bootstrap-admission-validation-20261112.json)
cover native locks and protected files with physical/PID1 substitutions. Runtime
files must not be deleted or recreated to bypass a failed preparation.

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

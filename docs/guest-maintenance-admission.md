# Guest admission during host maintenance

This describes the current source candidate. The controllers are uninstalled;
bootstrap, RAM recovery and physical deployment validation remain pending under
[the production plan](spi-production-readiness-plan.md). Use the pinned released
maintenance tree for the existing installation until a new matched root is
validated. [Lifecycle design](guest-autostart-design.md) defines the protocol.

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

The managed receiver and coherent restore publisher remain unfinished. The
legacy five-member verifier/restore path continues to serve manual guests and
cannot restore this six-member managed profile. The original `firstkvm` remains
manual. Validate the complete managed backup/restore workflow with the separate
disposable guest and actual KVM before production signoff.

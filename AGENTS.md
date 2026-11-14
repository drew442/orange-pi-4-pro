# Repository guidelines

## Scope and starting context

Build a minimal KVM/QEMU host for **Orange Pi 4 Pro only**, with mandatory
SPI/NVMe boot and a 2 GB recovery SD. Another identical unit needs its own
identity and storage plan; no other Orange Pi model or generic A733 board is
supported. Keep the minimal host and zero routine SD writes.

Read `docs/implementation-status.md` first and `docs/beta-scope.md` for release
promises. Read only the operator/reference pages needed for the task. Historical
documents and `docs/evidence/` are reference material, not an active work queue.
The recorded hardware goal remains paused; a repository PR does not resume it.

## Finite work and validation

- Admit new pre-release work only for a promised feature or concrete serious
  defect. State the observation, smallest useful change/check, and stop condition.
  Put optional improvements in `docs/roadmap.md`; do not grow release gates.
- Use `scripts/validate-project.py` and `docs/validation-policy.md`. Run affected
  checks; consume its compact result once. Keep raw logs under ignored `out/`.
  Reuse successful checks/reviews when relevant bytes and assumptions match.
  A new commit or documentation change does not invalidate hardware evidence.
- Scripts do deterministic inventories, hashing, testing, and summaries. Do not
  poll or re-interpret successful logs with agents. On failure preserve the error,
  make one justified correction/investigation, then rerun the affected check.
- Beta uses upstream Linux/KVM/QEMU validation plus actual board integration
  evidence. No new endurance gate, upstream conformance campaign, mandatory
  second construction, or managed ten-cold/ten-warm boot campaign.
- Never claim an unbuilt image, unperformed physical check, skipped required
  stage, or unresolved background job passed. Keep full source provenance while
  using relevant dependency fingerprints for test reuse.

## Agent routing

Follow `docs/agent-routing.md`: Sol 6.1 low for routine coordination and bounded
implementation; medium for ambiguous integration and normal independent review;
high for difficult diagnosis/review. Luna handles narrow extraction; Astra is a
deliberate escalation. Use explicit models/efforts and focused fresh handoffs.
Zero or one child is normal, at most two active; no nested delegation or duplicate
coordinator work. One writer per file. Inspect effective runtime metadata when
available; role names and model self-reports are not proof of routing.

## Runtime and device invariants

The default is the `manual-beta` profile. Keep working manual guest operations,
shared admission/ownership/locking, storage preparation and teardown. Park
automatic managed dispatch; retain its source dependencies and recovery tools.
Never bypass guest manifest/checkpoint admission with an undocumented raw launch.

Independently review consequential boot, disk-selection, firmware-writing, and
rollback changes. Model strength cannot replace real KVM/boot evidence. Honor
persisted authorization for the known board's SD/NVMe/SPI and free Ubuntu-builder
capacity (`docs/evidence/storage-write-authorization-20261108.json`); revalidate
live targets and idle state. Ask again only for genuinely new targets or scope.
Respect an operator pause. Do not reopen shutdown-closed RAM admission to avoid
the required fresh boot. Preserve recovery roots, guest checkpoints, failed
cohorts and original/calibrated SPI backups. Leave heatsinks in place.

## Delivery

Use focused patches and imperative commits. PRs explain the problem, behavior,
checks, reused evidence and remaining physical gates. Follow
`docs/build-and-release.md` for packaging in this repository. Keep images,
downloads, raw logs, identities and secrets out of Git. Preserve byte-pinned
evidence paths during documentation cleanup. Keep evolving state only in the
status page; update current instructions instead of appending another saga.

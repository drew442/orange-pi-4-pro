# Deploy the opt-in managed qualification guest

Repaired candidate `4334a22` now boots on SPI/NVMe with SD removed. Initial storage
preparation and the first account/payload enrollment were executed on 2026-11-13.
Review caught a port mismatch before managed registration or launch: the fixture
serves **8080**, while the original external template requested 80. The corrected
configuration/reference below supersede those original preparation pins. Preserve
the unused original cohort and receipts; never overwrite its manifest in place.
The unused cohort is retained intact in private p5 quarantine. Corrected
enrollment and registration now pass. The first start was refused before
queueing on systemd dependency checks in the previous `2996365` root. The repaired
root passes reviewed deployment and full runtime authentication; supported enable
and explicit start now queue successfully. Actual managed execution/checkpoint
acceptance is in progress. **Do not re-enroll this existing board cohort.** The
preparation/enrollment commands below describe a fresh deployment.
[Correction record](evidence/managed-fixture-port-correction-20261113.json).
[Current physical evidence](evidence/post-reinstall-SPI-managed-B-20261113.json).

The matched B marker is `e623b575f0d10b47ed8716b0fbe365b2bc764ecb18bd4cc083382432429936fe`.
The expected NVMe is SPCC M.2 PCIe SSD, serial `134807190FC700149277`,
256060514304 bytes; STATE UUID is `360dfb39-f49f-487e-b307-0bb887c854c5`,
guest p5 UUID `298b281a-d10e-44eb-bdb9-285e27eaafad`. Revalidate live identity;
these constants alone do not establish a safe mounted destination. Initially,
policy and admission runtime must be absent. Existing `autokvm` data or an
incomplete transaction requires inspection, not replacement or another enrollment.

## Prepared inputs

The two [fixture builds](evidence/managed-fixture-build-20261112.json) match.
The original three payload seeds and account input are retained on codex under
`out/managed-fixture-deployment-20261112-01/`. It was extracted from the pinned
8453817-byte archive SHA `ad271ab152a2307f763a2f9e565625111416fc29892a9227dfa10813a588a082`;
the original archive is also retained on the builder. Source templates are
[guest configuration](../configs/autokvm-qualification.json) and
[account configuration](../configs/autokvm-qualification-accounts.json).
They request locked UID/GID 60002, 256 MiB guest RAM, a 512 MiB service limit,
one vCPU and isolated networking with host TCP 18081 to guest 8080. Account and
resource planners must verify availability; no free-UID claim is made here.
`firstkvm` remains UID/GID 60001 and is not enrolled for automation.

| Input | Bytes | SHA-256 |
| --- | ---: | --- |
| `Image` | 5253128 | `29ba9385454cfbde890cabd24ced727838658d939368ab52dfbf6d770433e959` |
| `initramfs.cpio.gz` | 975144 | `315f6ff4398e477bf1e040cca3c60483dca88c68e99a9a3cda0ecd545b5d0da3` |
| `guest-disk.raw` | 134217728 | `ff595fac0b9a7d990f099edc0dd0f9d4859fb9942e185d2f2377088ce32bd577` |
| `autokvm-qualification-port8080.json` | 483 | `fda05f86d9e8aa5bd73b6fcb2e7a9ee815878c8cd2a6ea3f3082d9407d722527` |
| `autokvm-qualification-accounts.json` | 67 | `dc507e2bdb1cb61adbbb38da72fc863d49addc92d77b4ff1cf9faddcd6d024a0` |

For a fresh deployment, copy these five inputs after the live guard. Use the
corrected repository template for the renamed port8080 config; the original
archive contains the obsolete port80 config. Use a new root-owned 0700
NVMe STATE directory `/run/a733-state/qualification/autokvm-20261112-01`, as
single-link root:root 0400 files. Keep the seed unchanged; enrollment creates its
separate mutable p5 disk. Retain live-guard and copying receipts for every
installation; this guide does not provide an unguarded copy command. The commands
below assume that guarded preparation has completed. Existing output paths
refuse reuse. Preserve partial outputs on failure and inspect actual state before
another operation; never delete lifecycle metadata to force a retry.

## Publish policy and prepare runtime

Run in the reviewed native B root Bash session. Check every exit and receipt;
stop dependent work on a failure or uncertain result. `set -C` prevents accidental
receipt replacement. No storage formatting is part of this sequence.

```bash
set -euo pipefail
set -C
umask 077
A733_TOOLS=/usr/lib/a733-tools/scripts
A733_PREP=/run/a733-state/qualification/autokvm-20261112-01
python3 -I -B "$A733_TOOLS/configure-guest-storage.py" \
  --target /dev/nvme0n1 --expected-model 'SPCC M.2 PCIe SSD' \
  --expected-serial 134807190FC700149277 --expected-bytes 256060514304 \
  --state-uuid 360dfb39-f49f-487e-b307-0bb887c854c5 \
  --guest-uuid 298b281a-d10e-44eb-bdb9-285e27eaafad \
  > "$A733_PREP/policy-receipt.json"
systemctl start a733-guest-prepare.service
systemctl show a733-guest-storage.service a733-guest-prepare.service \
  -p LoadState -p ActiveState -p SubState -p Result -p MainPID -p ExecMainStatus
```

Retain journals and both successful unit outcomes. Initial preparation publishes
an empty managed registry and opens normal admission; it does not create an
account for the unpublished `autokvm` guest. Policy publication must precede this
first preparation. Exact mount and shutdown ordering remain physical gates.

## Prepare accounts, then enroll payload

Use the supported prepare/apply interfaces, in that order. Protect planner output
before applying it; stale plans and identity collisions refuse. These commands
operate on the named disposable guest and preserve `firstkvm`.

```bash
python3 -I -B "$A733_TOOLS/guest-accounts.py" prepare \
  "$A733_PREP/autokvm-qualification-accounts.json" > "$A733_PREP/accounts-plan.json"
chmod 0400 "$A733_PREP/accounts-plan.json"
python3 -I -B "$A733_TOOLS/guest-accounts.py" apply \
  "$A733_PREP/autokvm-qualification-accounts.json" \
  --plan "$A733_PREP/accounts-plan.json" > "$A733_PREP/accounts-result.json"
enrollment=(--config "$A733_PREP/autokvm-qualification-port8080.json" \
  --kernel "$A733_PREP/Image" --initrd "$A733_PREP/initramfs.cpio.gz" \
  --disk "$A733_PREP/guest-disk.raw" \
  --guest-uuid 298b281a-d10e-44eb-bdb9-285e27eaafad)
python3 -I -B "$A733_TOOLS/enroll-guest.py" prepare "${enrollment[@]}" \
  > "$A733_PREP/enrollment-plan.json"
chmod 0400 "$A733_PREP/enrollment-plan.json"
python3 -I -B "$A733_TOOLS/enroll-guest.py" apply "${enrollment[@]}" \
  --plan "$A733_PREP/enrollment-plan.json" > "$A733_PREP/enrollment-result.json"
```

The expected **initial** 1024-byte enrollment manifest SHA is
`a46ae94c58289808f3b8f9848a9762ce68cd0fa6b1d28383821b6199135d8701`.
It was constructed offline from the validated configuration, exact seed records,
fixed p5 UUID and the installed manifest schema. Compare the successful enrollment
receipt and actual published manifest with this independent reference before
registration. The reference is not a file to install over guest metadata.
Changed inputs/UIDs require new reviewed references. The pin changes after a real
checkpoint; never reuse the initial pin for a later checkpointed cohort.

## Opt in and prove real operation

After the preceding comparison, register disabled with the explicit fixture
profile, then enable. These are separate operations; registration does not
create the guest payload or start it.

```bash
A733_INITIAL_MANIFEST=a46ae94c58289808f3b8f9848a9762ce68cd0fa6b1d28383821b6199135d8701
python3 -I -B "$A733_TOOLS/manage-guests.py" enroll autokvm \
  --manifest-sha256 "$A733_INITIAL_MANIFEST" --fixture-profile managed-power-key-v1
python3 -I -B "$A733_TOOLS/manage-guests.py" enable autokvm \
  --manifest-sha256 "$A733_INITIAL_MANIFEST"
python3 -I -B "$A733_TOOLS/manage-guests.py" start autokvm
```

Retain actual nonroot KVM CPU/disk/network, guest-owned clean shutdown and durable
checkpoint evidence. After a separately guarded normal host reboot, prove
unattended start and the next persisted counter. The external qualification helper
requires a completed **automatic current-boot** run; an explicit start is not that
proof. Do not launch a second confirmation request or retry an uncertain start.

Continue with coherent backup/restore, disable, installed maintenance interruption/
retry, old-A rollback and managed-B return before final SPI qualification and
independent procedure reproduction. [Controls](guest-maintenance-admission.md),
[restoration](managed-guest-restore.md), [qualification](managed-boot-qualification.md)
and [boot collector](spi-boot-qualification.md) define those boundaries. Preserve
original backups and full SPI boundary snapshots. Reuse the accepted endurance
result; no new endurance run is required.

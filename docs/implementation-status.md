# Implementation status

Updated 2026-11-11. **Release acceptance and independent physical deployment
reproduction pass for the recorded A733 using the immutable SD fallback.**
Actual KVM, the eight-hour workload, ten warm reboots, ten physical cold boots,
complete SD comparisons, update/rollback and controlled recovery all pass.
The earlier November 8 SSH failure remains unexplained. See the
[release checklist](release-acceptance.md) for the accepted scope and limitations.

## Current SPI objective

The operator requested operation with SD removed and authorized SPI programming.
A manual U-Boot NVMe-file boot now passes on the installed normalized host. The
registered recovery policy uses documented ROM SD priority and physically tested
SD recovery. The guarded programmer is under independent review and full-suite
validation before the first physical SPI write.
[Procedure and evidence](spi-sd-boot-20261111.md).

## Live board and constraints

- **Current:** normalized A at `root@192.168.1.116`, original pinned identity,
  boot `a55b0c1c-91fc-4fee-80b7-3a963a20d72e` after manual NVMe-file boot. Read-only live release guards
  pass after the ten cold cycles and final SD comparison. Root p2 is read-only;
  original configuration/keys, DHCP/NTP and resolver policy pass. Fresh full
  installation completed with a streamed source hash and optional readback off.
  The restored counter-5 guest ran real nonroot KVM through 6, clean shutdown,
  checkpoint and restart into 7. Last checkpoint **2**, manifest `768a0138…759fb`,
  has six verified history records. Guest is stopped. Recreate RAM accounts/units
  after host reboot using the [guest operations guide](guest-operations.md).
  [Installation](evidence/normalized-host-install-20261110.json),
  [guest proof](evidence/normalized-guest-restore-20261110.json),
  [reproduced deployment](independent-deployment-20261110.md),
  [cold boot/SD proof](evidence/final-cold-boots-20261111.json).
- **Target:** A733, observed 12 GiB/eight CPUs. SD is SU02G,
  **1977614336 bytes**, CID `035344535530324780716f216e00a8f5`.
  NVMe is SPCC M.2 PCIe SSD, **256060514304 bytes**, serial
  `134807190FC700149277`, firmware EDFM20.0, 512/512 sectors. Diagnostic inventory
  is sufficient; leave heatsinks attached. Reported supply is 67 W USB-PD with
  passive 10.4 mm six-layer whole-board copper cooling; negotiated power is unmeasured.
- **Boot route:** protected normal-host SD prefix `ab5e8c…ad207` restored;
  serial shows SD Linux-file loading. NVMe BOOT `cc2ad9…26c313` was included in
  the installation; firmware loading it remains untested. SPI is unchanged.
  Linux SD write/discard/flush fields remain zero after the fresh host boot.
  Explicit maintenance writes are separate from routine observations.
  [Current images](current-images.md).
- **Serial:** bridge `orangepi@192.168.1.117`, `ttyS3-capture.service`, volatile
  `/var/log/ttyS3.log`; native relay `a733-uart-relay-20261109-01.service` retains
  output off-board. UART help, RAM shell and controlled SysRq recovery pass;
  a fully locked kernel and hardware reset are outside that proof. AP-RESET is
  not exposed on the reviewed 40-pin header; no wiring was requested.
  [Procedure](remote-serial-control.md).
- Known board SD/NVMe/SPI writes remain authorized. Revalidate exact targets and
  retain independent review; ask yes/no only for unapproved targets or scope.
  Prefer SPI-to-NVMe with SD removed, otherwise complete immutable-SD proof.
  The registered source-backed SD recovery policy replaces the earlier
  USB-FEL-specific prerequisite. SPI programming remains pending concrete review
  and completed validation. [Programmer](known-spi-programmer.md).

## Acceptance evidence

| Gate | Current evidence |
| --- | --- |
| Diagnostic fits actual 2 GB SD; all eight CPUs at EL2 | Passed; oversized vendor images were not used |
| Actual nonroot KVM CPU/disk/network, poweroff/checkpoint/restart | [Initial passes](kvm-vgic-results-20261108.md), [restored host counter 6](paired-host-restore-20261110.md), and normalized B/rolled-back A counters 7/8 with retained zero exits |
| Management and identity | Installed DHCP, NTP, pinned SSH and both authorized keys pass; any-subnet configuration retained through update/rollback |
| Eight-hour workload | [Passed from retained >20-hour data](evidence/eight-hour-endurance-result-20261109.json); 2423 samples reach 72669 guest seconds/cycle 2422, with temperatures/memory/disk/network/KVM and post-run health/SD checks. Original 86400-second metadata remains historical. Operator stop is not guest-generated ENDURANCE_PASS or clean guest poweroff. Old timer is stopped/disabled; no rerun |
| Original host backup/restoration | [Immutable offline cohort](evidence/offline-host-preservation-20261110.json) and [physical interrupted restore/full retry/boot/KVM](paired-host-restore-20261110.md) pass |
| Changed inactive update and manual rollback | [Physical p3 interruption, A recovery boot, complete B retry, selector interruption before/after publication, cancellation, B boot/KVM and old-A rollback/KVM pass](physical-host-update-rollback-20261110.md); handled SIGTERM, not power cuts |
| Guest backup/restoration | [Counter-3 archive restored and advanced 4/5](evidence/corrected-host-guest-restore-20261108.json). [Counter-5 archive](evidence/current-counter5-backup-20261109.json) and [forensic copies](guest-preservation-20261109.md) remain preserved; [fresh archive restoration and actual KVM 5→6→7/checkpoint/restart now pass](evidence/normalized-guest-restore-20261110.json) |
| Failed health/offline root selection, missing NVMe, dirty journal | [Controlled SSH/offline-root recovery/KVM 9 passes](controlled-ssh-recovery-20261110.md). [NVMe-driver fault → serial shell → ordinary B → KVM 10 passes](missing-nvme-recovery-test.md); physical disconnection is not claimed. [Actual dirty crash → protected export → ext4 replay/marker → KVM 11 passes](dirty-state-recovery-trial.md), with verified custody. Initial raw inspector counter refusal remains retained; no broader claim |
| Fresh normalized full installation and deployment reproduction | [Matching full streams](evidence/normalized-full-nvme-image-20261109.json) and [isolated composition](evidence/isolated-nvme-composition-20261109.json) pass. B alone has booted through the updater; [full installation](evidence/normalized-host-install-20261110.json), [fresh restored KVM](evidence/normalized-guest-restore-20261110.json) and [independent procedure reproduction](independent-deployment-20261110.md) pass |
| Final 10 warm and 10 cold boots | [Ten guarded warm reboots](evidence/final-warm-boots-20261110.json) and [ten operator-performed cold boots](evidence/final-cold-boots-20261111.json) pass independent serial/device/release review. Each cold cycle has clean UART powerdown, physical USB-C removal attestation and a distinct fully ready boot |
| SPI removed-SD boot or immutable SD | Immutable-SD fallback passes. [Final cold-series comparison](evidence/final-cold-boots-20261111.json) matches complete-card SHA `401df0…64be5` and prefix `ab5e8c…ad207`, with Linux write/discard/flush fields zero. [Firmware audit](sd-firmware-write-policy.md) passes within its stated limits. [Cold metadata custody](evidence/final-cold-custody-20261111.json) is verified off-device. SPI remains unqualified |

The original host cohort and counter-5/forensic archives remain private and
immutable on the Ubuntu builder. Later counter 6–11 changes are disposable
functional-test data with retained evidence/checkpoints. The fresh normalized installation has returned from RAM to the host. Completed cases have private raw receipts in their linked `out/` directories
and verified second copies on the builder.
Do not restore old STATE alone over a different root pair.

[Controlled recovery details](controlled-ssh-recovery-20261110.md) preserve the
UART parsing anomaly, ext4 warning, evidence-path limitation and exact scope.
The earlier uncontrolled SSH failure remains unexplained.

## Findings and release inputs

The prior old A exposed wildcard 5355 because its resolver directory was 0700.
The normalized fix now passes physically on **both B and fresh A**: directory
0755, global LLMNR/mDNS disabled and no 5355/5353 listeners. Root password stays
locked and SSH key-only. [Prior policy/update result](physical-host-update-rollback-20261110.md).
The final host passes release/device guards after warm and cold qualification.
The first cold invocation used a stale UUID and refused before poweroff; the
[cold guide](cold-boot-validation.md) now reads the current UUID at launch.
Installer RAM serial retained three CMA allocation failures whose cause remains
unexplained. The successful install/flush/hash receipts and completed hardware
qualification are retained with that limitation.

Legacy serial showed orphan cleanup despite `ro,noload`. Protected bootstrap
now enforces block RO before STATE probing; real ext4 software fixtures and
physical protected boot pass. [Actual dirty-journal recovery](dirty-state-recovery-trial.md)
now passes with original dirty-source custody, protected exporter counter equality,
kernel replay, exact marker and KVM. The raw debugfs inspector refused three
zero-sector flushes; its refusal and source-backed interpretation are retained.
[Protection](evidence/state-block-protection-20261109.json).

Before reinstall, dirty-trial recovery SMART showed critical warnings/media errors 0, unsafe shutdowns 80
and admin errors 162. Prior invalid-field entries had baseline status/parameter;
the exact causes of increments are not proven. That older return console also reported
an original persistent journal corrupted/unclean and renamed/replaced; byte export
does not prove application-journal integrity or identify its cause.

The selected full distribution is `out/normalized-nvme-candidate-20261109/`:
545581074-byte gzip SHA-256
`3bc02f46d272dcac591d435fff2b1bc98d4fb048569fb2120746145929a8ad64`,
manifest `3cbe28…ad08f`. It is a factory distribution requiring the reviewed
same-board identity helper. The full installation, original identity preservation and first protected-SD
normalized A boot and restored guest KVM/checkpoint/restart now pass.
[Deployment guide](deploy-protected-20261109.md). Preserve the compatible kernel,
DTB and firmware; root provenance's old diagnostic DTB is not the current boot DTB.
Licensing/source inventory remains incomplete for 26 vendor boot inputs and two
exact Linaro source revisions; nearby GNU releases are not substitutes. No public
binary redistribution clearance is claimed. [Source inventory](toolchain-sources.md).

## Resources and next actions

Latest privileged Ubuntu 22.04 suite: **2304 tests**, zero skips/errors/failures,
five shell suites and five diagnostic cases; all five TOMLs parse.
[Cold-collector suite evidence](evidence/cold-boot-collector-validation-20261110.json)
records the latest run. The final-A warm collector authenticates release/root/policy and stops on
actual remote release refusal; [software evidence](evidence/final-warm-binding-validation-20261110.json)
retains independent review and the initial corrected validation-environment failure. Host checks do not prove
hardware acceptance.

At final qualification, codex has more than 24 GB available. The last full-suite
builder sample has 40938356736 bytes available and MemAvailable 7376642048 bytes.
Keep bulk sources, images and backups on the builder. Its LV is 128819658752 bytes
with 4265607168 bytes VG free. All free builder capacity is approved for use and
expansion after exact-target revalidation and independent review. Native codex
LVM is outside that approval. Retain resource guards and begin compilation with
two jobs. Installation uses one progress-enabled streamed write with optional
troubleshooting readback. The eight-hour workload remains accepted from existing
data and requires no rerun.

## Operation after acceptance

Use [current images](current-images.md), the [reproduced installation guide](deploy-protected-20261109.md),
[guest operations](guest-operations.md), [maintenance/rollback](normalized-maintenance-20261109.md)
and [backup/recovery](host-state-backup.md). Preserve original private host,
guest and forensic archives off-device. Qualifying firmware, kernel/DTB and
storage identities are recorded in the evidence. Changes to that combination
need appropriate validation before extending the acceptance claim.

SPI recovery/programming and SD-removed boot remain prospective work. The
qualified protected-SD fallback satisfies this release. Keep source/license
inventory limits explicit. The [completed release audit](evidence/release-acceptance-20261111.json)
and [construction index](build-reproduction-status.md) identify the evidence and
precise reproduction scope.

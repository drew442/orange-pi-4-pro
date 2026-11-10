# Implementation status

Updated 2026-11-10. Release acceptance and reproduced final deployment remain
incomplete. Actual KVM and the revised eight-hour workload gate pass; no new
endurance run is required. The earlier November 8 SSH failure remains unexplained
and is not reclassified as a pass.

## Live board and constraints

- **Current:** `root@192.168.1.116`, original pinned SSH identity, boot
  `487a3021-fb8f-4300-924f-31d17aed18b9`. Original root A is read-only on p2;
  STATE is RW after actual journal replay; GUESTS p5 is mounted. The dirty STATE
  image remains immutable off-device. [Physical crash/recovery result](dirty-state-recovery-trial.md#november-10-physical-result).
  Guest counter **11** is stopped and durably checkpointed, enrollment SHA-256
  `3193cb24133742bccc3662778d60637e60887a8b8ba8cb31c9b3becbe6483f7d`;
  its inactive RAM unit matches the new disk pin.
- **Target:** A733, observed 12 GiB/eight CPUs. SD is SU02G,
  **1977614336 bytes**, CID `035344535530324780716f216e00a8f5`.
  NVMe is SPCC M.2 PCIe SSD, **256060514304 bytes**, serial
  `134807190FC700149277`, firmware EDFM20.0, 512/512 sectors. Diagnostic inventory
  is sufficient; leave heatsinks attached. Reported supply is 67 W USB-PD with
  passive 10.4 mm six-layer whole-board copper cooling; negotiated power is unmeasured.
- **Boot route:** restored protected normal-host SD prefix `ab5e8c…ad207`; NVMe BOOT
  `cc2ad9…26c313` is retained but firmware loading it is untested. SPI is unchanged.
  Explicit maintenance SD writes are separated from routine-write observations.
  No normal installation readback was requested. [Current images](current-images.md).
- **Serial:** bridge `orangepi@192.168.1.117`, `ttyS3-capture.service`, volatile
  `/var/log/ttyS3.log`; native relay `a733-uart-relay-20261109-01.service` retains
  output off-board. UART help, RAM shell and controlled SysRq recovery pass;
  a fully locked kernel and hardware reset are outside that proof. AP-RESET is
  not exposed on the reviewed 40-pin header; no wiring was requested.
  [Procedure](remote-serial-control.md).
- Known board SD/NVMe/SPI writes remain authorized. Revalidate exact targets and
  retain independent review; ask yes/no only for unapproved targets or scope.
  Prefer SPI-to-NVMe with SD removed, otherwise complete immutable-SD proof.
  SPI programming still refuses before device open while independent recovery
  proof is unregistered. [Programmer](known-spi-programmer.md).

## Acceptance evidence

| Gate | Current evidence |
| --- | --- |
| Diagnostic fits actual 2 GB SD; all eight CPUs at EL2 | Passed; oversized vendor images were not used |
| Actual nonroot KVM CPU/disk/network, poweroff/checkpoint/restart | [Initial passes](kvm-vgic-results-20261108.md), [restored host counter 6](paired-host-restore-20261110.md), and normalized B/rolled-back A counters 7/8 with retained zero exits |
| Management and identity | Installed DHCP, NTP, pinned SSH and both authorized keys pass; any-subnet configuration retained through update/rollback |
| Eight-hour workload | [Passed from retained >20-hour data](evidence/eight-hour-endurance-result-20261109.json); 2423 samples reach 72669 guest seconds/cycle 2422, with temperatures/memory/disk/network/KVM and post-run health/SD checks. Original 86400-second metadata remains historical. Operator stop is not guest-generated ENDURANCE_PASS or clean guest poweroff. Old timer is stopped/disabled; no rerun |
| Original host backup/restoration | [Immutable offline cohort](evidence/offline-host-preservation-20261110.json) and [physical interrupted restore/full retry/boot/KVM](paired-host-restore-20261110.md) pass |
| Changed inactive update and manual rollback | [Physical p3 interruption, A recovery boot, complete B retry, selector interruption before/after publication, cancellation, B boot/KVM and old-A rollback/KVM pass](physical-host-update-rollback-20261110.md); handled SIGTERM, not power cuts |
| Guest backup/restoration | [Counter-3 archive restored and advanced 4/5](evidence/corrected-host-guest-restore-20261108.json). [Current preserved counter-5 archive](evidence/current-counter5-backup-20261109.json) and [three forensic directories](guest-preservation-20261109.md) verified off-device; new counter-5 archive restore remains pending |
| Failed health/offline root selection, missing NVMe, dirty journal | [Controlled SSH/offline-root recovery/KVM 9 passes](controlled-ssh-recovery-20261110.md). [NVMe-driver fault → serial shell → ordinary B → KVM 10 passes](missing-nvme-recovery-test.md); physical disconnection is not claimed. [Actual dirty crash → protected export → ext4 replay/marker → KVM 11 passes](dirty-state-recovery-trial.md), with verified custody. Initial raw inspector counter refusal remains retained; no broader claim |
| Fresh normalized full installation and deployment reproduction | [Matching full streams](evidence/normalized-full-nvme-image-20261109.json) and [isolated composition](evidence/isolated-nvme-composition-20261109.json) pass. B alone has booted through the updater; final full installation/reproduction remains unperformed |
| Final 10 warm and 10 cold boots | Pending after selecting/installing final distribution and boot route; test/accidental reboots are not counted |
| SPI removed-SD boot or immutable SD | SPI recovery/programming/SD-removed boot pending. Qualifying workload SD counters and pre-maintenance whole-card hash matched; [bounded firmware audit](sd-firmware-write-policy.md) passes. Final shutdown/reboot comparison and serial reconciliation remain pending |

The original host cohort and counter-5/forensic archives remain private and
immutable on the Ubuntu builder. Later counter 6–11 changes are disposable
functional-test data with retained evidence/checkpoints. RAM recovery boots have
ended. Completed cases have private raw receipts in their linked `out/` directories
and verified second copies on the builder.
Do not restore old STATE alone over a different root pair.

[Controlled recovery details](controlled-ssh-recovery-20261110.md) preserve the
UART parsing anomaly, ext4 warning, evidence-path limitation and exact scope.
The earlier uncontrolled SSH failure remains unexplained.

## Findings and release inputs

Current old A exposes wildcard 5355 listeners: its root management code creates
`/etc/systemd/resolved.conf.d` as 0700, preventing the resolver from reading policy.
The normalized directory-mode fix **passed physically on B**: directory 0755,
global LLMNR/mDNS disabled and no 5355/5353 listeners. That B slot was deliberately
replaced by the original compatible cohort for the dirty-journal trial; its
canonical normalized source remains preserved. New normalized A is not yet
booted. Root password remains locked, SSH key-only, and observed QEMU identities
have empty capabilities and NoNewPrivileges. [Policy and update result](physical-host-update-rollback-20261110.md).

Legacy serial showed orphan cleanup despite `ro,noload`. Protected bootstrap
now enforces block RO before STATE probing; real ext4 software fixtures and
physical protected boot pass. [Actual dirty-journal recovery](dirty-state-recovery-trial.md)
now passes with original dirty-source custody, protected exporter counter equality,
kernel replay, exact marker and KVM. The raw debugfs inspector refused three
zero-sector flushes; its refusal and source-backed interpretation are retained.
[Protection](evidence/state-block-protection-20261109.json).

Post-recovery SMART shows critical warnings/media errors 0, unsafe shutdowns 80
and admin errors 162. Prior invalid-field entries had baseline status/parameter;
the exact causes of increments are not proven. The return console also reports
an original persistent journal corrupted/unclean and renamed/replaced; byte export
does not prove application-journal integrity or identify its cause.

The selected full distribution is `out/normalized-nvme-candidate-20261109/`:
545581074-byte gzip SHA-256
`3bc02f46d272dcac591d435fff2b1bc98d4fb048569fb2120746145929a8ad64`,
manifest `3cbe28…ad08f`. It is a factory distribution requiring the reviewed
same-board identity helper. The drive currently holds the restored original
cohort after the dirty-journal trial.
[Deployment guide](deploy-protected-20261109.md). Preserve the compatible kernel,
DTB and firmware; root provenance's old diagnostic DTB is not the current boot DTB.
Licensing/source inventory remains incomplete for 26 vendor boot inputs and two
exact Linaro source revisions; nearby GNU releases are not substitutes. No public
binary redistribution clearance is claimed. [Source inventory](toolchain-sources.md).

## Resources and next actions

Latest privileged Ubuntu 22.04 suite: **2274 tests**, zero skips/errors/failures,
five shell suites and five diagnostic cases; all five TOMLs parse. Production
controllers were unchanged during the physical cases. Host checks do not prove
hardware acceptance.

Latest sampled available space: codex **372191232 bytes**, above the 256 MiB floor;
builder **41076981760 bytes**, MemAvailable **7537065984 bytes**. Keep bulk sources,
raw images and backups on the builder. Builder LV is 128819658752 bytes with
4265607168 bytes VG free; all its free capacity is approved for use/expansion.
Native codex LVM is outside that approval. Recheck exact targets/resources and
retain independent review; start compilation with two jobs. Build admission and
runtime disk/memory guards remain unchanged. Normal installation uses one streamed
write with progress and optional troubleshooting readback; do not benchmark hardware
or add another eight-hour run solely for the revised duration policy.

1. Retain completed controlled SSH, NVMe-driver fault and actual dirty-journal
   recovery/KVM cases without repeating them. Preserve kernel/control evidence
   if the earlier unexplained SSH failure recurs.
2. Prove SPI recovery and matched NVMe-first firmware operation where possible;
   otherwise finish immutable-SD acceptance. Keep the SPI proof gate intact.
3. Reproduce fresh final normalized installation, personalize the same-board identity,
   restore the newly preserved guest archive and verify actual KVM and final policy.
4. Bind the prepared [warm collector](warm-boot-validation.md) to the final root/image/
   boot route, then collect ten warm and ten genuine cold boots plus final SD/hash/
   firmware reconciliation. Its authenticated operator-stop evidence admission passes;
   missing or failed captures and active workloads are still refused.
5. Complete the [plan's release audit](hypervisor-plan.md#release-acceptance-and-documentation)
   and documentation/source inventory. Finish only when acceptance and reproduced
   deployment are evidenced. Historical build details remain in
   [build reproduction status](build-reproduction-status.md).

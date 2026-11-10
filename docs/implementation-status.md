# Implementation status

Updated 2026-11-10. Release acceptance and reproduced final deployment remain
incomplete. Actual KVM and the revised eight-hour workload gate pass; no new
endurance run is required. The earlier November 8 SSH failure remains unexplained
and is not reclassified as a pass.

## Live board and constraints

- **Current:** normalized A at `root@192.168.1.116`, original pinned identity,
  boot `d9e94d54-79bb-49c3-ad72-2672a9c71c6a`. Whole-NVMe installation completed
  with exit 0, expected streamed source hash and **no readback**. Original identity
  restoration/fsync/unmount and protected SD restoration pass. Independent operator
  and coordinator checks authenticate 40 tool files, root p2 read-only, STATE,
  original configuration/keys, DHCP/NTP and corrected resolver policy. GUESTS
  activation and preserved counter-5 restoration are being prepared; no guest
  has run on this fresh installation yet. [Physical installation record](evidence/normalized-host-install-20261110.json).
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
| Fresh normalized full installation and deployment reproduction | [Matching full streams](evidence/normalized-full-nvme-image-20261109.json) and [isolated composition](evidence/isolated-nvme-composition-20261109.json) pass. B alone has booted through the updater; independent operator's full installation and first normalized A boot pass; guest restoration/KVM and complete procedure reproduction remain pending |
| Final 10 warm and 10 cold boots | Pending after selecting/installing final distribution and boot route; test/accidental reboots are not counted |
| SPI removed-SD boot or immutable SD | SPI recovery/programming/SD-removed boot pending. Qualifying workload SD counters and pre-maintenance whole-card hash matched; [bounded firmware audit](sd-firmware-write-policy.md) passes. Final shutdown/reboot comparison and serial reconciliation remain pending |

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
The fresh host also passes the final-A collector's **read-only** live preflight;
no measured warm/cold cycles have run yet. Installer RAM serial retained three
CMA allocation failures; successful install/flush/hash receipts do not explain
their cause or establish a kernel-clean run.

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
normalized A boot now pass; fresh guest validation remains pending.
[Deployment guide](deploy-protected-20261109.md). Preserve the compatible kernel,
DTB and firmware; root provenance's old diagnostic DTB is not the current boot DTB.
Licensing/source inventory remains incomplete for 26 vendor boot inputs and two
exact Linaro source revisions; nearby GNU releases are not substitutes. No public
binary redistribution clearance is claimed. [Source inventory](toolchain-sources.md).

## Resources and next actions

Latest privileged Ubuntu 22.04 suite: **2281 tests**, zero skips/errors/failures,
five shell suites and five diagnostic cases; all five TOMLs parse. The final-A warm collector now authenticates release/root/policy and stops on
actual remote release refusal; [software evidence](evidence/final-warm-binding-validation-20261110.json)
retains independent review and the initial corrected validation-environment failure. Host checks do not prove
hardware acceptance.

Latest sampled available space: codex **345886720 bytes**, above the 256 MiB floor;
builder **41013968896 bytes**, MemAvailable **7486029824 bytes**. Keep bulk sources,
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
3. Retain the independently operated fresh installation and first A boot. Restore
   the preserved counter-5 archive, verify actual KVM/checkpoint/restart and complete
   the independent deployment report.
4. Bind the prepared [warm collector](warm-boot-validation.md) to the final root/image/
   boot route, then collect ten warm and ten genuine cold boots plus final SD/hash/
   firmware reconciliation. Its authenticated operator-stop admission and final-A release binding pass software review;
   missing or failed captures and active workloads are still refused.
5. Complete the [plan's release audit](hypervisor-plan.md#release-acceptance-and-documentation)
   and documentation/source inventory. Finish only when acceptance and reproduced
   deployment are evidenced. Historical build details remain in
   [build reproduction status](build-reproduction-status.md).

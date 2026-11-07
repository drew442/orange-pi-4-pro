# Implementation status

Updated 2026-11-07. Decisions and next actions are below; detailed build evidence
is indexed in [build reproduction status](build-reproduction-status.md).

## Current milestone

The [November 7 physical logs](evidence/installed-host-first-boot-20261107.json)
confirm successful installation/full readback on the exact NVMe and installed
root A reaching systemd. All eight CPUs enter EL2 and KVM initializes. Ethernet
links at 1 Gbps, but DHCP/SSH fails and the normal serial root account is locked.
**Actual SSH and KVM guest execution remain unproven.**

The deployed machine-id is `0600`, blocking unprivileged networkd's default DHCP
identity generation. The actual pinned ARM library reproduces this permission
failure. [Reviewed source fixes](evidence/installed-host-fix-20261107.json) seed/upgrade it
to `0644`; keys and configuration
remain private. Use the [existing-install repair](installed-host-repair-20261107.md),
which changes one NVMe file's permissions without reinstalling. Physical repair
and successful DHCP/SSH still await operator results.

The [refreshed installer](installer-refresh-20261107.md) is now built from
`151a532`: 256 MiB, exact-target yes/no, one expanded write pass, optional readback
and progress feedback. Packaged ARM tests, independent image/source-BOM inspection
and a separate ARM default-no-readback probe pass. This new image is unbooted;
it does not automatically repair installed state. November 6 host SD/NVMe artifacts
remain unchanged. **Do not repeat NVMe installation to fix management.**

## Decisions and physical evidence

- Preserve **A733, actual 2 GB SD, nominal 250 GB NVMe**, minimal Linux KVM/QEMU
  and **zero routine SD writes**. Prefer SPI-to-NVMe with SD removed; otherwise
  immutable SD bootstrap. Never substitute emulation/containers for real KVM.
- [Inventory](board-diagnostic-20261104.md): 12 GiB LPDDR5; SU02G SD
  **1,977,614,336 bytes**, CID `035344535530324780716f216e00a8f5`; NVMe
  **256,060,514,304 bytes**, `SPCC M.2 PCIe SSD`, serial `134807190FC700149277`,
  firmware `EDFM20.0`, logical/physical sectors **512/512**. Use actual geometry.
- Erase authorization for that exact NVMe persists; revalidate identity and idle
  state immediately before the concrete write. Do not request that permission
  again. The operator completed the authorized NVMe installation. No coordinator physical
  erase or SPI programming has occurred.
- [Power/cooling](evidence/power-cooling-20261106.json): operator reports 67 W
  USB-PD adapter and 10.4 mm, six-layer whole-board copper heatsink. Leave all
  heatsinks attached; diagnostic identities suffice. Negotiated power, fan
  configuration and thermal endurance remain unmeasured.
- Wired MAC `10:f4:ae:90:5e:57`, 1 Gbps/full, DHCP `192.168.1.115/24`, gateway/DNS
  `192.168.1.1` and verified HTTPS are observed on the preparation boot. The clock
  starts in 1970; HTTPS passed after manual UTC correction. Repeated installed-host
  networking and automatic time remain open. [Time-sync input](host-time.md) is
  authenticated. The [time-profile build](timesync-outer-launcher.md) now passes
  actual 221-package configuration/export and independent complete archive
  readback, including boot links and fixed 996:996 identity. Opt-in profile and
  matching root/package/clock-state source integration now pass; actual installed
  transitions and NTP remain open. Current installation images are unchanged.
- [Operator inputs](evidence/operator-inputs-20261105.json): RSA3072 and Ed25519
  keys, DHCP hostname `a733-host`, any source/subnet. Require public-key-only SSH.
  [ARM SSH-policy checks](evidence/ssh-console-management-arm-20261106.json) pass;
  real login with both keys remains pending; the serial root account has no usable
  password. The installer physical RAM shell is the current recovery access.
- SD is unmounted in Linux; one zero-write snapshot has `ro=0`. Full-card offline
  hashes, Linux counters and firmware/cycle/workload observations remain required.
- SPI is **XM25QU128C**, JEDEC `204118`, 16 MiB, erase 65,536/write 1 byte.
  [Investigation](spi-boot-investigation.md) records the distinct boot layout and
  erase-count concern. The user permits SPI preparation with exact target/write details and yes/no
  confirmation before programming. Verified backup/recovery and a compatible
  reviewed candidate remain required; no SPI write has occurred. The
  [read-only backup tool](spi-backup.md) passes 27 safety cases and independent
  review; its original-flash backup/off-device retention are not yet performed.
  The [NVMe-first component candidate](evidence/uboot-nvme-candidate-20261107.json)
  now compiles in the frozen builder. Independent linked-code/package/script
  inspection passes, including EL2 service `0x8000ff40` and unchanged firmware
  blobs. A [separate registered replay](evidence/uboot-nvme-replay-20261107.json)
  now matches the unpacked binary, boot package, configuration, challenge and SPI
  boot0 exactly. All 77 new manifest entries resolve to verified local files or
  original references. The [file-only assembler](spi-file-candidate.md) now passes
  two identical assemblies using real boot components and an explicitly synthetic
  backup. It preserves every byte outside the two replacement ranges and records
  all 256 erase-unit hashes. No backup-derived board candidate, physical SPI
  programming or SPI boot is established. The retained candidate workspace also
  passes [full temporary-path restoration](evidence/uboot-nvme-restoration-20261107.json):
  all 27,105 entries and 72 manifest hashes, followed by clean unmount. No
  restored-source rebuild is claimed. The approved [builder expansion](build-host-capacity-plan.md)
  enabled the production root run below; allocations still need staged checks.

## Implemented and verified in software

- [Source lock](../configs/diagnostic-sources.lock.json): vendor `a95129bf`, kernel
  `2ac08e8c` (6.6.98), U-Boot `b791be84` (2018.07), pinned toolchains and boot inputs.
  Preserve compatible boot0/monitor/SCP/kernel/DTB. Actual diagnostic and reviewed
  EL2 handoff boots are recorded in [board diagnostics](board-diagnostic-20261104.md).
  The [modalias whitespace correction](ethernet-diagnostics.md) passes source/ARM
  checks and independent review; that source correction remains unbooted.
- [Minimal interim userspace](host-userspace.md): 220 locked packages, QEMU 7.2.22,
  OpenSSH/storage tools, approximately 531 MiB unpacked. Choose the release base
  after measuring the working KVM host; Debian alone supplies no board support.
- [Roots](host-payload.md), [package-state isolation](host-package-state.md),
  [state](host-state.md), [selection](nvme-root-selection.md),
  [handoff](host-handoff.md), [management](host-management.md),
  [updates](host-updates.md) and [RAM recovery](host-recovery.md) pass software/
  packaged ARM checks. The [current roots](current-host-roots.md) contain all 39
  tools. File-backed interruption tests do not prove physical recovery.
- [Personalized NVMe candidate](evidence/personalized-nvme-20261106.json) uses
  actual geometry, both keys and state UUID `00fd936c-49fb-4fea-87c8-928b9c1599e0`.
  Complete logical hash/GPT/filesystem/selection inspection and independent
  component-binding review pass. [Installer evidence](evidence/deployment-installer-20261106.json)
  and [delivery](deployment-delivery.md) pass software/TLS/ARM checks. Physical acquisition/install/readback and host root A boot are now recorded;
  management is blocked by the machine-id permission defect.
- [Guest operations](guest-operations.md), [activation](guest-activation.md),
  [enrollment](guest-enrollment.md), [checkpoints](guest-checkpoints.md),
  [backup](guest-backups.md), [restore](guest-restoration.md),
  [HTTPS restore](guest-restore-delivery.md) and [networking](guest-networking.md)
  pass host/packaged checks. **No guest has booted.** Acceleration fallback is forbidden.
- [Clean-build progress](build-reproduction-status.md): frozen builder, fresh
  source U-Boot/kernel replays and both SD image reproductions pass. The
  [fresh raw-package pipeline](userspace-fresh-pipeline.md) configures 220 locked
  packages and matches the [normalized export pair](userspace-export-reproducibility.md):
  138,025,729 compressed bytes, 15,876 entries. [Explicit profile selection](userspace-profiles.md)
  and complete staging pass. The [tracked base pipeline](userspace-pipeline-launcher.md)
  passes all three phases and matches the normalized archive. The complete time-profile
  build/readback passes. [Time-profile integration](evidence/time-profile-integration-20261107.json)
  now authenticates matching root/state provenance, seeds private clock state and
  guards boot/update/rollback. Independent source review and an actual frozen-builder
  component pass: 221 packages, eight package-state components, four management
  files, 40 maintenance files and a nondeployable 256 MiB state image. New time roots
  require management ABI v2; updated tools retain v1 rollback support. The
  [production 4 GiB A/B roots](production-host-roots.md) now build in the frozen
  namespace and pass complete readback: 16,367 paths, all file bytes/links/device
  records and raw attributes per slot, plus independent artifact/source review.
  The matching [production 8 GiB state](production-host-state.md) now also builds
  and passes complete readback: 1,172 paths, both keys, corrected machine-id
  permissions, fixed clock ownership and selected root A. One construction each
  is proved. The matching [256 MiB EL2 bootstrap](production-host-bootstrap.md) now
  also builds and passes boot-file, wrapper/newc/gzip checks and independent
  artifact review. Repeats and full NVMe reconstruction remain open.
  Latest privileged Ubuntu unittest discovery runs **1,530 cases, no skips or failures**;
  five shell suites and four assembly cases pass. Within-tick mutation fixtures now force observable mtime changes;
  runtime/source guards are unchanged. See [root evidence](evidence/production-host-roots-20261107.json).
  These checks establish no physical acceptance or independent deployment.

## Next actions and blockers

1. Operator applies the [machine-id repair](installed-host-repair-20261107.md)
   using the existing installer RAM shell, then boots the same matching host SD.
   Return the complete serial log and observed DHCP address/SSH result. Do not
   repeat acquisition or full-disk installation.
2. Validate SSH with both keys, then run [the first KVM guest](kvm-first-guest.md)
   for CPU/disk/network, clean shutdown/restart and persisted disk marker. Return
   its console/log evidence; `/dev/kvm` alone cannot satisfy the gate.
3. Continue [clean-build work](build-reproduction-status.md). Separate authenticated
   replay adapters correct the cross-day account dates while preserving historical
   producers, receipts and artifact pins. [Fresh baseline](evidence/account-date-baseline-execution-20261107.json)
   and [repeated time profile](evidence/account-date-time-execution-20261107.json)
   now pass: 220/221 packages and unchanged compressed/raw/BOM bytes. Independent
   complete 15,876/15,909-entry readbacks, source review and all 46 account-date
   tests under root pass. Production roots and personalized state now pass
   construction and full readback, and the matching immutable bootstrap now passes. Compose NVMe next; software reproduction
   does not establish physical deployment acceptance.
   [Kernel replay retention](kernel-run-retention.md) preserves the expanded tree
   as verified archives on two hosts. [Byte-preserving conversion](evidence/candidate-sd-sparsification-20261107.json)
   recovered 663 MB from three generated image copies. The compiled candidate
   and subsequent file-assembly fixture left 4.01 GB free before expansion. The
   operator-approved [20 GiB builder LVM growth](build-host-capacity-plan.md) now
   succeeded and initially passed the unchanged 22 GiB staging guard. Production
   A/B roots and personalized 8 GiB state pass complete readback; the matching
   immutable bootstrap now passes software inspection. [Verified workspace retention](composition-retention.md)
   archives three terminal work trees on both hosts and recovers 2.48 GB; free
   after bootstrap and byte-preserving sparsification is **23.83 GB**, only
   **208 MB above** the unchanged 22 GiB guard. Recheck every further staging step. No guard reduction or additional LV
   growth is authorized.
   The [candidate build and exact replay](uboot-nvme-candidate.md)
   pass independent inspection. The file-only SPI assembler passes synthetic
   fixtures; its actual board backup/candidate and hardware handoff remain open.
   Keep the existing installation repair as the next operator action; do not
   program SPI or repeat NVMe installation.
   [Firmware license evidence](evidence/vendor-boot-license-inventory-20261106.json)
   remains incomplete; all 479 userspace source archives are acquired, not compiled.
4. Prove installed automatic time/state transitions and physical reliability:
   10 cold/10 warm boots, 24-hour CPU/disk/network/thermal workload, zero routine SD
   writes, interrupted update/manual rollback, dirty-journal and missing-NVMe
   recovery, and off-device guest backup restoration. Establish SPI backup/recovery
   before considering SPI programming. After SSH works, follow the
   [SPI backup procedure](spi-backup.md) and return plan/backup/off-device
   verification output. Do not program SPI yet.

## Completion rule

[Release acceptance](hardware-validation.md) and an independently reproduced
clean build/deployment remain incomplete. Finish only when the plan's physical
results and reproduction procedure are verified. Builds, model review and
emulation cannot waive these requirements.

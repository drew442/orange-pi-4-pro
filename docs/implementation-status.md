# Implementation status

Updated 2026-11-06. Decisions and next actions are below; detailed build evidence
is indexed in [build reproduction status](build-reproduction-status.md).

## Current milestone

The [physical preparation boot](evidence/installer-prep-physical-20261106.json)
reaches its RAM shell with all eight CPUs at EL2, KVM VHE and `/dev/kvm`.
Automatic YT8531 binding/DHCP, routing/DNS and verified HTTPS work after manual
UTC correction. The exact-device NVMe planner reports 512/512 sectors and no busy
indicators, with **no writes**. **Installed NVMe boot, SSH login and actual KVM
guest execution remain unproven.** No newer physical result has been received.
A [LAN access check](evidence/board-access-observer-20261106.json) sees the recorded
MAC answering ICMP with SSH refused; no serial device is attached to codex.

The reviewed [personalized deployment procedure](deploy-personalized-20261106.md)
is ready for the operator. Its 256 MiB installer and host SD images fit the actual
card; the 547,077,631-byte compressed NVMe bundle is authenticated and available
from pinned HTTPS on codex. The installer has no SSH server. Write the host SD
only after successful NVMe installation/readback. Published artifacts are unchanged
by subsequent clean-build work.

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
  again. No coordinator physical erase or SPI programming has occurred.
- [Power/cooling](evidence/power-cooling-20261106.json): operator reports 67 W
  USB-PD adapter and 10.4 mm, six-layer whole-board copper heatsink. Leave all
  heatsinks attached; diagnostic identities suffice. Negotiated power, fan
  configuration and thermal endurance remain unmeasured.
- Wired MAC `10:f4:ae:90:5e:57`, 1 Gbps/full, DHCP `192.168.1.115/24`, gateway/DNS
  `192.168.1.1` and verified HTTPS are observed on the preparation boot. The clock
  starts in 1970; HTTPS passed after manual UTC correction. Repeated installed-host
  networking and automatic time remain open. [Time-sync input](host-time.md) is
  authenticated; the [fixed identity gate](timesync-identity.md) passes ARM checks.
  The [checkpoint guard/private views](timesync-state.md) verify file-backed
  ownership and timestamp preservation. An [offline 221-package recipe](timesync-userspace.md)
  and explicit service policy pass source/input review. The [export adapter](timesync-export.md)
  also passes source/host review; outer orchestration, actual configuration/export,
  package-state composition, installed transitions and synchronization
  remain pending.
- [Operator inputs](evidence/operator-inputs-20261105.json): RSA3072 and Ed25519
  keys, DHCP hostname `a733-host`, any source/subnet. Require public-key-only SSH.
  [ARM SSH-policy checks](evidence/ssh-console-management-arm-20261106.json) pass;
  real login with both keys remains pending.
- SD is unmounted in Linux; one zero-write snapshot has `ro=0`. Full-card offline
  hashes, Linux counters and firmware/cycle/workload observations remain required.
- SPI is **XM25QU128C**, JEDEC `204118`, 16 MiB, erase 65,536/write 1 byte.
  [Investigation](spi-boot-investigation.md) records the distinct boot layout and
  erase-count concern. Verified backup/recovery and SPI-specific authorization
  are required before programming; the current diagnostic selects SD only.

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
  and [delivery](deployment-delivery.md) pass software/TLS/ARM checks. No board
  boot or transfer of these refreshed artifacts is claimed.
- [Guest operations](guest-operations.md), [activation](guest-activation.md),
  [enrollment](guest-enrollment.md), [checkpoints](guest-checkpoints.md),
  [backup](guest-backups.md), [restore](guest-restoration.md),
  [HTTPS restore](guest-restore-delivery.md) and [networking](guest-networking.md)
  pass host/packaged checks. **No guest has booted.** Acceleration fallback is forbidden.
- [Clean-build progress](build-reproduction-status.md): frozen builder, fresh
  source U-Boot/kernel replays and both SD image reproductions pass. A new
  [userspace configuration/export](evidence/userspace-configured-export-20261106.json)
  configures all 220 packages and independently verifies all 15,881 archive entries.
  The [tracked replay](userspace-configuration-replay.md) also configures all 220
  with no APT fallback warnings. The [normalized export pair](userspace-export-reproducibility.md)
  now matches compressed/raw/BOM bytes: 138,025,729 bytes compressed, 15,876 entries.
  Generated NVMe IDs/caches are removed and later archive timestamps clamped to
  the pinned snapshot. The [fresh raw-package pipeline](userspace-fresh-pipeline.md)
  now authenticates/extracts/configures anew and matches those bytes, with complete
  independent readback. The [explicit normalized profile](userspace-profiles.md) now integrates root/state
  selection and passes actual complete staging: 15,876 base entries, 220 packages,
  eight package-state components and the unchanged 39-file runtime. Latest native
  discovery: **1,250 cases, 97 environment skips**,
  four assembly cases and all five shell suites. The [tracked outer launcher](userspace-pipeline-launcher.md)
  passes review and read-only plans; actual root preflight refuses insufficient
  capacity before output creation. Its full execution, production composition and
  independent deployment remain open; these checks establish no hardware acceptance.

## Next actions and blockers

1. Operator follows [personalized deployment](deploy-personalized-20261106.md):
   flash the current installer, correct UTC, authenticate/acquire the bounded-RAM
   bundle, inspect the exact-device plan, then apply the already authorized NVMe
   write/readback. Save complete serial logs and offline SD hashes. Switch to the
   matching host SD only after successful installation.
2. Validate NVMe host boot and SSH with both keys. Run [the first KVM guest](kvm-first-guest.md)
   for CPU/disk/network, clean shutdown/restart and persisted disk marker. Return
   its console/log evidence; `/dev/kvm` alone cannot satisfy the gate.
3. Continue [clean-build work](build-reproduction-status.md): execute the reviewed
   outer pipeline launcher once capacity permits, then use the normalized profile to
   reconstruct production roots/state/NVMe and complete image readback. The reviewed [20 GiB builder LVM growth](build-host-capacity-plan.md)
   awaits approval after [target/backup recheck](evidence/build-host-capacity-recheck-20261106.json);
   no resize occurred and capacity guards remain unchanged.
   [Firmware license evidence](evidence/vendor-boot-license-inventory-20261106.json)
   remains incomplete; all 479 userspace source archives are acquired, not compiled.
4. Finish automatic time/state compatibility and prove physical reliability:
   10 cold/10 warm boots, 24-hour CPU/disk/network/thermal workload, zero routine SD
   writes, interrupted update/manual rollback, dirty-journal and missing-NVMe
   recovery, and off-device guest backup restoration. Establish SPI backup/recovery
   before considering SPI programming.

## Completion rule

[Release acceptance](hardware-validation.md) and an independently reproduced
clean build/deployment remain incomplete. Finish only when the plan's physical
results and reproduction procedure are verified. Builds, model review and
emulation cannot waive these requirements.

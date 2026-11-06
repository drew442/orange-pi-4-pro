# Implementation status

Updated 2026-11-06. Replace stale entries; detailed evidence lives in linked files.

## Current milestone

The [physical installer-preparation boot](evidence/installer-prep-physical-20261106.json)
reaches its RAM shell with all eight CPUs at EL2, KVM VHE and `/dev/kvm`.
YT8531 vendor binding and DHCP now work automatically: `192.168.1.115/24`,
gateway/DNS `192.168.1.1`. Verified HTTPS returns HTTP/2 200 after manual UTC
correction. The read-only planner confirms the authorized NVMe identity and
512/512 sector geometry with no busy indicators and **no writes**.
**Actual KVM guest execution, installed NVMe boot and SSH login remain unproven.**
One boot is not a reliability result. A whitespace bug in init's modalias scan
is corrected in source and independently reviewed; that correction remains
unbooted. No extra diagnostic-only reflash is required for the successful checks.

## Decisions and physical evidence

- Preserve A733, actual 2 GB SD and nominal 250 GB NVMe requirements. Prefer
  SPI-to-NVMe with SD removed; otherwise immutable SD with zero routine writes.
  Actual KVM guest execution remains mandatory.
- [Physical logs](board-diagnostic-20261104.md): 12 GiB LPDDR5, eight CPUs;
  SU02G SD 1,977,614,336 bytes, CID `035344535530324780716f216e00a8f5`;
  NVMe 256,060,514,304 bytes, model `SPCC M.2 PCIe SSD`, serial
  `134807190FC700149277`, firmware `EDFM20.0`; both logical and physical sectors are **512 bytes**.
  Use actual NVMe geometry.
- Heatsinks remain attached. Diagnostic outputs supply component identities;
  chip markings are unnecessary. [Equipment description](evidence/power-cooling-20261106.json):
  67 W USB-PD adapter and 10.4 mm, six-layer copper heatsink covering the board.
  Negotiated power, fan configuration and thermal endurance remain unmeasured.
- User explicitly authorized discarding that exact NVMe's data. Revalidate its
  identity/idle state immediately before the concrete installation; do not ask
  again for matching-device erase permission. No coordinator NVMe erase or SPI
  programming has occurred. SPI programming remains unauthorized.
- SD is unmounted in Linux; one write-counter snapshot is zero, but `ro=0`.
  Firmware audit, offline full-card hash comparisons and cycle/workload evidence
  remain necessary. A single Linux snapshot does not prove zero routine writes.
- Wired MAC `10:f4:ae:90:5e:57`, 1 Gbps/full, YT8531 vendor binding, DHCP,
  default route, DNS and verified HTTPS are observed on the preparation boot.
  Clock starts in 1970; HTTPS passed after operator UTC correction. Automatic
  clock establishment and repeated-boot/workload reliability remain open.
  [Operator inputs](evidence/operator-inputs-20261105.json) provide RSA3072 and
  Ed25519 keys and authorize any SSH source (`0.0.0.0/0`); use DHCP and hostname
  `a733-host`. Public-key-only authentication remains required.

## Builds and host-only verification

- [Source locks](../configs/diagnostic-sources.lock.json): vendor build
  `a95129bf`, kernel `2ac08e8c` (6.6.98), U-Boot `b791be84` (2018.07), pinned
  toolchains/packages and 26 opaque firmware/tool inputs. Compatible boot0,
  monitor, SCP, kernel and DTB remain together.
- [Booted baseline](diagnostic-image.md): 268,435,456-byte raw image, SHA-256
  `b05086afaab3d2f9da6c33b8e457f3552cce2899c08824d371615008bf66d9fd`.
  Vendor images do not fit; no larger card or prior live inventory was required.
- [EL2 candidate evidence](evidence/el2-diagnostic-20261104.json): 268,435,456
  bytes, SHA-256 `08fff0a88c509ee484c2450c58d42629fd90eedb8cd4c57513f98f4f78611b58`;
  gzip 16,188,653 bytes. Only the equal-size boot package is replaced; all outside
  bytes match the authenticated baseline, including the complete filesystem.
  Emitted monitor call is `0x8000ff40`; actual SPSR is `0x3c9`. Packed monitor/
  SCP and copied kernel/DTB/initramfs/boot0 hashes pass; physical EL2/KVM
  initialization are observed, with actual guest execution still pending.
- [Jammy builder](build-host-assessment.md): actual builds with two compilation
  jobs, GCC 11 kernel/Linaro 7.4.1 U-Boot; 4 GiB swap.
  [Verified relocation](evidence/builder-relocation-20261106.json) recovered
  26.12 GB free before the fresh deployment build, passing the 22 GiB guard.
  Optional archives/old fixtures are retained on codex with exact byte hashes;
  current operator inputs remain on the builder. The latest sample showed
  about 7.0 GiB available RAM; recheck disk and memory during staging.
- [Network helper correction](ethernet-diagnostics.md): two fresh builds produce
  identical 268,435,456-byte raw images and 16,188,752-byte gzip files. Independent
  review, module-byte/mode preservation and standalone inspection pass. The
  [corrected preparation SD](installer-preparation.md#next-physical-boot) builds
  and passes independent SD inspection; its installer initramfs was regenerated; its actual ARM runtime probe
  passes. The published helper image has now booted physically with automatic
  vendor PHY binding, DHCP, routing/DNS and verified HTTPS. Its sysfs scan split
  `Fixed MDIO bus.0` at spaces; both init scripts now pass each path as an argument to
  `find -exec`. Whitespace/failure regressions, pinned ARM BusyBox compatibility
  and independent Sol high review pass; new source fix is not yet hardware-booted.
- [Minimal interim ARM64 userspace](host-userspace.md): 220 version/hash-locked
  packages, QEMU 7.2.22, OpenSSH/storage tools; approximately 531 MiB unpacked.
  Final Buildroot/release-base decision follows working hardware KVM baseline.
- [Read-only A/B roots](host-payload.md), [package-state isolation](host-package-state.md),
  [NVMe state](host-state.md), [selection](nvme-root-selection.md) and
  [handoff](host-handoff.md) pass file-backed and packaged ARM checks. The
  [refreshed roots](current-host-roots.md) carry all 39 current tool files
  at `/usr/lib/a733-tools`, including optional networking. Native content/ownership,
  actual ARM 220-package queries and the fixed-seed packaged workflow pass.
  Guest fixture/state/bootstrap/full-disk artifacts remain separately bound.
  [Management](host-management.md) has ARM sshd/key/unit and serial fingerprint
  checks; no real SSH login. [HTTPS delivery helper](deployment-delivery.md) has nine native TLS cases
  and independent review; actual board delivery remains pending.
- [Updates](host-updates.md), [selection journals](host-root-selection.md) and
  [RAM recovery](host-recovery.md) pass private-loop interruption/readback tests.
  Physical update, dirty-journal recovery and rollback remain untested.
- [Personalized NVMe candidate](evidence/personalized-nvme-20261106.json):
  256,060,514,304 bytes, 512/512 sectors, both real keys and state UUID
  `00fd936c-49fb-4fea-87c8-928b9c1599e0`. Raw build and independent full logical
  hash/GPT/filesystem/root-selection inspection pass. Component/source bindings
  pass independent Sol high review. Historical 250 GB fixtures remain nondeployable.
- [Refreshed installer](evidence/deployment-installer-20261106.json) is staged at
  `out/installer-deployment-20261106/a733-installer-prep.img`; matching host
  bootstrap is at `out/host-deployment-20261106/a733-host-sd.img`. Both are
  256 MiB and local full hashes pass. Installer ARM/TLS/SD probes pass, including
  both-key/discard-policy support. No board boot of these new artifacts is claimed.
  [The installation guide](deploy-personalized-20261106.md) now pins the
  547,077,631-byte gzip bundle. Complete expansion/hash verification and a live
  authenticated LAN transfer pass. The pinned HTTPS endpoint is running on
  codex; the reviewed two-image procedure is ready for the operator.
- [Guest fixture](guest-fixture.md), [KVM-only launch](guest-operations.md),
  [activation](guest-activation.md), [enrollment](guest-enrollment.md),
  [checkpoints](guest-checkpoints.md), [backup](guest-backups.md),
  [restore](guest-restoration.md), [HTTPS restore](guest-restore-delivery.md) and
  [isolated networking](guest-networking.md) pass host/packaged ARM checks.
  **No guest has booted.** No acceleration fallback is permitted. The new fixed
  seed passes actual Jammy root/drew byte-identical builds; the
  [complete fixture rebuild](evidence/guest-seed-fixture-20261104.json) passes
  with matching kernel/initramfs and new pinned seed. Earlier artifacts remain unchanged.
- [Explicit boot profiles](boot-profiles.md) preserve baseline defaults while
  accepting the locked EL2 package only by opt-in. Actual host SD and regenerated
  installer SD both build/inspect at 256 MiB; ARM runtime/TLS probes pass. They
  remain unbooted preparations; host state is a fixture. Actual historical images
  still verify, and new EL2 images refuse default baseline verification. A fresh
  committed-source checkout reproduced both images and installer initramfs bytes.
- Jammy: 849 discovery cases without skips, four diagnostic assembly cases and
  all five shell regressions pass. Independent Sol high reviews cover boot,
  installation/selection and rollback changes. [Detailed implementation evidence](implementation-evidence.md)
  separates builds, software probes and physical results.

## External integration blockers

The preparation boot resolves first-boot vendor PHY binding, DHCP, route/DNS and
verified HTTPS delivery after manual clock correction. No further diagnostic
command is required to repeat those successful observations. Real keys/source
policy and [ARM SSH-policy checks](evidence/ssh-console-management-arm-20261106.json)
are prepared. [The actual-geometry candidate](evidence/personalized-nvme-20261106.json)
and matching 256 MiB EL2 bootstrap pass software inspection. Builder headroom
was recovered without losing retained bytes. Encoded verification, pinned HTTPS
LAN delivery and independent procedure review now pass. The
[operator installation sequence](deploy-personalized-20261106.md) is ready.
No physical installation or payload transfer to the board has occurred. Actual SSH, KVM guest execution and release gates remain open.
NVMe erase authorization for the recorded drive persists; no additional erase
permission is being requested. SPI identity/geometry are directly observed as
XM25QU128C, JEDEC `204118`, 16 MiB, erase 65,536 bytes, write 1 byte; verified
backup/recovery and SPI-specific programming authorization remain outstanding.

## Next actions and open gates

1. Operator follows the [personalized deployment](deploy-personalized-20261106.md):
   flash the current installer, authenticate/acquire the compressed bundle into
   bounded RAM, inspect the exact-device plan and apply the already authorized
   NVMe erase/write/readback. Preserve logs and SD hashes, then switch to the
   matching host-bootstrap SD. Source and target are revalidated before writing.
2. Validate physical installation/readback, host boot and SSH with both keys,
   then follow [the first KVM guest procedure](kvm-first-guest.md) for actual
   execution, clean shutdown/restart and NVMe disk persistence.
   `/dev/kvm` alone does not satisfy this gate. Establish automatic time and
   repeat network checks under installed-host conditions.
3. Prove installed NVMe operation, SD policy, repeated boots/workload, interrupted
   updates, manual rollback, dirty-journal recovery and off-device restore. SPI
   is identified as XM25QU128C/16 MiB; backup and proven recovery remain
   necessary before programming authorization.
   [Pinned SPI investigation](spi-boot-investigation.md) records its distinct
   layout, fixed erase-count concern and the diagnostic's SD-only boot target.
4. Full clean-build reproducibility remains open. The prepared-host
   [U-Boot replays](evidence/uboot-replay-20261106.json) and
   [mapped kernel replay](evidence/kernel-replay-mapped-20261106.json) reproduce
   the retained artifacts, including all 332 regular module files anchored to
   the original initramfs. U-Boot's original random challenge is retained only
   under explicit replay; fresh-build randomness remains the default. Kernel
   replay uses original debug paths and umask 0002. Those runs reused source
   caches and the prepared host.
   All [842 exact package archives](evidence/builder-packages-acquired-20261106.json)
   and the [signed AMD64 seed](../configs/builder-base-20261106.lock.json) are
   authenticated and retained. The [actual seed isolation](clean-builder.md)
   and [fresh offline installation](evidence/builder-offline-install-20261106.json)
   now pass: exact fully configured inventory, package audits, namespace/device
   restrictions and unchanged host/cache checks. Failed inputs remain archived;
   current deployment artifacts are preserved. The [frozen builder](evidence/builder-frozen-20261106.json) now passes two
   identical exports and full namespace restoration, including metadata, exact
   842-package inventory and isolation checks. A separate codex copy passes full
   expansion/hash verification. [Fresh upstream sources and Linaro toolchain](evidence/rebuild-sources-acquired-20261106.json)
   are acquired and hash-pinned, including all 26 firmware/tool blobs. These are
   two exports of one installed root, not independent OS constructions.
   [Verified retirement](evidence/builder-scratch-retirement-20261106.json) recovered
   11.25 GB of redundant archives. [Fresh source import](evidence/frozen-source-import-20261106.json)
   passes inside the restored builder; all three Git checkouts match pins and
   pass integrity/no-alternates checks. Next replay U-Boot, then recover further
   capacity to meet the kernel’s unchanged 15 GB start guard. Retain two compilation
   jobs and existing kernel/NVMe guards.
   The [26-input license inventory](evidence/vendor-boot-license-inventory-20261106.json)
   records missing attribution/grants. All [479 userspace source archives](evidence/userspace-sources-acquired-20261106.json)
   for the 220 binary packages are acquired and independently rehashed.
   Firmware/source/license audit and independently reproduced deployment remain gates.
5. Automatic time is absent from the current 220-package candidate. The
   [time-sync input](host-time.md) is authenticated and its existing dependencies
   verified, but requires a new package/payload profile, NVMe-state compatibility
   review and physical validation. Current installation instructions retain manual
   UTC correction; the prepared images and their hashes are unchanged.

## Completion rule

[Release acceptance](hardware-validation.md) remains incomplete: actual KVM guest,
installed NVMe boot, 10 cold/10 warm cycles, 24-hour workload, zero routine SD
writes, interruption/rollback/recovery/restore and independently reproduced
deployment. Build success, model review and emulation cannot satisfy these gates.

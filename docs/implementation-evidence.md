# Implementation evidence

## 2026-11-04: diagnostic preparation

Coordinator read the plan, status, builder assessment and routing policy before
implementation. SSH, passwordless sudo, Ubuntu 22.04.5, approximately 49 GiB free,
7.1 GiB available memory and 4 GiB unused swap were rechecked. `build/preflight.sh`
passed actual file-backed loop mounting, a write/read within that allocated test
filesystem, and ARM64 static BusyBox chroot execution. No physical storage was
written. Preflight log: builder `/home/drew/orange-pi-4-pro-build/preflight.log`.

Source commits and binary/archive hashes are in
[`diagnostic-sources.lock.json`](../configs/diagnostic-sources.lock.json) and
[`vendor-boot-inputs.sha256`](../configs/vendor-boot-inputs.sha256). All 26 vendor
pack inputs/tools matched. Git sources were resolved with `git ls-remote`, then
checked against the actual checkouts. Source branches are not the lock mechanism.
The remote source verifier was executed successfully against those worktrees.

## Independent boot review

Sol high reviewed the recipe and the pinned U-Boot tree before integration.
Coordinator retained architecture, integration and final review ownership.

| Concern | Evidence / disposition |
| --- | --- |
| Raw offsets | Vendor family `sun60iw2.conf:223-224`; assembler checks exact offsets and boundaries |
| ARM wrapper, ARM64 payload | Kernel `scripts/package/builddeb:283`; U-Boot `arch/arm/lib/bootm.c:346-423` secure-monitor handoff |
| Load overlap | U-Boot `include/configs/sunxi-common.h:322-326`; assembler now bounds kernel/DTB/ramdisk; hardware reserved memory still pending |
| Other-device fallback | Compiled `BOOTCOMMAND` restricts `boot_targets` to `mmc0`; boot script stops after failure |
| Persistent environment | `env/nowhere.c` and `env/env.c:215`; recipe disables persistent backend/command and asserts result |
| System switching / boot count | `board/sunxi/Kconfig:135`; both disabled/asserted |
| Misc command write | `board_helper.c:726-727`, normal caller commented at `board_common.c:815`; additionally disabled with `BOOTCMD_SKIP_MISC` |
| Recovery-key misc write | `board_helper.c:349`, called at `board_common.c:814`; conditional on key flag and named misc partition, absent from this image |
| Boot parameters / userdata | `sprite/sunxi_boot_param.c:99` and `update_user_data()` return early; OTA feature disabled/asserted |
| Opaque boot0 / monitor | Retained vendor inputs and hashes; zero-write/EL2 behavior needs physical evidence |
| Toolchain provenance | Review caught trusting an extracted directory; recipe now extracts fresh from hash-checked archive |

The independent reviewer implemented `tests/test-assemble-diagnostic.py` after
the review; the coordinator reran it locally and on Jammy. It verifies exact
256 MiB image size, DOS partition bounds, raw bootloader bytes, extracted clean
ext4 files, script CRC/payload, output preservation and oversized-input refusals.
These tests use generated fixtures, not a bootable board or physical card.

A fourth packaging regression compares two full image hashes. It initially
exposed random ext4 directory hash seeds and imported source inode ctimes;
the assembler now fixes the hash seed and normalizes generated-filesystem ctimes
and ownership using `debugfs`. Identical fixture inputs produce identical images
locally and on Jammy. This is not yet a two-clean-build reproducibility result.

The first real U-Boot configuration failed because `ENV_IS_NOWHERE` hid
`CONFIG_ENV_SIZE`, still required for its RAM environment buffer and allocator.
An independently reviewed one-line Kconfig patch removes that dependency and
retains `0x20000`; it enables no persistent backend. Patched builds use a fresh
detached worktree; the original source checkout stays clean. The patch is hashed
in the source lock and copied beside artifacts. Runtime build outcome is tracked
in status.

## Actual diagnostic candidate

The final recipe completed on Jammy. U-Boot required invocation from its actual
directory (`PWD` is used for `DTS_PATH`); its vendor export copies now stay within
the temporary worktree. Earlier failed/ignored-copy logs were preserved separately
on the builder and are not represented as clean builds.

Current image: [manifest](evidence/diagnostic-20261104.json), 268,435,456 bytes,
SHA-256 `b05086afaab3d2f9da6c33b8e457f3552cce2899c08824d371615008bf66d9fd`.
Compressed size: 16,188,652 bytes. SHA-256 and size remained correct after SSH
transfer and local decompression. Independent Sol high review reran the actual
artifact verifier and checksum list, checked gzip expansion, wrapper CRCs/payloads
and the archived init script. Actual Ethernet module vermagic is
`6.6.98-a733-diag SMP preempt mod_unload aarch64`.

Actual load endpoints: kernel `0x42a05048`, DTB including resize `0x430431a8`,
initrd `0x4367b39b`. They meet packaging budgets; physical reserved-memory
compatibility still needs serial evidence. Completed build logs and resource
samples: builder `/home/drew/orange-pi-4-pro-build/logs/`. Samples retained over
6 GiB available RAM, approximately 41 GiB free disk and only 12 KiB swap use.
These samples are not measurements of exact peak resource demand.

Two fresh U-Boot worktrees reused the same built kernel. Kernel, DTB, initramfs
and configurations matched; U-Boot differed only at its 128-byte generated
challenge and header checksum. Vendor `Makefile:1579-1586` reads `/dev/urandom`;
`board/sunxi/sunxi_mips.c:190-198` uses the challenge for USB debug-key verification.
MIPS support is disabled, but `arch/arm/cpu/armv7/spare_head.c:70` exposes its
address to firmware, so opaque monitor use cannot be ruled out. Preserve the
generated source/hash next time and review its role before changing randomness.
No full clean-build reproducibility claim is made.

No board boot, physical storage erase, SD flash, SPI programming, NVMe installation
or KVM guest was performed. Exact-SD inventory/authorization and all hardware
acceptance results remain outstanding.

## Independent NVMe plan review

A separate Sol high review found absent `lsblk --tree` would refuse ordinary
partitioned targets, and accepting unusually large logical sectors could overlap
primary GPT metadata at a fixed 1 MiB start. Both were fixed with regressions.
Logical sectors are explicitly limited to supported 512/4096-byte geometry.
Reports describe checked busy indicators, not guaranteed idleness. The planner
does not write devices; mount namespaces, raw-device users and observation races
remain explicit limitations for any future installer design. All 18 planner tests
pass locally and on Jammy.

## Routing calibration

| Work | Explicit model / effort | Outcome / repair |
| --- | --- | --- |
| Vendor extraction | Luna / low | Useful bounded report; coordinator corrected mistaken RAM interpretation, constructed URL and entrypoint in one follow-up |
| Initramfs implementation | Sol / medium | Host packaging tests accepted; no hardware claim |
| NVMe plan implementation | Sol / medium | Independent review required two fixes, completed at same tier |
| Boot review / image tests | Sol / high | Load-region, fallback and toolchain reuse findings integrated |
| NVMe review | Sol / high | Tree/sector findings integrated and tested |
| KVM launch preparation | Sol / medium | 13 host tests accepted; KVM-only argv/non-root/hash checks; no guest run |
| License extraction | Luna / low | Located notices and missing opaque-firmware terms; coordinator verified U-Boot notice path after incomplete extraction |

Children used fresh focused contexts, exclusive write scopes and no delegation.
At most two children were active. Input/output token counts, cache accounting,
account prices and exact reviewer repair time were not exposed; monetary cost
and measured savings are unknown. Hardware acceptance was not delegated or waived.

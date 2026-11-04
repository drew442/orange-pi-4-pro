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

The later [provenance validation build](evidence/diagnostic-provenance-20261104.json)
ran from clean project checkout `82e9e7b` using the existing pinned source/download
caches and kernel output, with a fresh U-Boot worktree and separate artifact
directory. It preserved the exact generated challenge C, unpacked binary, config
and complete captured build log before cleanup. Both checksum lists and the actual
image verifier passed after worktree removal. Parsing the saved C recovered 128
bytes that occur exactly once at offset 754,336 in the unpacked binary. This
preserves provenance without changing randomness or claiming its full security
role is audited. It is not a second full clean-builder build. The original offered
diagnostic image was reverified unchanged; neither image has booted on the board.

## Independent NVMe plan review

A separate Sol high review found absent `lsblk --tree` would refuse ordinary
partitioned targets, and accepting unusually large logical sectors could overlap
primary GPT metadata at a fixed 1 MiB start. Both were fixed with regressions.
Logical sectors are explicitly limited to supported 512/4096-byte geometry.
Reports describe checked busy indicators, not guaranteed idleness. The planner
does not write devices; mount namespaces, raw-device users and observation races
remain explicit limitations for any future installer design. All 18 planner tests
pass locally and on Jammy.

## Early-boot storage preparation

The [NVMe resolver and ARM64 runtime](host-storage-boot.md) have independent Sol
high review. The reviewer reproduced acceptance of an empty duplicate export
field; the implementer added separate occurrence flags and three regressions.
All 39 fixtures then passed under Bash, host BusyBox ash and the exact pinned
diagnostic ARM64 BusyBox 1.30.1. Coordinator execution in private builder chroots
also checked the four-file dynamic ELF closure, positive ext4/negative blank-file
probes, unchanged fixture hashes and BusyBox stat/hex conversion. No mounts or
physical storage writes occurred. The resolver is deliberately conservative about
blank partitions and remains preparation, with root/state mounting and management
configuration open. The packaging helper has six nonprivileged regressions.

## Update candidate preparation

[Candidate evidence](evidence/host-update-candidates-20261104.json) records clean-checkout
preparation of both actual 4 GiB package-state roots using source commit
`8cd3947f575681fab59268b59f8175fbd194bd84`. Independent readback matches both
original full hashes, exact trusted manifests and previously prepared selectors;
receipts and `0700`/`0400` metadata match. Each capture took about 18 seconds
with about 30 MiB peak RSS. The 286-case suite passes on Jammy without skips,
plus four local diagnostic cases and all shell regressions. A Jammy test repair
removed timestamp-granularity dependence; the isolated discovery invocation
explicitly supplies the trusted repository import path. No production code
changed after the captured producer commit. The test verifier and final test
source hashes are recorded separately.

Independent Sol high review caught unbounded debugfs stdout accumulation in
the shared metadata reader. Byte caps now apply during reads, with a deadline,
child cleanup and duplicate-key rejection before JSON parsing. Oversize, hang,
EOF-then-hang, cleanup-swap and actual regular-file ext4 checks pass. These are
file-only preparation checks, not installed update, rollback, power-cut or
hardware evidence. New code does not imply a rebuilt/deployed bootstrap.

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
| Host userspace implementation | Sol / medium | Signed snapshot recipe; mount/cleanup findings fixed at same tier; actual build passed |
| Host userspace review | Sol / high | Private namespace and debootstrap lifecycle fixes independently checked before root run |
| Guest fixture implementation | Sol / medium | Host packaging tests passed; coordinator completed actual two-job kernel build after script-format/WLAN corrections |
| Guest fixture review | Sol / high | Missing script-execution dependency caught; disk/write safeguards checked; actual KVM remains unperformed |
| Boot provenance collection | Sol / medium | Two fixture tests passed; coordinator verified actual generated source/binary pairing after cleanup |
| Boot provenance review | Sol / high | Copy timing/checksum/source-only guards accepted; no firmware normalization or hardware claim |
| Immutable root payloads | Sol / medium | Authenticated archive/module packaging; 12 fixtures; actual 4 GiB A/B builds and content audit passed |
| Payload/loop review | Sol / high | Fixed loop attachment race and required writable-block evidence; controlled file tests accepted; hardware remains pending |
| blkid runtime extraction | Luna / low | Exact binary/dependency hashes and package ownership extracted; coordinator authenticated and executed isolated runtime |
| NVMe boot resolver | Sol / medium | 39 fixtures accepted after one targeted duplicate-field correction |
| Storage boot review | Sol / high | Duplicate-field rejection independently reproduced and fix verified; coordinator tested exact ARM64 BusyBox |
| Host handoff implementation | Sol / medium | 49 fixtures; coordinator found absolute init-link, console and overlay-permission issues, fixed at same tier |
| Host handoff review | Sol / high | Partial-move cleanup ordering finding fixed; coordinator authenticated actual archive, tested pinned ash and private builder overlay |
| Per-installation state | Sol / medium | 14 regressions; actual 8 GiB journaled file/gzip built using fixture inputs and fresh identities |
| State artifact review | Sol / high | Staging-versus-filesystem metadata and storage-test coverage findings fixed; coordinator read back actual selected content and verified both full hashes/expansion |
| NVMe root selection | Sol / medium | Fixed GPT roles, recovered-state selector and moved-/run cleanup; 84 handoff and 41 resolver fixtures |
| Selection review | Sol / high | Mutable-inode parsing and unbounded PID1 metadata findings fixed; private authenticated capture and capped reads independently checked |
| Management activation | Sol / medium | 20 regressions; coordinator built managed roots/state/bootstrap and executed ARM software probes |
| Management review | Sol / high | Foreign overlay/nested mount rejection and atomic no-replace key publication corrected and rechecked; hardware activation pending |
| Managed artifact documentation | Luna / low | Four bounded guides updated; coordinator corrected wording about mocked configuration preparation |
| Complete host SD bootstrap | Sol / medium | 12 authentication/path/package/assembly regressions; coordinator executed real Jammy packaging and independent disk/archive inspection |
| Host SD boot review | Sol / high | Four unchanged diagnostic regressions and 12 host tests passed; verifier metadata comparison suggestion fixed with two coordinator regressions |
| NVMe layout and GPT codec | Sol / medium | Seven independent structure/CRC/UUID/readback cases; 18 original planner fixtures retained |
| NVMe integration review | Sol / high | SD/removable backing-storage rejection fixed; formatter root-mode regression and explicit sector geometry checked |
| Independent NVMe inspector | Sol / medium | Nine GPT tamper/sparse/path/filesystem tests; production codec not imported |
| NVMe inspector review | Sol / high | Nine tests and bounded same-descriptor readback accepted; fixed builder staging confirmed separately |
| Installer runtime inventory | Luna / low | Authenticated archive file sizes, Python symlink and direct ELF dependencies extracted; missing sfdisk confirmed, runtime closure still open |
| Installer runtime staging | Sol / medium | Ten regressions; safe hardlinks, bounded alias traversal and private source capture corrected before actual staging |
| Installer preparation SD | Sol / medium | Eight authenticated input/path/storage/assembly fixtures; 256 MiB layout preserved |
| Installer/runtime/boot review | Sol / high | Expanding aliases, empty stdlib files and provenance attribution findings fixed; private probe and SD checks reviewed |
| Full-disk writer | Sol / medium | 26 safety/fault regressions; conservative shared reports, root precondition and throttled progress fixed before publication |
| Writer/install review | Sol / high | Candidate eligibility mismatch and state-readback coverage gaps corrected; loop-only integration harness and packaging reviewed |
| Compressed NVMe bundle | Sol / medium | 24 regressions; absent fixture intent preserved and active Jammy Python/zlib bound to package ownership and hashes |
| Bundle review | Sol / high | Missing-intent and alternate-interpreter findings fixed; actual pinned Jammy runtime probe passed |
| Verified payload acquisition | Sol / medium | 20 TLS/storage/deadline/failure regressions; source and receipt identities retained, full bounded expansion without raw staging |
| Acquisition/install review | Sol / high | Slow-header deadline, foreign replacement cleanup and timer teardown findings fixed and independently rechecked; packaged ARM TLS probe and native full-size harness reviewed |
| Immutable package metadata | Sol / medium | Eight complete snapshot trees, bounded descriptor and staging-only cache pruning; root added bootstrap/selector/runtime pairing |
| Package-state/install review | Sol / high | Installer provenance Path error and ambient dpkg database override found and fixed; exact mount/ABI negative fixtures and two private-loop harnesses reviewed |
| Package VFS/ARM probes | Sol / medium | Native tiny A1→B2→A1 plus actual ARM default-path queries and independent 1,122-entry metadata readback; coordinator executed and verified cleanup |
| Package guide updates | Luna / low | Coordinator requested one correction to preserve commands/references and correct state RW semantics, then corrected remaining scope wording |
| Update candidate preparation | Sol / medium | Authenticated raw root staging and no-overwrite publication; coordinator ran both actual 4 GiB roots and independent source/capture/selector comparisons |
| Update/parser review | Sol / high | Unbounded debugfs capture found and fixed; bounded reads, deadline/reaping and ownership cleanup checked; no installed updater or hardware claim |
| Slot metadata extraction | Luna / low | Source mapping established; coordinator clarified fresh per-boot RAM overlay semantics and authenticated actual archive metadata separately |

Children used fresh focused contexts, exclusive write scopes and no delegation.
At most two children were active. Input/output token counts, cache accounting,
account prices and exact reviewer repair time were not exposed; monetary cost
and measured savings are unknown. Hardware acceptance was not delegated or waived.

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
explicitly supplies the trusted repository import path. The recorded candidate producer hashes remain unchanged; the later staging
controller is recorded separately below. The test verifier and final test
source hashes are recorded separately.

Independent Sol high review caught unbounded debugfs stdout accumulation in
the shared metadata reader. Byte caps now apply during reads, with a deadline,
child cleanup and duplicate-key rejection before JSON parsing. Oversize, hang,
EOF-then-hang, cleanup-swap and actual regular-file ext4 checks pass. These are
file-only preparation checks, not installed update, rollback, power-cut or
hardware evidence. New code does not imply a rebuilt/deployed bootstrap.

## Inactive-slot staging software checks

[Staging evidence](evidence/host-update-stage-loop-20261104.json) records the
checkout-only controller, read-only live-host policy fixtures, and a native
private-loop transaction. An injected error leaves 8 MiB written, 12 MiB
attempted and zero verified; the current selection is unchanged. A fresh
explicit retry writes and verifies all 4 GiB against the authenticated B root.
Real exclusive block claims, descriptor/geometry ioctls, flush and full readback
run; live NVMe identity and TTY authorization are replaced by explicit test
fixtures. Guest/boot sentinel checks are confined to private files. No root
switch, physical update, rollback or power-cut acceptance is implied.

Two early retries were safely refused by a nonblocking lock before any new
write. Settling builder device events before the separate retry plan cleared
the refusal; the lock owner was not independently identified. Resource sampling
also caught test mock call history retaining copy buffers. Direct hook
replacements reduced final peak process RSS to 34,212 KiB. The successful
final run took 28.71 seconds; independent inspection confirmed no owned backing
attribute or private workspace remained. Detach failure preserves the workspace
for investigation. These harness repairs leave production locks and copying
unchanged.

All 329 discovery cases pass on Jammy without skips; four separate diagnostic
cases, shell regressions, Bash syntax, TOML parsing and diff checks pass.
Independent Sol high review accepted target selection, exact GPT byte checks,
source/storage authentication, interruption reporting and the final harness.
The staging controller still requires a trusted complete checkout. The later
selection controller is recorded below; packaging, booted rollback, cross-version
shared state and all hardware release gates remain open.

## Explicit root selection software checks

[Selection evidence](evidence/host-root-selection-loop-20261104.json) records
source `ca180ef` on Jammy: actual authenticated 4 GiB A/B private copies, A
mounted read-only, and 64 MiB journaled ext4 state in a private mount namespace.
A pre-rename fault retains A; a post-rename interruption reports B visible with
durability uncertain. Reusing the durable journal and explicitly cancelling
pending selection restores A. Offline unmounted-state readback confirms A;
both complete root hashes and unrelated sentinels remain unchanged. All owned
mounts, loops and files were removed, independently checked after process exit.
The run took 1:53.89 with 37,500 KiB peak process RSS. Live NVMe identity/TTY
checks are explicit fixtures; no actual boot, running-root switch or hardware
rollback is implied.

Independent Sol high review reproduced false publication receipts for Ctrl-C
after rename, then at the helper-return/caller-accounting boundary. The helper
now defers handled signals through rename accounting, reconciles by the created
inode, accepts held directory FDs and preserves visibility on close failure.
The coordinator applied the reviewer's caller guard after the first focused
repair. Actual queued SIGINT and handled SIGTERM tests preserve visible/durable
flags and restore signal masks. The reviewer independently reran 59 focused
policy/helper/transaction cases. Journals sync existing file data before parent
sync on reuse; they preserve history, not an assertion of previous-slot health.

All 367 discovery cases pass on Jammy without skips, plus four diagnostic cases
and shell regressions. Packaging, offline failed-boot recovery, shared-state
restore/cross-version compatibility, board management/KVM health and all physical
release criteria remain open. The five-field selector and existing immutable
boot images are unchanged; this work does not deploy a new bootstrap.

## Offline recovery software checks

[Recovery evidence](evidence/host-recovery-loop-20261104.json) records source
`11d3318` on Jammy. A fresh sparse 250 GB loop disk has real GPT roles/kernel
partitions, a trusted 4 GiB root and an actual 8 GiB journaled ext4 state. Full
state hashes before/after preparation match. Recovery archives a malformed
selector, publishes verified A and unmounts state; offline reads confirm A and
the exact original corrupt bytes. Complete root and GPT checks plus boot/guest
and state sentinels match. Independent post-process inspection finds no owned
workspace or backing. The run takes 1:28.36 with 36,784 KiB peak process RSS.
NVMe/RAM entry and interactive authorization are fixtures; it is clean-state
selector recovery, not crash-journal replay or physical failed-boot acceptance.

Independent Sol high review found that accepting arbitrary ext4 journal
references could make a writable mount access another device. The coordinator
restricted state to the pinned recipe's internal journal inode 8, zero external
journal device/UUID and no journal-device feature. Independent rejection cases
and the real native filesystem validate that contract. The shared signal-safe
publisher was factored without changing existing accounting. An actual recovery
SIGINT regression preserves publication flags and attempts owned-state cleanup.
The conservative state-write receipt is emitted before mounting because replay
may change state even on a failed mount. No raw root writes are issued.

All 397 discovery cases pass on Jammy without skips, with four separate
diagnostic cases and shell regressions. The diagnostic config hash still
matches its pinned record and enables namespaces. New-controller ARM execution,
packaged recovery, dirty-journal transactions, real boot, state/guest backup
restoration and all physical release gates remain open.

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
| Inactive target policy | Sol / medium | Exact live root/state/selector ties, fixed GPT/kernel geometry and idle opposite-slot policy; 14 fixtures including identity matrix and snapshot orchestration |
| Inactive staging writer | Sol / medium | 29 source/authorization/race/lock/failure fixtures; coordinator ran real 4 GiB private-loop interruption/retry and verified cleanup |
| Inactive staging review | Sol / high | Production policy/transaction accepted; retained-workspace cleanup improvement and final bounded-memory harness checked; physical gates unchanged |
| Selection policy | Sol / medium | Activation/cancellation pre/post states with strict unchanged staging defaults; 20 focused fixtures |
| Selection transaction | Sol / medium | Read-only whole-root authentication, held-directory publication and durable history; interruption repair integrated with coordinator caller fix |
| Selection/signal review | Sol / high | Independently reproduced helper/caller Ctrl-C gaps, verified both fixes and 59 focused cases; private ext4 harness reviewed before coordinator run |
| Offline recovery policy | Sol / medium | Fixed RAM environment, all-node idle/GPT/UUID and internal-state contract; coordinator added external-journal rejection from independent review |
| Offline recovery transaction | Sol / medium | Full root/state read-only preflight, journal-first repair of missing/malformed selection, conservative mount/publication/cleanup receipts; 20 fixtures |
| Offline recovery review | Sol / high | External-journal device-safety blocker found and fixed; controller/shared publisher and fresh-GPT native harness reviewed; physical acceptance unchanged |
| Slot metadata extraction | Luna / low | Source mapping established; coordinator clarified fresh per-boot RAM overlay semantics and authenticated actual archive metadata separately |
| Maintenance package | Sol / medium | Exact runtime source closure, deterministic archive and captured installer copies; coordinator built tools and refreshed 256 MiB SD |
| Package/install review | Sol / high | Ancestor publication race, foreign cleanup and source reread findings fixed; shared captured sources accepted |
| ARM maintenance probe | Sol / medium | Authenticated closure, real candidate hash/debugfs, restricted private namespace and policy refusal checks; actual execution recorded separately |
| ARM namespace correction/review | Sol / medium + Sol / high | Actual candidate guard refused a chroot without a root mount; nonrecursive private root bind fixed fixture topology without bypassing production policy |
| Maintenance guide | Luna / low | Bounded source extraction drafted guide; coordinator supplied actual artifacts, corrected immutable-host staging path and integrated operational guides |

## 2026-11-04: portable maintenance integration

Source `d5c71f5` produced the 58,524-byte authenticated tools archive and a
refreshed 256 MiB preparation SD; [records](evidence/maintenance-tools-20261104.json)
and [cached clean repeat](evidence/maintenance-rebuild-20261104.json) retain
artifact hashes. Kernel, DTB and vendor boot components stayed pinned.
Independent package review found unsafe ancestor publication, foreign cleanup
and source reread issues; descriptor-relative publication/owned cleanup and one
captured source map corrected them before the actual build. Legacy installer
paths and the new tree use the same bytes.

The first actual ARM maintenance run correctly refused candidate storage
because the chroot lacked a visible root mount. An independently reviewed
nonrecursive private self-bind fixed the fixture topology. The next run passed
full candidate authentication/debugfs, then exposed inherited ignored SIGINT in
the signal fixture. Verifier `c44d670` establishes and checks Python's standard
handler/unblocked mask; a real subprocess regression covers ignored+blocked
inheritance. Production source/storage/publication policy was unchanged.
The [final ARM run](evidence/maintenance-arm-20261104.json) passed in 1m38s,
maximum RSS 102,560 KiB. Actual configured QEMU pathname/hash and binfmt F flag
were verified; the kernel-retained interpreter descriptor was not inspected.

Both actual SD inspections, packaged ARM runtime/TLS probes, archive extraction
and native help checks passed. Current discovery: 426 cases on Jammy, no skips;
locally two privileged skips. Four separate diagnostic cases and all shell
regressions also pass. No new compilation or package installation was needed.
Builder logs, including the two failed fixture runs, are retained under
`/home/drew/orange-pi-4-pro-build/maintenance-artifacts-20261104/logs/`.
Post-cleanup sampling had over 7 GB available RAM and 18 GB free fixed storage;
these are samples, not exact peak-use measurements. No private ARM fixtures or
host-visible mounts remained. Physical boot, NVMe/SPI work, dirty-journal replay,
rollback, SD-write measurements and actual KVM remain unperformed.

## 2026-11-04: guest input lifetime and placement

Source `16ad952` replaces path-reopen launch with held authenticated descriptors,
regular single-link checks, bounded exact-size hashing and cooperative disk
locking. Two Sol medium workers owned disjoint launcher/storage files; root
integrated the guard before hash reads, added boot-input placement, descriptor
numbers above stdio, close-on-exec treatment and second-check failure coverage.
The fixed guest mount lives under RAM `/run/a733-guests`, with data on selected
NVMe p5. No mount activation/account creation is implemented or assumed.

Independent Sol high review caught a test that reached the new earlier KVM
access check instead of the intended lock. Calling the lock engine directly
corrected that test without bypassing its real flock. The reviewer then accepted
all policy/FD changes and reran 24 launcher plus 12 storage cases. Dry-run test
wording was corrected to content preservation, since reads can update atime.

A fresh Sol medium worker supplied the opt-in ARM backend harness. Coordinator
reviewed its authenticated archive capture, private namespace, regular-file-only
QEMU operations and cleanup, then ran it on Jammy. [Actual ARM evidence](evidence/guest-file-backend-arm-20261104.json)
records original descriptor bytes surviving path replacement through QEMU 7.2.22
file reads. No VM/accelerator was invoked. An actual UID-65534 builder storage
call [refused unsupported topology](evidence/guest-storage-builder-refusal-20261104.json).
All 449 discovery cases passed on Jammy with no skips, plus four diagnostic
cases and shell regressions. Local discovery had two privileged skips.
No private ARM fixture or host-visible mount remained; resource sampling had
over 7.3 GB available RAM and 18 GB free fixed storage. No compilation or
package installation was needed, and no physical device or hardware gate was
tested. Full guest activation, supervision, network, backup and restoration
remain required work.

## 2026-11-04: explicit guest activation

Source `927a5a2` adds live p5 activation and locked RAM service identities. Sol
medium workers owned disjoint policy, transaction and account files; a fresh Sol
medium worker supplied the native loop harness. Root retained the mount/journal
contract, integration, ARM account harness, execution and acceptance ownership.
At most two children were active. No account or mount was installed on the board.

Independent Sol high review found a primary-GID parser bypass: a leading-zero
foreign GID could evade a string collision check. Both passwd UID/GID now use
canonical bounded unsigned numeric parsing; signed, malformed and overflow
regressions pass. Root also fixed close-failure receipt preservation; independent
review accepted both fixes. Existing unrelated credential rows must remain
unchanged and no password hashes appear in plans or receipts.

[Native loop evidence](evidence/guest-mount-loop-20261104.json) records actual
p5 mount, post-kernel-mount SIGINT cleanup, retry, 0711 readback and idempotence
on one fresh owned 250 GB sparse GPT loop; four non-guest partition sentinels
and a guest file stayed unchanged. Its live-host/NVMe/TTY boundaries were
fixtures. The coordinator corrected the harness's initial plan mode to 0400
before execution. No physical device was accepted and no dirty-journal crash
was simulated.

[ARM evidence](evidence/guest-accounts-arm-20261104.json) records real pinned
systemd-sysusers 252.39 creation, locked identity, unrelated-row preservation,
idempotence and collision refusal. The real CLI refused the private chroot; only
the trusted fixture's RAM mount guard was mocked for positive file checks. An
earlier successful probe preceded the final numeric/receipt fixes; the final
source was rerun from a clean checkout. No builder host accounts changed.

All 497 discovery cases pass on Jammy with no skips (two privileged skips
locally), plus four diagnostic cases and shell regressions. Both integration
checks exited zero and post-process inspection found no leftover fixture, loop
attachment or host-visible guest mount. Resource samples retained over 7 GB
available RAM and 18 GB fixed-storage free space; no compilation/package install
was needed. Raw logs are outside Git under the builder's
`guest-activation-artifacts-20261104/logs/`. Packaging, boot integration, guest
enrollment/supervision, backup/restore and every physical acceptance gate remain.

## 2026-11-04: guest enrollment and unit preparation

Source `54346ac` adds no-overwrite guest enrollment and pure configuration/unit
rendering. Sol medium workers owned disjoint enrollment and profile modules;
root added the directory-placement entrypoint while preserving regular-file
launcher checks, and wrote/executed the ARM integration harness. Independent
Sol high review accepted the source guards, permissions, publication signal
handling, inode-owned cleanup and preparation unit scope. The unit has no
automatic restart or graceful-stop claim and embeds the initial disk pin.

[Actual ARM evidence](evidence/guest-enrollment-arm-20261104.json) records all
140,442,449 bytes of the pinned kernel/initramfs/raw fixture copied and read
back with actual ownership. UID 60001 could write its disk but could not modify
kernel data, read the manifest, rename assets or create directory entries; a
different UID was refused. Source seeds remained unchanged. Actual systemd 252
parsed the unit without diagnostics; no service, VM or KVM guest was started.
The disposable fixture mocked live mounted-NVMe/UUID boundaries and used the
builder's memory/CPU data, not board measurements.

The first full Jammy test run exposed an older timing-dependent mutation test:
a same-size overwrite in one filesystem clock tick could keep observed
timestamps equal; unexpected successful preparation then leaked a test-held
lock into the next subcase. Test fix `6859dbe` sets a distinct initial timestamp
and always closes an unexpected prepared guest. Independent review accepted
this as deterministic coverage of observable metadata changes. Production
behavior is unchanged; concurrent in-place writers must still be excluded.

The final 528 discovery cases pass on Jammy with no skips, plus four diagnostic
cases and shell regressions. Local discovery had 21 privileged skips. Post-job
checks found no enrollment fixture or host-visible guest mount. No compilation,
package install, physical enrollment, board boot, actual KVM or SD-write test
was performed. Raw failed/final suite logs and ARM results are retained under
the builder's `guest-enrollment-artifacts-20261104/logs/`.

## 2026-11-04: stopped-guest checkpoint records

Source `6c62d03` adds strict manifest validation, retained stopped-disk inspection
and metadata-only checkpoint publication. Two fresh Sol medium workers owned
disjoint policy/schema and transaction files. Root specified the service/lock/
consumer contract, added enrollment manifest pins and exact raw history bytes,
and wrote the ARM integration wrapper. Independent Sol high review accepted
the implementation and all 31 checkpoint plus 22 enrollment tests under sudo.

The reviewer noted that parsed history alone could not reconstruct a valid
noncanonical old manifest's exact pin. Before integration, the archive gained
exact base64 before/after bytes; a regression proves both bytes and recorded
hashes. History remains manifest metadata, never a disk backup. The checkpoint
records clean-shutdown attestation rather than claiming filesystem verification.

[Actual ARM evidence](evidence/guest-checkpoint-arm-20261104.json) proves initial
enrollment pin output, old launch-pin refusal after a working-disk write, actual
cross-process lock refusal, queued-SIGTERM publication reconciliation, exact
history, unchanged disk bytes, old manifest-pin refusal, no-op behavior, new-pin
dry-run and updated unit parsing. Live storage, stopped-service, consumer and
TTY boundaries were fixtures, and no guest or service was started. A separate
[real proc scan](evidence/guest-checkpoint-consumers-20261104.json) saw a
UID-60001 child holding an owned regular file and returned empty after exit;
the prepared builder also reported the expected properties for a nonexistent
unit. These checks do not establish board visibility or guest cleanliness.

The full Jammy run passed 559 discovery cases without skips, plus four diagnostic
cases and all shell regressions. Local discovery had 47 privileged skips; the
checkpoint/enrollment root suites separately passed locally. Both ARM and
post-process cleanup checks passed. No compilation/package install, physical
checkpoint, VM, actual KVM, power-loss recovery or SD-write test was performed.
Raw logs remain outside Git in `guest-checkpoint-artifacts-20261104/logs/` on
the builder. Actual clean shutdown/restart, packaging, boot integration, guest
networking and off-device backup restoration remain required.

Children used fresh focused contexts, exclusive write scopes and no delegation.
At most two children were active. Input/output token counts, cache accounting,
account prices and exact reviewer repair time were not exposed; monetary cost
and measured savings are unknown. Hardware acceptance was not delegated or waived.

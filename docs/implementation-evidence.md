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

## 2026-11-04: streamed guest backup artifacts

Source `f984c01` adds a stopped-guest sender and authenticated artifact receiver.
Two fresh Sol medium workers owned disjoint sender/tests and format/receiver/tests
files. Root specified the fixed framing and exact history contract, wrote the
ARM/native integration probe and reviewed the changes. Independent Sol high
review accepted the implementation and reran all 33 focused tests under sudo
without skips. No child delegated or handled physical acceptance.

Coordinator review corrected the receiver's subprocess descriptor path to use
`/proc/<receiverPID>/fd/N`: `/proc/self/fd/N` would refer to findmnt's own
descriptors. A regression and the actual native receiver storage check cover
the correction. The artifact verification file records completed file checks;
publication and durability belong exclusively to the final receiver receipt,
so a post-rename fsync failure cannot leave a false durability claim inside it.

[Actual ARM/native evidence](evidence/guest-backup-arm-20261104.json) records a
3,386,765-byte compressed archive, SHA-256
`e83ddd8bc5d71ca2507cae92ce4735add9243c36b5f991573c9f29ceef3c85de`.
The pinned 128 MiB disk, kernel, initramfs, current manifest and exact checkpoint
history passed ARM source streaming and native private capture/authentication,
sparse extraction and independent full readback. A wrong compressed pin was
refused before parsing; an existing output was refused. All source and harness
hashes match the committed files. Source live storage/service/consumer/approval
boundaries were substitutions inside a disposable fixture. The native receiver
used the actual builder ext4/LVM storage check. Both ends ran on the builder;
this is neither an off-device retention test nor a restored guest boot.

The probe took 63.79 seconds, with 197,048 KiB maximum RSS. Jammy root discovery
passed 592 tests without skips, plus four diagnostic cases and all shell
regressions. Local discovery passed with 51 privileged skips; local root backup
tests also passed. TOML parsing, Bash syntax and diff checks passed. Post-checks
found no fixture directory or backup mount, with 18,750,976,000 bytes free and
about 7.8 GB available RAM. No compilation, package installation, physical
storage write, guest launch, clean shutdown or hardware acceptance was performed.
Logs remain outside Git in the builder's
`guest-backup-artifacts-20261104/logs/` directory.

[Prospective operator instructions](guest-backups.md) cover authenticated SSH
transfer to another machine and readback. Fresh-destination restore, off-device
retention, restored KVM execution and maintenance/boot integration remain open.
Model input/output tokens, cache use, account prices and exact review/repair
time were not exposed; monetary cost and measured savings remain unknown.

## 2026-11-04: fresh-destination guest file restoration

Source `f7e86a9` adds a read-only authenticated restore source and guarded
prepare/apply controller. Two fresh Sol medium workers owned disjoint source
and transaction files/tests. Root designed UUID rebinding and provenance,
implemented the manifest extension and ARM integration probe, and retained
architecture/integration/acceptance ownership. Independent Sol high review
accepted the final implementation and all 35 focused root cases without skips.

Review reproduced a valid 300-record history producing a 70,827-byte plan, which
the 64 KiB apply reader refused. The plan now binds the exact history envelope
with byte count, SHA-256 and record count. History files are closed after fsync,
then individually reopened for final identity/metadata/full-readback checks,
rather than retaining a descriptor per record. A 300-record restore passed
under a 96-descriptor limit. Real queued SIGTERM after rename and parent-fsync
failure tests preserve visible publication and report durability separately.
Foreign entries are retained instead of being recursively removed.

[Actual ARM file evidence](evidence/guest-restore-arm-20261104.json) authenticates
the pinned rootfs and guest fixture, creates disposable locked accounts, mutates
and checkpoints the disk, streams its backup, then restores into a fresh
directory with a different destination UUID. All 140,442,449 payload bytes pass
independent copy/readback checks; exact history and original source bytes are
preserved. The restored manifest pin is
`be343cde567c17185cf36449bc529019b5a98a6088a130a432dcdc68362780d9`.
Existing-target refusal, updated manifest provenance, a new checkpoint-base
inspection, launch dry-run and unit parsing pass. Live namespace/NVMe/UUID/
service/UID-census/TTY boundaries were substituted only inside the disposable
fixture. No VM or service was started. All recorded runtime/harness source
hashes match the committed files.

A separate [native UID census](evidence/guest-restore-consumers-20261104.json)
sees an actual child with real/effective/saved/filesystem UID 60001 and refuses
restoration policy while it exists; the census succeeds before creation and
after reaped exit. No host account was created. This is a process-policy check
on the builder, not a stopped-guest or board-visibility proof.

The ARM probe took 95.68 seconds and 202,900 KiB maximum RSS. Jammy root passed
627 discovery cases without skips, four diagnostic cases and all shell
regressions. Local discovery passed with 78 privileged skips; root restore
cases passed separately. TOML, Bash syntax and diff checks passed. Cleanup
checks found no fixture directory or restore mount. Builder free space was
18,745,786,368 bytes with about 7.8 GB available memory; no compilation or
package installation occurred. Logs remain outside Git under
`guest-restore-artifacts-20261104/logs/` on the builder.

[The restoration guide](guest-restoration.md) documents complete source and
destination prerequisites, exact prospective commands, receipts and limits.
Local archive staging can consume too much room for a large guest on the
nominal 250 GB NVMe; delivery without staging remains implementation work.
Off-device retention, restored KVM/network/data checks, physical interruption,
boot integration and reproduced deployment remain open. No physical storage
was erased or written. Token/cache/account-price and exact review-time data
were not exposed; monetary costs and measured savings are unknown. Children
used focused fresh contexts, exclusive files, no nested delegation and at most
two concurrent children.

## 2026-11-04: pinned HTTPS guest restoration without archive staging

Source `7679fbe` adds a canonical encoded-chunk index generator and HTTPS source,
sharing the existing decoded archive parser. Two fresh Sol medium workers owned
disjoint chunk/index and network source files/tests. Root integrated source
selection, preserved local compatibility, added real TLS controller tests and
the ARM/native probe, and retained architecture/acceptance ownership. Independent
Sol high review accepted all changes, including 42 new focused cases and 17
local-controller compatibility cases.

The index authenticates each 1 MiB encoded chunk before any decoder receives its
bytes, and the complete archive hash/size/EOF before the final chunk is released.
The index hash precedes index JSON parsing. Canonical framing, external manifest
pin, complete history and all payload checks remain required. HTTPS performs
fresh verified scans for prepare, apply review and private copying. Selected
host/namespace/storage/UUID checks precede contact; guest-specific account,
service, UID, budget and free-space checks follow the first authenticated
read-only scan and precede destination writes. No encoded local archive is
created by this source.

Root review replaced environment-sensitive SSL context creation with an explicit
verifying client context. An actual TLS regression confirms `SSLKEYLOGFILE`
cannot create a local key log. The first Jammy discovery run also exposed a
test relying on an identical rewrite changing a timestamp within one clock tick.
Seeding an old initial mtime makes the observed-change regression deterministic;
production hash/metadata checks were unchanged. Both corrections received
focused independent review. Initial failed and pre-correction probe logs are
retained alongside final logs.

[Actual ARM TLS evidence](evidence/guest-restore-https-arm-20261104.json) restores
the pinned fixture over real loopback TLS, including certificate/hostname
verification with a protected custom CA. Its 3,386,765-byte archive has four
encoded chunks; the 543-byte index SHA-256 is
`ad2ce11ae99f8dd0e1eb410e1c160a3494b866850f29bf093785f261bae4ef29`.
All 140,442,449 payload bytes, manifest and exact history match local restoration.
Wrong-index-pin refusal and private-stage cleanup pass. A native index-generator
follow-up uses actual builder fixed-storage checks for input/output and produces
the identical index. Source and harness hashes match the committed files.
Actual TLS unit tests additionally reject certificates/hostnames/redirects,
changed multi-chunk streams, suffixes/truncation and a drip-fed deadline. A real
TLS controller test changes the final chunk during copying after private writes;
the destination stays unpublished and owned staging is removed.

The final probe took 112.24 seconds and 362,164 KiB maximum RSS. Its source and
destination live on the same builder, and NVMe/namespace/UUID/service/UID-census/
TTY boundaries were fixture substitutions. The retained server archive is a
fixture input, not a client staging file. No guest was booted. This neither
proves off-device retention nor board networking or full-capacity resource use.

Jammy root discovery passed 669 cases without skips, plus four diagnostic cases
and all shell regressions. Local discovery passed with 94 privileged skips.
TOML parsing, Bash syntax, documentation command syntax/links and diff checks
passed. Post-checks found no fixture directory or HTTPS restore mount; builder
free space was 18,740,310,016 bytes with about 7.8 GB available RAM. No compilation,
package installation or physical storage operation occurred. Raw logs remain
outside Git under the builder's `guest-restore-https-artifacts-20261104/logs/`.

[Delivery instructions](guest-restore-delivery.md) cover index generation,
trusted pins, restricted static HTTPS endpoint requirements and exact client
commands. Endpoint deployment, actual board networking/resources, restored KVM
execution, physical recovery and reproduced deployment remain open. Children
used fresh focused contexts, exclusive files, no descendants and at most two
concurrent children. Tokens, cache accounting, account prices and exact review
time were not exposed; monetary cost and measured savings are unknown.

## 2026-11-04: installed and portable guest tool closure

Source `cd80603` extends the explicit maintenance package to 38 sources plus
`package.json`, with 16 CLI entrypoints, and installs the same captured tree
under `/usr/lib/a733-tools` in both immutable root slots. A fresh Sol medium
worker owned the package allowlist/tests and maintenance ARM CLI list. Root
owned root integration, provenance, raw-image verification and packaged ARM
workflows. A fresh Sol high reviewer accepted the final implementation and
native checks. Luna low updated six guest guides from supplied verified facts;
root reviewed those edits. Children used exclusive files, fresh contexts, no
descendants and at most two concurrent children.

The [113,133-byte portable archive](evidence/guest-tools-package-20261104.json)
has SHA-256 `e7495d225ef754a3ad591baecd098db7555a494dfe36d6b808de298be7f1ba07`.
Its manifest and archive match a fresh-checkout rebuild byte for byte. Both
[4 GiB root components](evidence/guest-tools-payload-20261104.json) retain the
compatible kernel/DTB/modules, journal-free read-only format and package-state
ABI. Exact source/provenance bytes are shared with the portable package.
No activation, mount, account, service, guest or listener is added by packaging.

Review strengthened [independent root inspection](evidence/guest-tools-payload-verify-20261104.json)
to require the checkout-derived complete BOM/entrypoints, every package
directory's exact entries and root-owned `0555` mode, and root-owned `0444`
file contents. An empty self-declared BOM cannot pass. The first raw verifier
run exposed variable shadowing in the newly added directory loop; root fixed
the verifier and successfully repeated it on the same images. Images were not
rebuilt to mask the failure. Raw hashes, compressed expansion, ownership,
package metadata and both slots pass final checks. Build time was 144.14 seconds
with 68,060 KiB maximum RSS; verification took 43.77 seconds and 21,364 KiB.

The [packaged ARM probe](evidence/guest-tools-arm-20261104.json) authenticates
the external package manifest, exact tree and deterministic archive, then stages
only captured package bytes at the installed path. Review found a second-read
gap after tree authentication; captured bytes are now compared again with
trusted expected bytes before any staging. Regressions refuse a changed capture
and inconsistent outer entrypoints before execution. Actual ARM sysusers,
enrollment, checkpointing, backup, local and real-TLS HTTPS restoration,
ownership/readback and unit parsing pass without a repository tree in the
chroot. No guest or service starts. The probe took 112.39 seconds and 360,200 KiB.
Live NVMe/namespace/UUID/service/UID-census/TTY policy boundaries remain explicit
fixture substitutions, not hardware evidence.

The separate [maintenance ARM probe](evidence/guest-maintenance-arm-20261104.json)
imports all 37 Python modules and runs all 16 CLI help paths using the pinned
minimal runtime. It retains full 4 GiB candidate authentication, real ARM
debugfs inspection, GPT/sysfs fixtures, atomic selector storage/queued SIGINT
and production guard refusals. It took 119.43 seconds and 102,736 KiB. The
registered binfmt interpreter pathname/hash is checked; its kernel-held F
descriptor is not independently inspected. RAM recovery is not an operational
guest host, and its published SD image still carries the older tool subset.

Jammy root passed 674 discovery cases without skips, four diagnostic cases and
all shell regressions. Local discovery passed with 94 privileged skips. TOML,
Bash syntax, guide syntax/links and diff checks passed. To preserve the root
builder's unchanged conservative free-space guard, a verified duplicate
268,435,456-byte generated SD image was removed from the old repeat directory
after hashing it against the retained primary and confirming it was not loop
attached. Its identical compressed repeat, primary raw image and logs remain.
No physical target was erased. Post-checks found no private fixture or mount;
free builder space was 17,472,258,048 bytes with about 7.7 GB available memory.
No compilation or package installation occurred.

Artifacts/logs are exported under the builder's
`guest-tools-artifacts-20261104/`; raw roots remain in the protected build output,
with compressed roots exported for download. The [operator guide](guest-tools-package.md)
distinguishes new installed/portable trees from the earlier recovery SD and
full-disk fixtures. Full per-installation assembly, actual boot integration,
guest networking/KVM, zero SD writes and physical recovery/acceptance remain
open. Tokens/cache/account prices and exact review time were not exposed;
monetary cost and measured savings remain unknown.

## 2026-11-04: restricted loopback TCP guest networking

Source `0d83950` adds an optional strict network configuration and paired launch
flags; source `1b5123c` extends the trusted packaged ARM probe. Two fresh Sol
medium workers owned disjoint schema/launcher and packet-fixture files. Root
owned the guest HTTP fixture, actual ARM/network namespace harness, compilation,
packaging and integration. Independent Sol high review accepted the final
production changes and test-only probe extension, including a native zero-CPU
network-backend execution. Children used focused fresh contexts, exclusive
files, no descendants and at most two concurrent children.

Legacy configs/units retain their exact restricted backend without a listener.
Opt-in forwarding fixes host IPv4 loopback, the guest DHCP address and TCP;
ports and complete pairs are checked before payload access. New units retain
KVM-only execution, unprivileged identity, capabilities and NVMe/descriptor
guards. No bridge, TAP, host route, firewall or management configuration is
changed. Checkpoint/backup/restoration preserve the optional config. Earlier
strict readers refuse it, so rollback must retain a compatible portable tree
and remains a physical validation gate.

The [actual ARM network/CGI probe](evidence/guest-network-arm-20261104.json)
uses authenticated userspace and pinned static BusyBox in private PID/mount/
network namespaces. QEMU's `none` machine reports zero CPUs through QMP; a
synthetic Ethernet/TCP endpoint echoes 53 bytes through real usernet forwarding.
Proc TCP tables show only `127.0.0.1`, no wildcard/IPv6 forward. A synthetic SYN
toward a separate host-gateway listener produces zero accepts over 0.75 seconds.
This is a bounded backend-policy check, not exhaustive isolation or a guest
network test. Static ARM BusyBox ash/httpd serves the exact CGI, rejects a wrong
confirmation and records the correct counter. No guest kernel or KVM executes.
The probe took 13.86 seconds and 58,208 KiB maximum RSS.

Review found that the initial CGI marker redirection could expose an empty file
before printf completed. The final CGI writes a private noclobber RAM temporary
file, then atomically renames it. A deterministic delayed-printf regression
verifies that init cannot observe incomplete marker contents. The final ARM
probe was repeated with this fix; pre-correction logs remain alongside it.
The guest disk is synced/unmounted before HTTP starts and confirmation has a
120-second deadline. A gateway ping no longer stands in for request/response.

The [packaged ARM configuration workflow](evidence/guest-network-config-arm-20261104.json)
preserves networking through actual enrollment, checkpoints, backup and local/
HTTPS restoration file checks, with existing live-boundary substitutions. It
verifies generated unit flags and the packaged launcher's exact dry-run backend.
It took 111.65 seconds and 359,824 KiB. Hardware acceptance remains explicitly
pending; no service or guest was started by that workflow. Source hashes match
the committed files. The [113,789-byte updated package](evidence/guest-network-package-20261104.json)
matches a fresh-checkout archive/manifest rebuild byte for byte. Existing root
images and recovery SD were not replaced.

The [new guest fixture](evidence/guest-network-fixture-20261104.json) compiles from
the pinned vendor source with two jobs and 30-second memory/disk samples. Build
time was 268.78 seconds and maximum reported child RSS 545,700 KiB. The kernel
matches the prior Image byte for byte. New initramfs SHA-256 is
`2f3360a8f6365b84c352cae99949a5ae92a81001efe28dbae221f334805f854b`;
new 128 MiB disk SHA-256 is
`48074e898f24a18ed617c5a999f81806d4c6064086712acb4d4831d6ce4102ff`.
[File readback](evidence/guest-network-fixture-verify-20261104.json) verifies all
manifest pins, actual cpio init/CGI contents, e2fsck, ID and zero counter.
Compared with the earlier disk, observed inode differences include builder
UID/GID and source ctime. A complete binary-difference explanation and whole
seed-filesystem reproducibility remain open; source/kernel reproducibility is
not generalized to that disk.

Final Jammy root discovery passed 693 cases without skips, four diagnostic
cases and all shell regressions. Local discovery passed with 94 privileged
skips. TOML, Bash/POSIX syntax, guide syntax/links and diff checks passed. No
private probe mount/directory remained. Builder free space after export was
15,319,891,968 bytes with about 7.7 GB available RAM. No packages were installed;
no board storage or SPI was written. Logs/artifacts are outside Git under the
builder's `guest-network-artifacts-20261104/`.

[Prospective hardware instructions](guest-networking.md) require the updated
complete portable tools, trusted fixture pins, console counter, verified HTTP
read and confirmation, listener/management checks, KVM-only execution, shutdown
and restart evidence. Actual board DHCP, guest kernel/virtio traffic, KVM,
long-run behavior and network continuity through boot/update/rollback remain
open. Token/cache/account pricing and exact reviewer time were not exposed;
monetary cost and measured savings remain unknown.

## Fixed guest seed metadata

Sol medium implemented a bounded seed helper; independent Sol high review found
no blocker and requested complete imported-source provenance. The coordinator
retained a full build/configs/scripts/licenses archive in future fixture outputs.
Eight helper regressions and the actual fixture staging/seed shell regression
pass locally. The production CLI was then run without test substitutions on the
pinned Jammy builder as root and as drew with different umask/cwd/timezone.
Both full 128 MiB files compare byte for byte, SHA-256
`ff595fac0b9a7d990f099edc0dd0f9d4859fb9942e185d2f2377088ce32bd577`.
[Evidence](evidence/guest-seed-20261104.json) records the package pins and sources.
Readback includes explicit ext4 metadata, content and e2fsck. The complete
kernel/fixture recipe has not been rerun after this change; existing published
fixture pins remain unchanged. No guest or physical storage test is implied.

## Physical first boot and separate EL2 diagnostic

The operator supplied full serial/read-only logs and NVMe sysfs/fdisk output.
Luna low extracted the bounded inventory; the coordinator checked the decisive
boot messages and preserved log lengths/hashes in the
[physical evidence record](evidence/board-diagnostic-20261104.json).
The board reaches the RAM shell with 12 GiB/eight CPUs and the actual SD/NVMe.
BL31 SPSR `0x3c5`, Linux EL1 entry and unavailable HYP prove a real blocker.
No heatsink removal/chip markings are needed. The user authorized discarding
this exact NVMe's existing data; no NVMe erase or SPI programming was performed.

Sol high traced normal U-Boot `bootm` and disassembled the actual locked monitor,
including its hypervisor service, HCE setup and PSCI secondary-entry derivation.
The existing monitor service `0x8000ff40` provides a narrow EL2 experiment without
changing firmware/kernel/DTB. Sol medium implemented a separate recipe; independent
Sol high review found an initial unused-variable build error and SCM metadata
mismatch, both corrected. Fresh assembly also exposed filesystem metadata drift;
the final recipe clones the authenticated booted raw image and overlays only the
equal-sized boot package. All other bytes compare identical.

The actual final Jammy build took 40 seconds with two compilation jobs. Full
manifest hashes, gzip expansion, MBR/boot blobs, legacy ext4, boot script CRCs,
actual TOC1 component payloads and copied immutable pins pass the reusable
`verify-el2-diagnostic.py`. Coordinator inspected the final compiled handoff;
independent review checked matching object/linked calls. Monitor/SCP match locked
vendor binaries. No board boot is implied. [Candidate evidence](evidence/el2-diagnostic-20261104.json)
records hashes, source/patch provenance, tests and limitations.

Jammy discovery passes 719 tests without skips; all four diagnostic assembly cases
and five shell regressions pass. Three manifest-boundary tests reject symlink,
FIFO and directory input. The [reviewed operator guide](el2-diagnostic.md) supplies
exact downloads, authorized-card guards, write/readback, full-card evidence,
serial expectations and baseline recovery before any installation. Source/model
account token/cache/pricing totals were not exposed; monetary savings remain
unknown. Hardware EL2, KVM guest execution and full release gates remain open.

The subsequent [complete fixed-seed fixture rebuild](evidence/guest-seed-fixture-20261104.json)
passes end to end: 234.28 seconds, two compilation jobs, 545,912 KiB maximum child
RSS, 30-second RAM/disk samples. Kernel and HTTP initramfs match the prior network
fixture byte for byte; the new seed matches both standalone root/drew builds.
All manifest pins, actual cpio init/CGI, complete imported-source archive,
e2fsck, fixture ID and zero counter pass independent file readback. This closes
the earlier whole-recipe rerun gap; it does not establish guest execution.

## Explicit boot-profile integration

Luna low extracted baseline pin dependencies; the coordinator retained the
existing default and required an explicit experimental profile across host SD,
installer SD, NVMe assembly and inspectors. Sol medium implemented the bounded
change. Independent Sol high review found historical default verification would
reject missing metadata; the correction accepts only an absent baseline key with
all blob/evidence pins intact. Null/malformed metadata and missing EL2 provenance
are refused. Neither installation intent nor physical write authorization changes.

[Actual Jammy evidence](evidence/el2-boot-profiles-20261104.json) records the
256 MiB host and installer SD files, regenerated installer initramfs, raw offset/
MBR/ext4/kernel/DTB/script/CRC/payload/gzip inspection, packaged ARM runtime and
TLS loopback checks. Both actual historical baseline images still verify under
the default; both actual new EL2 images refuse that default. Real component
manifests pair under explicit EL2 and refuse wrong/missing/null profiles; no new
complete NVMe image was generated. New host state is still a fixture. No image
has a new physical boot result, and no KVM guest has executed.

Jammy discovery passes 726 cases without skips, four diagnostic assembly cases
and all five shell regressions. The [SPI source investigation](spi-boot-investigation.md)
records the software-detected chip, separately pinned SPI boot0/layout, the fixed
vendor erase-count concern and the diagnostic's SD-only boot target. Luna low
performed extraction and Sol high checked the source-based conclusions. No
heatsink removal or SPI programming is required for these findings; actual ID,
backup, recovery and authorization remain prerequisites for programming.

A fresh protected checkout of `cae284a` rebuilt the explicit EL2 host SD,
installer initramfs and installer SD. Raw/gzip/wrapped initrd bytes match the
first artifacts exactly, and both SD inspectors pass again. The repeat uses
existing authenticated payload/runtime caches, not clean package acquisition or
an independently reproduced physical deployment. Final builder sample: 9,311,625,216
bytes free, 7,651,188,736 bytes available RAM; no private probe mounts remain.
The full NVMe assembler's 22 GiB staging guard remains in place; reclaim
regenerable caches before preparing the actual operator image. No storage device
was erased and no SPI programming occurred.

## Builder staging capacity and source retention

Five complete generated guest working trees were archived and compared before
removing expanded copies, retaining both failed and successful build states.
The original pinned kernel checkout/configuration/Image/modules remain. Complete
manifest artifact pins match before/after. Full restoration of the latest guest
source cache was reproduced at its original path, compared member for member,
and its Image/configuration authenticated against the published fixture; the
expanded cache was removed again while its archive remained.

Forty-one manifest-listed regular image files were made sparse without changing
logical contents, size, path, inode or mode. Complete hashes pass before and after
all operations. Independent Sol high review corrected evidence-directory/no-follow
output guards, unconditional checks and busy-file detection before execution.
The builder was kept quiescent; snapshot checks/advisory locks do not claim
protection against a later noncooperating writer. EL2 artifact content and gzip
inspection pass again. [Detailed evidence](evidence/build-cache-retention-20261104.json)
retains source/script/journal pins outside Git.

Free disk increased from 9.3 GB to 23.9 GB (22.25 GiB), narrowly clearing the
22 GiB NVMe staging guard. Recheck before subsequent root/NVMe builds. Available
RAM remains about 7.7 GB; no compilation or package installation was needed.
No board storage erase, SPI programming, hardware guest or deployment recovery
was performed. Builder cache restoration is not physical release acceptance.

## Current host roots and fixed-seed ARM workflow

Fresh A/B roots include the current 39-file tool tree with optional loopback
networking. Full native raw/compressed hashes, immutable ext4/no-journal features,
closed tree contents and root ownership/modes pass. Actual ARM package-state
queries on both roots match the 220-row lock and 1,122-entry BOM; read-only views,
writable state logs and private namespace/loop cleanup pass. The 141.42-second
build reported 68,076 KiB maximum child RSS and reused the compatible pinned
kernel/DTB/modules. It needed no source/package download or compilation.

The packaged ARM probe exposed a legacy-fixture pin mismatch with the newer
HTTP initramfs/normalized disk. Sol medium added explicit repository-locked
`legacy`/`fixed-seed-http` verifier profiles. Independent Sol high review required
the fixed label check; it and strict 8 MiB kernel/64 MiB initrd/exact 128 MiB disk
bounds are enforced. Six new tests cover profiles, malformed identity, descriptor
capture refusals and forwarding/default compatibility. Original artifact capture
still succeeds, and the newer initramfs refuses the default legacy profile.

The actual packaged ARM workflow then passed with the fixed-seed fixture and
loopback configuration in 118.89 seconds, 359,752 KiB maximum child RSS. It covers
file-based enrollment/checkpoint/backup/local and HTTPS restore/unit checks with
recorded live-boundary substitutions. No guest or systemd service was started.
[Current root evidence](evidence/current-roots-20261104.json) retains precise scope,
new component pins and source/log hashes. Jammy discovery passes 732 cases without
skips, four diagnostic assembly cases and all five shell regressions.

Additional generated kernel compiler objects were fully archived/compared before
unlinking; all tracked source and 323 protected Image/configuration/map/module
files remain unchanged. The [retention record](evidence/kernel-object-retention-20261104.json)
keeps archive/member-list pins. After root export, free space is about 24.0 GB,
above the 22 GiB full NVMe staging guard with limited headroom. Physical EL2/KVM,
real management state/geometry, installation and release acceptance remain open.

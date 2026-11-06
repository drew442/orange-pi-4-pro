# Kernel artifact reproduction

The separate `build/kernel-replay.py` recipe rebuilds the retained A733 kernel
in a fresh private source checkout. It leaves the published installation images
in place. Cache recovery locations are recorded in
[build-cache retention](build-cache-retention.md). The [actual private build](evidence/kernel-replay-20261106.json)
now [reproduces the complete Image, DTB and configuration](evidence/kernel-replay-mapped-20261106.json)
with explicit original debug paths and umask 0002. Independent verification also
authenticates all regular module files against the original initramfs. The
earlier native-path build remains a recorded failure.

The target is the `Image`, board DTB and final kernel configuration pinned in
[evidence/diagnostic-20261104.json](evidence/diagnostic-20261104.json). The source
is kernel commit `2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f`; the starting
configuration comes from the pinned vendor commit's
`external/config/kernel/linux-sun60iw2-current-a733.config`. The recipe applies
the same KVM/configuration settings and build metadata as `build/diagnostic.sh`.
It uses two compilation jobs and the explicit complete
`replay-20261106` builder package profile. Compiler and full inventory checks
remain mandatory before and after compilation.

The prepared-host replays used caches at `/home/drew/orange-pi-4-pro-build` on
the Ubuntu 22.04 builder. The original kernel cache is now retained off-host;
the fresh frozen-builder run binds its separately imported sources at that
canonical path inside the namespace. Both vendor and kernel caches are deliberately shallow
checkouts of their pinned commits; this property is recorded. The private kernel
checkout requests a shared clone from the cached source, then checks out the
pinned commit into new files. Inspection found local Git packs and no alternates
in the generated replay checkouts; the failed-tree archive's restored Git metadata
also passes a full object check without access to the original builder path. Existing object files and the original `.config` are
not copied into the private checkout. This establishes a fresh compilation,
not independent source acquisition or an immutable clean builder.

Run only from a clean committed full project checkout with an existing `out/`
directory. The recipe refuses existing outputs, unsafe/overlapping paths,
package drift, less than 3 GB available RAM or no more than 15 GB free disk
before starting. It samples resources every 30 seconds and cancels the compiler
process group if available disk drops below 3 GB. Failed outputs remain available
for investigation; do not reuse their output names.

Successful artifact reproduction requires all three anchored targets to match
exactly. The recipe also installs stripped modules into its private artifacts
and records their relative paths, modes, sizes and SHA-256 hashes. The recipe's
comparison with the retained modules directory is explicitly **unanchored**.
A separate independent audit authenticates the original compressed initramfs
and compares all 332 regular module files, including permission modes. That
stronger proof covers regular files; it does not claim generated directory
metadata is identical.

The result, source/input snapshots, resource samples and build logs remain under
the new output directory. Generated sources, objects and binaries stay out of
Git. Build success does not establish physical boot, KVM guest execution,
independently reproduced deployment or the remaining release acceptance gates.

## First execution result

The private build at project commit `581a3101362bdb3c4bd596d9d01fccf284f27a9a`
ran from 10:05:25 to 10:21:09 UTC on 2026-11-06. Both sources, the complete
package inventory and retained inputs passed before/after checks. The generated
Image has the expected 27,283,464-byte size but SHA-256
`a920654a5bce4b3346bb18d69a43a521b39927edee700144aa00885b007bba16`,
instead of the retained target's hash. The recipe returned failure.

Exactly 40 bytes differ: the 20-byte arm64 vDSO GNU build ID at raw Image offset
16,290,424 and the 20-byte main kernel build ID at offset 21,656,516. All other
raw Image bytes match. Independent ELF/readback review confirms those records.
Retained original vDSO debug records contain the original source directory;
new records contain the private source directory, with matching compiler
producer strings. The [fresh private vDSO experiment](evidence/vdso-replay-20261106.json)
now reproduces the complete unchanged vDSO with explicit compiler/assembler
debug-path mappings and recorded umask 0002. Full-kernel reproduction remains
a separate comparison. One stripped module also differs in its
build ID, with retained/new modes 0664/0644; the original umask was not pinned.

No target hashes or output bytes were patched. The failed build source/object tree is now retained in
[verified archives](evidence/failed-kernel-source-retention-20261106.json); all
final artifacts and evidence remain in their original output directory. Sampled minimum available RAM was 7,085,875,200 bytes;
end free disk was 14,032,269,312 bytes. Another fresh build must first satisfy
the unchanged greater-than-15-GB disk guard.

## Verified vDSO experiment and full-kernel opt-in

At project commit `158a45a379017e3aa238cc18290db07dd0a099cc`, the fresh private
vDSO experiment completed from 10:35:29 to 10:35:52 UTC. The complete 3,296-byte
ELF matches SHA-256
`f6b2803e8e4a17782f4d0cf089366c3a06f2bf2284ff245212ee2ac378168f94`.
The target is authenticated through its unique unchanged embedding in the
pinned baseline Image. Its GNU build ID and optional debug ELF also match; the
latter is an unanchored comparison. Independent review rehashed all 2,221
manifest entries and verified actual compiler/assembler commands and debug paths.

The command ran in `/var/lib/a733-kernel-replay-work-20261106` on the builder:

```sh
python3 -I -B build/vdso-replay.py --target diagnostic-20261104 \
  --builder-profile replay-20261106 \
  --output "$PWD/out/vdso-replay-debugmap-20261106-01"
```

The full-kernel recipe now has an explicit `--debug-path-profile original-20261104`
opt-in. It maps private compiler and assembler debug directories to the original
recorded directory and pins umask 0002. Native mode remains the default and pins
umask 0022 without mappings. Both modes retain the same complete Image/DTB/config
hash gates, private source checkout, two jobs and greater-than-15-GB start guard.
Actual Kbuild commands and generated metadata are retained. Caller umask is
restored on success and failure. No output bytes or target hashes are patched.

## Successful full mapped replay

The [complete replay](evidence/kernel-replay-mapped-20261106.json) ran from
10:45:36 to 11:01:25 UTC at clean project commit
`2d20b25b53de2c1eb3f5fc0b250d9cb55608b229`. On the builder, in
`/var/lib/a733-kernel-replay-work-20261106`, this command passed:

```sh
python3 -I -B build/kernel-replay.py --target diagnostic-20261104 \
  --builder-profile replay-20261106 \
  --debug-path-profile original-20261104 \
  --output "$PWD/out/kernel-replay-mapped-20261106-01"
```

The Image is 27,283,464 bytes, SHA-256
`9df974c55fa61a77e8fe812652780dad471527eb9615c27838f481887d4c50e7`.
DTB and configuration also match their committed byte hashes. Both original
kernel/vDSO build IDs are restored naturally by compilation; no target bytes
were patched. Independent review rehashed all 2,556 manifest entries and checked
actual command mappings, source snapshots and the complete 842-package inventory.
All 849 privileged tests, four assembly cases and five shell regressions passed.

The independent module audit authenticates the original 3,650,395-byte initramfs
at its committed SHA-256, then compares all 332 regular files: 319 modules and
13 metadata files. Every path, byte hash and permission mode matches the replay,
with no missing/extra regular files. Its canonical inventory digest is
`3244bd1cd0573d429a51d98d90c0cdfc3dfa2b988331d4cf632f26b889d98455`.
The runtime recipe's separate directory comparison remains labeled unanchored.

Sampled minimum available RAM was 6,971,584,512 bytes and minimum free disk
10,012,741,632 bytes. This establishes artifact reproduction on the prepared
builder with reused pinned source caches. Independent source acquisition,
a fresh immutable builder, whole-image assembly and physical release acceptance
remain separate. The deployment images were not replaced.

## Fresh-source reproduction in the frozen environment

[The completed fresh-source replay](evidence/frozen-kernel-replay-20261106.json)
reproduces the authenticated Image, DTB and configuration exactly. Independent
review also authenticates the original compressed initramfs and compares all
332 regular module files: paths, permission modes, lengths, hashes and actual
bytes match. All 2,556 manifest entries pass rehashing with no missing or extra
files. This does not assert equality of generated directory metadata.

The frozen OS and independently imported source home were read-only; a new
project/output tree provided writable build space. Three original Image/DTB/
config files served as authenticated comparison baselines. Original object
files and the modules directory were not copied. The recipe's optional retained
modules comparison is therefore null; the separate initramfs audit supplies the
stronger anchored check. Generic reused-cache/prepared-builder wording in the
recipe result is interpreted alongside the linked fresh-source and frozen-OS
records; the acquisition boundary differs from the earlier prepared-host runs.

Both input snapshots and the exact 842-package profile match before and after
execution. Two-job compilation used original debug-path mapping and umask 0002;
all four captured compiler/assembler command files contain the expected maps.
Resource minima were 10,134,110,208 bytes disk free and 6,483,652,608 bytes RAM
available across inner/outer sampling. The namespace exits with no mapped UID
processes left. Complete native artifact/log copies pass hash and fsync checks.

[Capacity recovery](evidence/kernel-space-retention-20261106.json) preserved both
builder OS trees and current deployment images. The old source cache restores
on codex with complete archive comparison and standalone Git object checks.
Whole-image assembly/reproduction, firmware licensing and physical release
acceptance remain open. This run establishes neither two independent OS
constructions nor actual KVM guest execution.

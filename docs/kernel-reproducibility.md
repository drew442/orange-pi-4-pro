# Kernel artifact reproduction

The separate `build/kernel-replay.py` recipe rebuilds the retained A733 kernel
in a fresh private source checkout. It leaves the published installation images
and original cached build tree in place. The [actual private build](evidence/kernel-replay-20261106.json)
completed compilation but **failed exact Image reproduction**. The device tree
and configuration match their targets. No successful kernel replay is claimed.

The target is the `Image`, board DTB and final kernel configuration pinned in
[evidence/diagnostic-20261104.json](evidence/diagnostic-20261104.json). The source
is kernel commit `2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f`; the starting
configuration comes from the pinned vendor commit's
`external/config/kernel/linux-sun60iw2-current-a733.config`. The recipe applies
the same KVM/configuration settings and build metadata as `build/diagnostic.sh`.
It uses two compilation jobs and the explicit complete
`replay-20261106` builder package profile. Compiler and full inventory checks
remain mandatory before and after compilation.

The source caches are registered at `/home/drew/orange-pi-4-pro-build` on the
Ubuntu 22.04 builder. Both vendor and kernel caches are deliberately shallow
checkouts of their pinned commits; this property is recorded. The private kernel
checkout shares the cached Git object database, then checks out the pinned
commit into new files. Existing object files and the original `.config` are
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
and records their relative paths, modes, sizes and SHA-256 hashes. Its comparison
with the retained modules directory is explicitly **unanchored**: the existing
committed diagnostic evidence does not contain a modules-tree hash. That
comparison must not be presented as authenticated module reproduction.

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

The successful vDSO result supports testing this profile against the full kernel;
it does not establish that the complete Image or all modules reproduce.

A [full mapped replay](evidence/kernel-replay-mapped-start-20261106.json) is now
compiling from clean project commit `2d20b25b53de2c1eb3f5fc0b250d9cb55608b229`.
Its preflight/configuration checks and all 849 privileged discovery tests pass;
final Image/DTB/config and module comparisons are pending. The earlier failure
remains a failure, with its source tree retained in verified archives.

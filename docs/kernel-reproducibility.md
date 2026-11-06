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
producer strings. A fresh private vDSO-only experiment with explicit debug-path
mapping is the next test against unchanged original bytes. This cause has not
yet been demonstrated by rebuilding. One stripped module also differs in its
build ID, with retained/new modes 0664/0644; the original umask was not pinned.

No target hashes or output bytes were patched. The complete failed build and
evidence remain available. Sampled minimum available RAM was 7,085,875,200 bytes;
end free disk was 14,032,269,312 bytes. Another fresh build must first satisfy
the unchanged greater-than-15-GB disk guard.

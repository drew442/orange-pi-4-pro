# Kernel artifact reproduction

The separate `build/kernel-replay.py` recipe rebuilds the retained A733 kernel
in a fresh private source checkout. It leaves the published installation images
and original cached build tree in place. Actual execution results will be
recorded separately; the presence of this recipe is not a successful rebuild.

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

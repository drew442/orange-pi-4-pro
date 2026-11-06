# Clean-builder acquisition preparation

The prepared Jammy host reproduces the retained U-Boot and kernel artifacts.
That does not establish a newly acquired immutable builder. The
[read-only APT audit](evidence/builder-package-availability-20261106.json)
accounts for all 842 exact package/version/architecture tuples without changing
installed packages or repository indexes. All 86 declared build packages and
both cross compilers have traceable download records in the existing cache.

The [binary archive pins](../configs/builder-package-archives-20261106.lock.json)
record all 842 tuples. For 841, the pins came from cached APT metadata. The
[independent signature-chain audit](evidence/builder-index-signatures-20261106.json)
now verifies all three release signatures, seven index hashes and 1,200 matching
records covering those exact 841 packages. Actual archive acquisition remains
separate from that metadata proof. Full raw
metadata is retained outside Git at the hash/path in the audit receipt; the
concise receipt retains source/index hashes and the original lookup gap.

The sole status-only version was `xfsprogs` 5.13.0-1ubuntu2.1. It was subsequently
[acquired and authenticated](evidence/builder-xfs-acquired-20261106.json) from the
Ubuntu snapshot at `20261001T000000Z`: signed InRelease, both index hashes,
exact version/architecture stanza, then complete archive size and SHA-256.
The 870,292-byte archive SHA-256 is
`efe2119fec8c0cc7010afbf036b3b6aa60fa2d546e3adb5abe881ff3dac63634`.
No package was installed or inventory lock relaxed. The
[Ubuntu Snapshot service](https://snapshot.ubuntu.com/) supports retrieving
historical archive contents; do not change the active builder's APT sources
while a replay is enforcing its exact before/after inventory.

The separate `build/fetch-builder-packages.py` recipe uses the already pinned
aria2 1.36.0 on the Jammy builder, with two transfers, TLS checks, redirects
disabled and per-file SHA-256. It independently rehashes the complete cache
before writing a success receipt. Outputs are new private
`out/builder-packages-NAME` directories; explicit `--resume` requires the original
clean commit/input binding. Completed corruption is refused and bound aria2
partials may resume. Interruption stops the private downloader process group
before releasing the cache lock. Installed package inventory is checked on
both sides; packages are never installed or extracted.

Next, complete archive acquisition, select and pin a clean Jammy base and
construct an isolated builder from the exact inputs. Then run
the reviewed build recipes and compare complete artifacts. The new root/image
digest, commands and end-to-end results must be recorded before claiming clean
builder reproducibility. These acquisition pins are preparation, not that proof.

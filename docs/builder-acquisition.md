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
records covering those exact 841 packages. The
[complete acquisition](evidence/builder-packages-acquired-20261106.json) now passes
for all 842 archives, with independent rehashes on both hosts. Full raw
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
aria2 1.36.0 on the Jammy builder, with two transfers, fixed initial HTTPS
origins, TLS checks and per-file SHA-256. It retains aria2's native redirect
behavior; torrent/metalink following is disabled. The [actual offline option
probe](evidence/builder-fetch-option-probe-20261106.json) validates the complete
argument set before transfer. It independently rehashes the complete cache
before writing a success receipt. Outputs are new private
`out/builder-packages-NAME` directories; explicit `--resume` requires the original
clean commit/input binding. Completed corruption is refused and bound aria2
partials may resume. Interruption stops the private downloader process group
before releasing the cache lock. Installed package inventory is checked on
both sides; packages are never installed or extracted.

The actual acquisition ran at clean commit
`5499a8a09850d00a3420fefd565cf8bbc1b75519`, in
`/home/drew/orange-pi-4-pro-build/builder-package-acquisition-20261106`:

```sh
python3 -I -B build/fetch-builder-packages.py \
  --output "$PWD/out/builder-packages-20261106-02"
```

All 842 archives total 983,768,778 bytes. Complete copies remain in that builder
output and at `codex:/home/drew/orange-pi-4-pro/out/builder-packages-20261106/`.
Its 441,044-byte receipt has SHA-256
`5df679ff656a69d0204d6ba43c80549b86466a2296e8d5985de0736f21e8ad54`.
Installed packages and APT state were unchanged. Lock provenance labels record
discovery-time status; the later signature/acquisition receipts establish the
completed checks. No downloaded package was installed or extracted.

## Selected build seed and remaining construction

The [pinned seed](../configs/builder-base-20261106.lock.json) is the official
[Ubuntu Base 22.04.5 AMD64 archive](https://cdimage.ubuntu.com/ubuntu-base/releases/22.04/release/),
29,832,285 bytes, SHA-256
`242cd8898b33ea806ef5f13b1076ed7c76f9f989d18384452f7166692438ff1a`.
Its detached manifest signature verifies against the trusted Ubuntu CD-image
key. Read-only tar inspection found 101 installed package names, all present in
the target inventory. The archive remains at `out/builder-base-20261106/` on codex.
It is an AMD64 builder seed, not an Orange Pi installation image. It has not been
extracted into an operating root or executed.

[Capacity planning](evidence/clean-builder-capacity-20261106.json) records about
9.0 GB free after acquisition and 3,752,392 KiB of reported installed package
files. Recover verified generated-cache headroom before constructing the root;
target at least 25 GB free as a project planning allowance for root/source/output
staging. The existing kernel and NVMe assembly guards remain unchanged.

Next, construct a fresh isolated Jammy root with the exact package versions,
controlled maintainer scripts/services and no physical device access. Pin the
final builder filesystem digest, independently acquire sources/toolchains, and
run the reviewed build recipes in fresh environments. Record commands and
end-to-end comparisons before claiming clean-builder reproducibility. No such
constructed environment or fresh-builder execution is claimed here.

# Production-size time-enabled A/B roots

The [construction and readback evidence](evidence/production-host-roots-20261107.json)
records two actual **4 GiB** root components built in the frozen Ubuntu 22.04
namespace. Both pass complete filesystem readback. This is one construction;
the matching [8 GiB state](production-host-state.md) now also passes construction
and complete readback, and the [matching bootstrap](production-host-bootstrap.md)
passes construction and inspection. The [full NVMe candidate](production-nvme-composition.md)
now also passes software checks. A [second root construction](production-host-roots-repeat.md)
passes full readback with identical contents and metadata except 463 mtimes per
slot; raw/gzip bytes differ. Downstream repeats remain open.
Published deployment images are unchanged, and the
[existing-install repair](installed-host-repair-20261107.md) remains the next board
action. These components are not a replacement SD or whole-drive image.

## Pinned inputs and isolation

Producer revision `6f2477be759b28829041f70e514dbdf678bb1a13` uses the explicit
`timesync-20261106` userspace profile and `fakeroot-raw-xattrs-v1` metadata
backend. Historical baseline/default profiles are unchanged. The authenticated
138,093,095-byte userspace archive contains 221 configured packages. Four original
diagnostic inputs supply the compatible Image, DTB, kernel config and module
archive; all four match their locked hashes. A different initramfs in an older
provenance directory was detected before staging and was not accepted.

The launcher validates all **575** tracked source records against Git, retains a
bundle, copies authenticated inputs into a read-only capsule and checks out the
exact revision inside a new writable work directory. The existing frozen root,
private UID range/network, capability drops, firmware mask and initial probe
remain unchanged. Fakeroot spans each whole root recipe; no real block or
character node is created. The production slot sizes, UUIDs and free-space guards
are unchanged.

The executed launcher was:

```sh
sudo python3 -I -B \
  /var/lib/a733-host-roots-project-20261107-02/project/build/launch-host-roots.py \
  --project /var/lib/a733-host-roots-project-20261107-02/project \
  --tag timesync-01 \
  --diagnostic /home/drew/orange-pi-4-pro-build/artifacts-first-candidate \
  --userspace /var/lib/a733-clean-replay-20261106/userspace-timesync-account-dates-01/work/export-timesync-result/rootfs.tar.gz
```

The same command with `--plan` completed first without creating the phase or
accessing physical builder inputs. The existing tag now refuses reuse. A repeat
requires a fresh tag, committed source and enough capacity; never overlay an old
attempt. Failure retains partial outputs and a receipt. Handled interrupts are
ignored during child termination/final checks, then prior signal handling is
restored.

## Outputs and complete readback

Builder phase:
`/var/lib/a733-clean-replay-20261106/host-roots-timesync-01`.
Its roots are under `work/project/out/host-payload-timesync-01`. All **86** payload
files pass hash authentication. The filesystem superblocks have the fixed A/B
UUIDs, read-only feature and no journal. Each raw image is 4,294,967,296 bytes;
each gzip is about 143.47 MB. Exact hashes and tool versions are in the evidence.
The images contain 332 matching kernel modules, four management files and the
closed 40-file maintenance runtime, including the v2 time-state policy.

Before and after each original filesystem creator call, the wrapper captures the
same complete staging namespace, metadata, content hashes, links, device IDs and
raw attributes. These producer snapshots remain outside the deployable payload.
The independently reviewed verifier authenticates the producer receipt, payload
manifest and snapshot pins, then reads the image through a held regular-file
FD. It invokes read-only `debugfs` with numeric inode references; it never mounts,
repairs or writes an image. Use a trusted root-owned output parent as below.

```sh
sudo python3 -I -B /var/lib/a733-root-readback-controller-20261107/verify-host-roots.py \
  --receipt /var/lib/a733-clean-replay-20261106/host-roots-timesync-01/receipt.json \
  --receipt-sha256 dfcebe53d55dfcef53fcfee4bb88bd0db6a751ac3257163ceed742a6fb8904c4 \
  --output /var/lib/a733-host-roots-readback-20261107-01
```

That run completed. Each slot matches **16,367** expected paths, 12,699 unique
regular inodes totaling 451,236,156 bytes, 1,854 symlinks, eight character-device
records and one raw capability attribute. Only an empty, root-owned `0700`
`lost+found` is additional. Hardlink topology and all short/long symlink targets
match. Creator semantics retain child modification times at whole-second
resolution; root time comes from each actual superblock. The
[pinned creator trace](root-reconstruction-preparation.md) explains that policy.
Atime, ctime and crtime are outside the comparison. Input identities and hashes remain unchanged.

Native records and all 84 non-raw payload files are retained in
`out/host-roots-timesync-01-20261107`; raw images remain on the builder. Independent
artifact review rehashes every retained file, streams both gzips to their exact
raw hashes, corroborates superblocks and reconstructs metadata inventories from
retained debugfs logs. Full byte/long-target/xattr-value comparison is reviewed
verifier-execution evidence; the reviewer did not open native raw images.

## Capacity and remaining gates

The initial budget was 18,987,439,372 bytes, including the unchanged root recipe
budget and capsule/Git staging. Sampled minima were 22,357,508,096 bytes free disk
and 7,217,774,592 bytes available RAM. The host's 842-package inventory is unchanged,
with no remaining mapped processes/mounts or real device nodes in the work tree.
The retained work allocates 1,225,961,472 bytes; each raw root allocates 444,424,192.

After this root readback, free space was 22,813,622,272 bytes,
**808,697,856 bytes below** the unchanged 22 GiB final NVMe staging guard.
Subsequent state/bootstrap construction and verified retention enabled the
[completed full NVMe run](production-nvme-composition.md), which records current
capacity. No additional LV growth is authorized.

The final Ubuntu test suite runs 1,491 cases with 112 environment skips and no
failures; five shell suites and four assembly cases pass. Three mutation fixtures now explicitly
change mtime to avoid within-tick filesystem timestamp assumptions. Runtime
checks and pins are unchanged. Software tests and these component artifacts do
not establish installed networking/time, KVM guest execution, SPI boot, zero
routine SD writes, endurance, rollback/recovery or independent deployment.

The [November 8 encoded retention](component-retention-20261108.md) now retires
only this construction's two superseded builder raw images. Matching gzip files
remain on both hosts, with complete expansion proofs, independent review and an
actual fresh-path A recovery rehearsal. Restore a needed raw path using that
guide before invoking a historical consumer. Original artifact evidence remains
unchanged; current installed and corrected-generation images are separate.

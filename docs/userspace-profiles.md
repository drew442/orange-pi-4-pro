# Authenticated userspace profiles

Root and state builders accept `--userspace-profile`; omitting it preserves
`baseline-20261104`. Selection is explicit and never changes published deployment
images. Continue using [the personalized deployment guide](deploy-personalized-20261106.md)
for the board installation.

| Profile | Compressed bytes | SHA-256 |
| --- | ---: | --- |
| `baseline-20261104` (default) | 169,852,145 | `1967e76c0c831c1c0bab432e7116457d4372c15b413090c054f8958b72dcb392` |
| `normalized-20261106` (opt-in) | 138,025,729 | `98d6a57bbb70ff55bef32b6bd051ebfcb6bc0fad4f6c8b3167e8619b5a9d961e` |

The normalized archive comes from the [fresh raw-package pipeline](userspace-fresh-pipeline.md)
and matches the independently checked [normalized pair](userspace-export-reproducibility.md).
Both profiles contain the same locked 220-package inventory. This selection does
not add automatic time synchronization or change the package/state ABI.

## Build interface

Append `--userspace-profile normalized-20261106` to either documented builder
command in [root payloads](host-payload.md) or [state payloads](host-state.md),
using the normalized archive as `ROOTFS_TAR`. Use matching profile selections for
root and state. `--metadata-profile fakeroot-raw-xattrs-v1` remains a separate,
explicit metadata backend choice; it does not select archive bytes.

The retained normalized archive on codex is
`out/userspace-export-fresh-20261106/rootfs.tar.gz`; on the builder it is
`/var/lib/a733-clean-replay-20261106/userspace-foreign-recipe-01/work/export-fresh-01/rootfs.tar.gz`.
Check its size/hash before use. Follow the existing builder isolation, output,
configuration, selection and free-space requirements. Production slots remain
4 GiB and state remains 8 GiB; the small extraction probe below does not waive
production capacity checks.

[The resolver](../build/userspace-profiles.py) pins
[the profile lock](../configs/host-userspace-profiles.lock.json), authenticates
selected evidence and producer sources, and checks normalized pipeline success,
archive/BOM/inventory/epoch agreement. Bounded, no-follow reads reject malformed,
duplicate or mutated inputs before output creation. Builders authenticate the
archive and copied provenance against the resolved snapshot before extraction.
Normalized manifests and root markers include `sources.userspace_profile` with
archive, lock and resolver bindings. Baseline archive binding remains unchanged.

The resolver and its lock are source-side build inputs, imported lazily. They do
not enlarge the closed 39-file installed maintenance tree. Selected source
provenance is retained with build outputs, distinct from installed runtime files.

## Verification and limits

[Recorded evidence](evidence/normalized-userspace-profile-20261106.json) binds
source commit `4d8ddd1`, retained harness code, receipts, resource samples and
independent Sol/high review. The actual frozen-builder component probe resolved
both builder profiles, rejected the normalized archive under the baseline pin,
reauthenticated all 12 provenance files and extracted all 15,876 base entries
with complete metadata checks. It verified 220 exact installed packages, staged
four management and 39 maintenance files, and created/validated all eight
package-state components. No actual device nodes remained outside fakeroot.
Virtual metadata checks apply inside that session.

The successful run sampled 13 resource points: minimum free disk 3,290,382,336
bytes and available RAM 7,097,884,672 bytes. Its extraction-only estimate included
actual unpacked content, 64 MiB metadata allowance and a 3 GiB reserve. Production
image capacity guards remain unchanged. Before/after outer host inventories
match the replay lock; the probe did not invoke production tool-package
validation. Five outer versions differ from the older builder inventory; that
production validator selects only e2fsprogs/libext2fs2/tar/gzip.

Two unsuccessful harness attempts remain recorded: a missing bind destination
prevented payload startup; then omitted management staging prevented the
package-state snapshot after extraction. The corrected run uses production
staging order. An independently reviewed, exact-path/BOM-checked cleanup removed
only the second attempt's generated extraction tree, retaining its inputs and
receipts.

Native discovery passes 1,145 cases with 96 environment skips; four assembly
cases and all five shell suites pass. Independent focused review ran 92 cases
with two environment skips and checked relocated runtime imports without the
source-only resolver.

No production root/state/NVMe images were built by this probe, and no physical
boot, SSH login, KVM guest or SD-write acceptance is established. Complete
production composition and independently reproduced deployment remain open.
The [tracked outer launcher](userspace-pipeline-launcher.md) now passes review,
plans and capacity refusal; its complete execution remains pending. Builder
capacity after its protected source staging is about 3.28 GB free; the
[reviewed growth proposal](build-host-capacity-plan.md) still awaits authorization.

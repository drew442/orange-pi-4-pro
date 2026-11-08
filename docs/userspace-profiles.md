# Authenticated userspace profiles

Root and state builders accept `--userspace-profile`; omitting it preserves
`baseline-20261104`. Selection is explicit and never changes published deployment
images. Continue using [the personalized deployment guide](deploy-personalized-20261106.md)
for the board installation.

| Profile | Compressed bytes | SHA-256 |
| --- | ---: | --- |
| `baseline-20261104` (default) | 169,852,145 | `1967e76c0c831c1c0bab432e7116457d4372c15b413090c054f8958b72dcb392` |
| `normalized-20261106` (opt-in) | 138,025,729 | `98d6a57bbb70ff55bef32b6bd051ebfcb6bc0fad4f6c8b3167e8619b5a9d961e` |
| `timesync-20261106` (opt-in) | 138,093,095 | `0ead62c982344bfa3a796027e2f07ce0d8402e7033122707e01a9a3f8860f248` |

The normalized archive comes from the [fresh raw-package pipeline](userspace-fresh-pipeline.md)
and matches the independently checked [normalized pair](userspace-export-reproducibility.md).
The baseline and normalized profiles retain their locked 220-package inventory;
the time profile has 221 packages and adds systemd-timesyncd plus its UID/GID 996
identity. The time profile is source-integrated as an opt-in. Omission still
selects the unchanged baseline, and published deployment artifacts are unchanged.

## Build interface

Append `--userspace-profile normalized-20261106` or
`--userspace-profile timesync-20261106` to either documented builder
command in [root payloads](host-payload.md) or [state payloads](host-state.md),
using the archive selected by that profile as `ROOTFS_TAR`. Use matching profile
selections for root and state. `--metadata-profile fakeroot-raw-xattrs-v1` remains a separate,
explicit metadata backend choice; it does not select archive bytes.

The retained normalized archive on codex is
`out/userspace-export-canonical-01-20261106/rootfs.tar.gz`; on the builder it is
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
Normalized and time-profile manifests/root markers include `sources.userspace_profile` with
archive, lock and resolver bindings. Baseline archive binding remains unchanged.
The time profile authenticates its single complete construction/readback record
and 21 producer inputs; the global profile lock is pinned separately to avoid
a self-reference. Its component staging retains 25 provenance files, including
the clock helper. A second time-profile construction is still required.

The resolver and its lock are source-side build inputs, imported lazily. Selected
source provenance is retained with build outputs, distinct from installed runtime
files. The time profile adds a helper to the installed maintenance package, which
now contains 40 files from 39 source files. Its roots require management profile
`a733-management-v2-timesync`; old management tools reject v2, while the new
tools accept v1 for rollback.

## Verification and limits

[Normalized-profile evidence](evidence/normalized-userspace-profile-20261106.json) binds
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

[Time-profile archive evidence](evidence/timesync-execution-20261107.json)
records one actual frozen-builder configuration/export and independent complete
archive readback: 221 packages, 15,909 members, archive size/hash above. The
[integration component evidence](evidence/time-profile-integration-20261107.json)
records extraction, eight package-state components, four management files, 40
maintenance files, and a 256 MiB nondeployable state fixture. These are
component checks, not complete production roots/state/NVMe images. No physical
boot, SSH login, KVM guest or SD-write acceptance is established. A second full
time-profile construction and complete production image reconstruction remain open.
The published installer/host SD images and personalized NVMe bundle are unchanged.
The tracked baseline pipeline is complete. The time-profile component run sampled
at least 4.35 GB free; full production image capacity guards remain separate.
The [reviewed growth proposal](build-host-capacity-plan.md) still awaits
authorization.

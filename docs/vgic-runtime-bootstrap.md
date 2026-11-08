# VGIC derivation for the corrected bootstrap

The [corrected host boot](evidence/corrected-host-boot-20261108.json) and
[restored KVM guest](evidence/corrected-host-guest-restore-20261108.json) now pass.
The construction-time UNBOOTED classification below is superseded by those
physical records; [full acceptance remains open](endurance-run-20261108.md).

`build/vgic-runtime-bootstrap.py` derives a regular-file 256 MiB bootstrap for
corrected state **360dfb39-f49f-487e-b307-0bb887c854c5**, using explicit new
profile **experimental-el2-vgic-v1**. The sole DT semantic change adds
`interrupts = <1 9 4>` at `/interrupt-controller@3400000`. Firmware, complete
first 32 MiB, `uImage`, `uInitrd`, `boot.cmd` and `boot.scr` must remain byte
identical. NVMe-first firmware integration is a separate future step.

The base is the [corrected bootstrap](production-host-bootstrap-runtime-fixes.md)
from source `0a3d2a1ff78108e445d1c832362b2667897a9c9e`, authenticated by
[its evidence](evidence/production-host-bootstrap-runtime-fixes-20261108.json):

| Input | Bytes | SHA-256 |
| --- | ---: | --- |
| Original raw image | 268435456 | `1063c591c3955e1e8531c8b22960bc09eb080024a6e79d331846572c3e0d2f76` |
| Original manifest | 8794 | `23d80141db14245d3f832440416131e818fba7b73f1a64a3147365a60a8007c2` |
| Original DTB | 209320 | `4d7d964a54668cf5dc12cd4bf7e72a625c79883541272aeba1a91adbbedb86ed` |
| Required patched DTB | 209344 | `1ba8f5926c0d76d12479bafcfcfce66be4a934ccfb6a5bcbcc7f7aa86c07f9f4` |

The original builder outputs are under
`/var/lib/a733-clean-replay-20261106/host-bootstrap-timesync-runtime-fixes-01/work/project/out/host-sd-timesync-runtime-fixes-01`.
The original manifest must come from that output, without rewriting any fields.
The existing old `00fd…` VGIC recipe and global profile registrations are unchanged.

Run from a committed checkout on the prepared Ubuntu 22.04 host, with an unused tag:

```sh
python3 build/vgic-runtime-bootstrap.py --name corrected-01 \
  --image /ABSOLUTE/BASE/a733-host-sd.img \
  --manifest /ABSOLUTE/BASE/manifest.json
```

Two actual constructions from committed source `18af215` now pass, with byte-identical
raw images, gzip files and `uInitrd`. Independent source and artifact reviews pass.
The privileged Jammy suite passes **1,814 tests**, five shell suites and five
diagnostic assembly cases, without skips, errors or failures. See the
[construction record](evidence/vgic-runtime-bootstrap-20261108.json).

| Output | Bytes | SHA-256 |
| --- | ---: | --- |
| Raw bootstrap | 268435456 | `f02c74b4c001f1608d0005b6ff23fe82bffa8c02f71e19e5b6573819125ae383` |
| Gzip | 17271954 | `fbfa9f4eae5913a61768e90fb62b2a3f48df9a2d38aed5b7380b83873f5307b6` |

Builder outputs are in
`/var/lib/a733-vgic-runtime-bootstrap-project-20261108-01/project/out/host-sd-vgic-runtime-corrected-01`
and `-02`. Codex retains the first encoded image, manifest, original manifest,
initramfs and assembly log under `out/vgic-runtime-bootstrap-20261108/retained-01/`;
the raw files remain on the builder. The artifact review independently compares
complete raw/gzip streams, firmware prefix, extracted files and DT semantics.
Physical boot and matching full-NVMe integration remain pending.

This is a file packager, without namespace producer execution, mounts, device
writes, source downloads or dependency installation. Selected package versions
must match the original lock, including `device-tree-compiler` **1.6.1-1 amd64**.
Actual invoked tool version output and local recipe hashes are recorded.

Use a trusted quiescent checkout and inputs. Inputs require absolute paths,
regular files, single links and no symlink ancestors; `/dev`, `/proc`, `/sys`
and parent traversal are refused. Manifest and raw image size/hash pins are
mandatory. File descriptors remain held; identity, path identity and complete
hash are rechecked before the final manifest is written. This detects ordinary
replacement/mutation; the recipe does not claim isolation against a hostile
concurrent process controlling the checkout or its ancestors.

Admission requires **1 GiB scratch plus a retained 256 MiB free-space floor**.
The native checkout with about 311 MiB free must not build this image. Use the
builder with sufficient measured headroom. Tests use small temporary fixtures
and never build a native full-size image. Output is atomically reserved as
`out/host-sd-vgic-runtime-NAME/`; failures consume the tag and retain logs.
Progress goes to stderr and successful stdout is the complete JSON manifest.

The packager calls only pure helpers from `vgic-host-sd.py` and the existing
`assemble-diagnostic.sh` pipeline. It does not call the old recipe's `build()`
or select its original installed state. A binary FDT parser checks every node,
property, reservation and boot CPU, allowing only the new GIC property. Fresh
image extraction checks all preserved files, the exact patched DTB pin and the
complete first 32 MiB. The MBR remains `0xa7330001`, with one bootable Linux
partition at sector **65536**, size **458752** sectors. The 268435456-byte raw
image fits the actual **1977614336-byte SD**.

Outputs include the raw image, gzip, retained `uInitrd` for a future matching
NVMe consumer, `assembly.log`, `manifest.json` and the **byte-exact original**
`base-bootstrap-manifest.json`. Gzip uses level 9, zero mtime and no filename;
its complete expansion is verified. This promises deterministic compression
of identical input bytes, not independent whole-image reproducibility.

Current top-level recipe/file/DTB metadata describes the derivation. Complete
historical producer provenance and its old boot profile stay under
`base_bootstrap.original_manifest`, with the original raw manifest pin and
retained file. The old producer never claims to have built the changed bytes.
Root A/B embedded source DTB pins remain the original DTB; roots are untouched.

[Physical VGIC and actual KVM CPU/disk/network/shutdown/restart evidence](kvm-vgic-results-20261108.md)
exists for the diagnostic installed host. **This new corrected state360d
artifact remains UNBOOTED**, with `hardware_acceptance: false`. Do not flash
this bootstrap alone onto the existing `00fd…` installation. Independent
artifact review and separately matched integration remain coordinator gates.
The A733, 2 GB SD, nominal 250 GB NVMe, minimal host and zero routine SD write
requirements remain in force. Final hardware acceptance requires the corrected
host and an actual KVM guest; host tests cannot substitute for it.

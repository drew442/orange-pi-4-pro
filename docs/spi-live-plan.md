# Read-only live SPI comparison

`scripts/plan-spi-nvme.py` compares the actual retained SPI candidate with two
fresh complete reads of the running A733's `/dev/mtd0`. It supplies an immutable
comparison and prospective full-erase-unit byte plan on stdout. It contains no
programmer, erase operation, write mode or output-file creation. The authenticated
backup helper supplies read-only opening, sysfs/JEDEC/geometry checks, no-swap
checks and the read-only MEMGETINFO ioctl. Read progress goes to stderr.

This tool is limited to the [actual original-backup candidate](spi-original-backup-20261108.md).
The [actual live comparison](spi-live-plan-results-20261108.md) now passes;
its scope is limited to the observed read-only match.
It pins the entire candidate manifest, candidate image, original baseline and
physical sibling `backup-spi.py`. The fixed manifest in turn binds the backup
manifest, component parent and registry, source provenance, replacement ranges,
preservation ranges and all 256 original/candidate erase-unit hashes. There is
no CLI fixture or alternative-pin mode.

The exact production pins are:

| Input | SHA-256 |
| --- | --- |
| `backup-spi.py` | `b160e3fe8e421e18e35aad34aff909e10b4dfca027178d80a4b202c711e2147d` |
| `manifest.json` | `d1444bb4f0c16d9495e125d3db4b7e7e130af294106f356c850d0e0b8fb31ff0` |
| 16,777,216-byte candidate | `83edcd7f448e8353163666016a6bae3e9b70d041a5e4d8454f48931839205e90` |
| 16,777,216-byte live baseline | `dffab0dd410657cb30c7b2fd7f2586a4792e8472e58882b3532581f8111a646d` |

## Trusted staging and invocation

Run as root with isolated Python and bytecode writing disabled. Stage the reviewed
planner, unchanged pinned backup helper and actual candidate inputs in trusted
root-owned directories on `/run` tmpfs. Every ancestor must be nonsymlink and
not writable by group or others; immediate input parents must be mode 0700.
All three opened input files (helper, candidate, manifest) must be root-owned,
single-link regular files with mode 0400. The helper must be the planner's
physical sibling. Staging and logging are the caller's responsibility; the
planner itself creates no files. Keep swap disabled and the staged tree quiescent.
The execution source and interpreter must be trusted separately.

For the coordinator's private staging layout, the read-only command is:

```sh
sudo python3 -I -B \
  /run/a733-spi-live-plan-20261108-01/scripts/plan-spi-nvme.py \
  --device /dev/mtd0 \
  --candidate /run/a733-spi-live-plan-20261108-01/inputs/a733-spi-nvme.bin \
  --manifest /run/a733-spi-live-plan-20261108-01/inputs/manifest.json
```

The tool holds file and ancestor descriptors through verification and rechecks
path bindings, file identities and complete input contents before publishing.
It authenticates the helper bytes before executing them directly, avoiding a
path-based import or bytecode cache. It rejects symbolic links, hard links,
noncanonical paths, mutable permissions, size drift and incomplete manifests.

The live identity must exactly match the manifest, including `/dev/mtd0`, `90:0`,
XM25QU128C, JEDEC `204118`, full 16 MiB geometry, sysfs path, partition map and
MEMGETINFO result. Checks before, between and after the complete reads bind the
open character FD to the current device path. Both reads must match the pinned
full baseline and every original unit hash. The current boot ID must remain
unchanged. Preserved bytes are also compared directly against the candidate.
Failures produce no success JSON.

## Meaning and limits of the result

The candidate replaces only `[0,237568)` and `[262144,1654784)`. The plan lists
changed units 0–25: **1,703,936 full-unit bytes**. The preserved gap, partial
unit tail and entire remainder are verified. Units 26–255 are skipped and must
remain untouched by any future independently reviewed programmer. The output
contains all 256 original/candidate unit hashes, exact ranges, current boot ID,
live identity and hash, and candidate/manifest/helper pins. It is a point-in-time
comparison; it does not reserve or lock the flash against another privileged
actor. Any future programming must revalidate its own live inputs and target.

`physical_writes`, `recovery_proven`, `deployment_authorized`,
`hardware_acceptance` and `programmer_available` are always false. Successful
host tests establish rejection behavior, not physical device validation. A
successful live comparison would establish only the observed read-only match.
It does not prove independent recovery, U-Boot NVMe loading, a safe programmer,
SPI-to-NVMe boot, SD-removed boots, zero routine SD writes or complete real-KVM
acceptance. Those physical gates and exact-target yes/no authorization remain
with the coordinator. Preserve the A733, 2 GB SD, nominal 250 GB NVMe and minimal
host requirements.

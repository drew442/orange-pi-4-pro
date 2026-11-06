# Replay ARM userspace configuration

The [tracked configuration run](evidence/userspace-configuration-recipe-20261106.json)
passes on `build.drewnet.online` from commit
`02bb4084bf9135a53dd38ffeaab7c1ac4399ba64`. It freshly configures a clone of the
retained authenticated foreign-stage tree. This repeats ARM configuration; the
subsequent [fresh pipeline](userspace-fresh-pipeline.md) also repeats foreign
construction from raw packages and matches the normalized exports. Complete
deployment reproduction remains open. Physical installation and KVM guest acceptance remain pending.

## Prepared environment and inputs

First reproduce the [frozen Ubuntu 22.04 AMD64 builder](clean-builder.md) and
[authenticated mirror/foreign stage](offline-userspace-reconstruction.md).
The launcher deliberately uses their recorded fixed paths:

- Frozen OS: `/var/lib/a733-frozen-builders/freeze-20261106-01/restored-rootfs`.
- Foreign work: `/var/lib/a733-clean-replay-20261106/userspace-foreign-01/work`.
- Probe: `/var/lib/a733-clean-builder/seed-probe-20261106-05/`.
- Pinned namespace command helper:
  `/var/lib/a733-builder-freeze-project-20261106/build/clean-builder.py`.

The host must be initial-namespace root on Ubuntu 22.04 AMD64, with all 842 exact
installed package tuples from the committed builder lock. The helper, QEMU and
probe bytes must match their pins. Protected infrastructure ancestors remain
initial-root owned; the restored frozen root is mapped `1048576:1048576`, mode
`0700` or `0755`. The launcher validates this distinction without changing the
frozen OS. Existing mapped processes, relevant mounts and output phases are refused.

[`launch-userspace-configuration.py`](../build/launch-userspace-configuration.py)
checks six explicit committed project files against Git blobs and snapshots their
bytes as initial-root-owned read-only inputs. They include both new recipes, the
API helper and all three dependency locks. Required files differing from their
committed blobs fail before cloning. The actual source checkout was supplied by
a SHA-256-checked Git bundle; its commit and all staged file hashes are retained
in the input manifest.

Require more than **4,000,000,000 bytes free before cloning**. During execution,
the launcher samples disk and memory each second and stops below 3,000,000,000
free disk bytes or 1,000,000,000 available RAM bytes, or after 2,700 seconds.
The separate exporter also requires its existing 3 GiB free-space floor. These
limits do not replace production root/NVMe staging guards. No LVM resize occurred.

## Actual successful invocation

This exact command ran on the prepared builder:

```sh
sudo python3 -I -B /var/lib/a733-userspace-configuration-project-20261106/build/launch-userspace-configuration.py \
  --tag recipe-01 \
  --project /var/lib/a733-userspace-configuration-project-20261106
```

The phase is `/var/lib/a733-clean-replay-20261106/userspace-configure-recipe-01`.
Preserve it; repeating that name must fail. A subsequent run needs a fresh safe
tag and the same validated prerequisites. `--project` selects committed recipe
inputs, while storage and namespace infrastructure paths remain fixed.

The launcher copies `arm-root`, `repo`, `adapter` and `foreign-receipt.json` into
fresh work with `cp -a --reflink=auto`. It rejects special source files and external
hardlinks, validates protected rootfs paths and authenticates the clone bindings.
It never seeds construction from the historical rootfs archive.

[`configure-host-userspace.py`](../build/configure-host-userspace.py) has no root
path override. Its preflight authenticates the foreign receipt, adapter and all
669 repository files against read-only inputs and exact package locks. Only the
validated clone is changed to mode `0755`, allowing APT's `_apt` user to traverse
its root. It stages the pinned build interpreter and daemon-suppression policy.

The existing [trusted API helper](userspace-api-preflight.md) enters the payload's
other namespaces while retaining the initial user namespace. It supplies the
reviewed device/API bindings and exits before a root-owned authenticated
continuation allows package scripts to run. The payload keeps its fixed private
UID/GID map, private network, no-new-privileges and seven capability drops.
The frozen OS and recipe inputs remain read-only.

Stock ARM second stage configures 99 base packages, followed by exact offline APT
requests for the 220-package lock. Restore `policy-rc.d` returning 101 after second
stage and retain the disable-all systemd preset. The successful receipt requires
exact fully installed inventory, empty `dpkg --audit`, successful `apt-get check`,
unchanged restrictions and no extra namespace processes. The outer launcher then
checks host inventory and process/mount cleanup before declaring success.

## Export comparison

The [second export evidence](evidence/userspace-repeat-export-20261106.json)
records the same reviewed exporter applied to this newly configured clone.
The new archive is retained at
`out/userspace-export-recipe-20261106/rootfs.tar.gz`: **165,686,672 bytes**,
SHA-256 `c2059eccac59534bac627eb9b9864225e2bc35a012697d11e66b76a8b0c6688b`.
Its raw tar is **539,678,720 bytes**, SHA-256
`24bc36e323d7ae9f0097b88d8c0d1e0d473a0dc6f52a4051bd8ef3614279782e`.
This rebuild result does not replace the deployment guide's selected artifacts.

Independent standalone raw tar/PAX readback of both complete archives passes,
including every file byte/hash, timestamp, attribute and device/link record.
Both cleaned archives have the same 15,881 paths, modes, owners, link targets,
devices and attributes. They differ in 2,690 modification timestamps and the
contents of these four files:

| File | Difference to resolve |
| --- | --- |
| `etc/nvme/hostid` | Generated host identifier remains in generic base |
| `etc/nvme/hostnqn` | Generated host NQN remains in generic base |
| `var/cache/ldconfig/aux-cache` | Build-generated dynamic-linker cache |
| `var/cache/apt/pkgcache.bin` | Build-generated package cache |

That exporter revision preserved source timestamps and omitted these four paths
from cleanup. The fixed gzip header did not hide the differing source metadata
and contents. The subsequent [normalized export pair](userspace-export-reproducibility.md)
now removes the generated NVMe identifiers and three caches and clamps later
archive timestamps, producing matching compressed/raw/BOM bytes. Both
configurations still reused one foreign extraction. The subsequent
[fresh pipeline](userspace-fresh-pipeline.md) now reconstructs anew from raw
packages and matches their normalized result; production deployment remains open.

## Evidence and failures

Selected public records are retained on codex at
`out/userspace-configuration-recipe-20261106/`: input snapshots, outer/inner
receipts, helper operations, per-command logs, direct source-root stat and host
inventories. Generated target SSH private keys are excluded from those records.
They must be stripped by the [reviewed exporter](offline-userspace-reconstruction.md#configured-userspace-and-export)
before an archive can become a root payload input.

The successful run configured all 220 packages, passed both audits and retained
only namespace PIDs 1 and 2. The source-root stat confirms `0755`. Neither APT
operation emitted the earlier `_apt` unsandboxed-download warning. Minimum free
disk was **3,675,967,488 bytes** and available RAM **7,278,411,776 bytes** across
157 samples. The complete host inventory is unchanged and no mapped processes
or output mounts remain. Fixtures and independent review supplement these actual
records; their counts and source hashes are in the evidence.

On failure, preserve the phase and all logs. Inspect its receipt, per-command
stderr and namespace/process cleanup before attempting a fresh tag. A preflight
failure can occur before the phase exists. The launcher does not erase failed
phases or modify the retained foreign source. The exporter separately cleans
its configured clone before archive publication; an interrupted export needs
inspection before retrying.

The [Ubuntu debootstrap manual](https://manpages.ubuntu.com/manpages/jammy/man8/debootstrap.8.html)
documents foreign extraction and second-stage completion. The pinned adapter's
source hashes and narrow overrides remain in its manifest. The
[systemd 249 nspawn manual](https://www.freedesktop.org/software/systemd/man/249/systemd-nspawn.html)
describes namespace operation, read-only binds and user mapping; the project's
pinned helper, additional capability drops and actual execution records define
the exact restrictions used here.

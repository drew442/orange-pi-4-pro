# Fresh userspace pipeline launcher

[`launch-userspace-pipeline.py`](../build/launch-userspace-pipeline.py) sequences
fresh foreign extraction, ARM configuration and normalized export. It uses the
three [existing recipes](userspace-fresh-pipeline.md), with a new shared work tree
and a separate private namespace for each phase. No historical configured tree
or rootfs archive seeds construction.

[Recorded checks](evidence/userspace-pipeline-launcher-20261106.json) bind
commit `87b2544`, independent Sol/high review and 1,159 native cases (97
environment skips), four assembly cases and all five shell suites. The actual
root preflight on the builder rejected 3,278,569,472 free bytes before phase
creation; its plan passed and before/after package inventories match.
Its actual three-phase execution remains pending builder capacity;
the prior successful [fresh pipeline](userspace-fresh-pipeline.md) used retained
outer wrappers. Do not treat those results as execution of this launcher.

## Inputs and plan

Use a committed clean checkout on the prepared Ubuntu 22.04 AMD64 builder. The
launcher authenticates its own source, shared orchestration primitives, recipes
and locks against their Git blobs, then snapshots only each recipe's declared
inputs. It reuses the established API helper handshake for the ARM phase.

Fixed prepared inputs remain required:

- Frozen root: `/var/lib/a733-frozen-builders/freeze-20261106-01/restored-rootfs`.
- Namespace helper: `/var/lib/a733-builder-freeze-project-20261106/build/clean-builder.py`.
- Probe/mask: `/var/lib/a733-clean-builder/seed-probe-20261106-05/`.
- Raw cache: `/home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01`.
- Signed package index: `/var/lib/a733-userspace-foreign-recipe-inputs-20261106/Packages.xz`.

The index, helper, probe and QEMU bytes are pinned. Root infrastructure ancestors
must be protected; the frozen root retains its mapped owner. Raw cache ancestors
are user-owned, so the cache is mounted read-only and every consumed package and
signed metadata file is authenticated by the recipe. The complete installed
outer builder inventory must match its replay lock.

`--plan` needs neither root nor those builder paths. From the committed checkout:

```sh
python3 -I -B build/launch-userspace-pipeline.py --tag clean-01 --plan
```

It validates source blobs and prints selected files, paths, phase order and
resource floors. It does not test the installed builder or execute phases.

## Execution contract

A real invocation requires initial-namespace root on the prepared builder, with
`--plan` omitted. Its phase name is
`/var/lib/a733-clean-replay-20261106/userspace-pipeline-TAG`.
Use a fresh safe tag; existing outputs are never overwritten. The launcher
contains no disk cleanup, LV resize or physical-device write operation.

Foreign extraction and ARM configuration each require more than 4,000,000,000
bytes free before launch; export requires more than 3,500,000,000. Runtime checks
retain the 3,000,000,000-byte disk and 1,000,000,000-byte available RAM floors,
with 900/2,700/900-second phase limits. The exporter also retains its own 3 GiB
floor. Production root/state/NVMe guards remain separate and unchanged.

Every phase retains its read-only source snapshot, scratch directory, runtime
log, recipe receipt binding and resource samples. Configuration retains the
nonce-bound API setup and helper records. Final checks require terminal success,
unchanged outer package inventory and no remaining mapped processes or builder
mounts. The exported archive must match the committed normalized userspace
profile before the pipeline can report success.

The complete root invocation retained in the evidence returned the expected
capacity error. No successful build invocation is documented for this launcher
yet. Once sufficient capacity is available, execute its committed prepared copy
with a fresh tag and preserve all resulting records.

The output will be `work/export-result/rootfs.tar.gz` beneath that fresh phase.
It is a userspace archive, not an SD image. Root/state composition still requires
[explicit profile selection](userspace-profiles.md) and full image readback.
Published installation artifacts remain unchanged. No hardware boot, installed
SSH, real KVM guest, endurance or zero-SD-write acceptance follows from this
launcher's host checks.

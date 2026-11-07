# Time-profile outer launcher

[`launch-timesync-userspace.py`](../build/launch-timesync-userspace.py) connects
the separate [package step](timesync-userspace.md) and [export adapter](timesync-export.md).
The [actual complete execution](evidence/timesync-execution-20261107.json) passes
configuration and export from the tracked normalized base, using commit `719146c`
and fresh tag `retained-02`. It produces a 138,093,095-byte archive with SHA-256
`0ead62c982344bfa3a796027e2f07ce0d8402e7033122707e01a9a3f8860f248`.
Independent readback verifies all 15,909 entries, metadata, complete 221-package
inventory, fixed 996:996 identity, preserved old accounts and required boot links.
The artifact is not yet registered for production composition. Published
installation files remain unchanged.

## Inputs and read-only plan

The launcher requires a safe new `--tag`, a `--base-tag` selecting a successful
[tracked normalized pipeline](userspace-pipeline-launcher.md), a committed
`--project` checkout and the authenticated `--extra-deb` file. It accepts no
arbitrary base-tree path. The source base must reside beneath
`/var/lib/a733-clean-replay-20261106/userspace-pipeline-BASETAG/work`.
The previously retained foreign/configured trees cannot substitute for this
tracked source chain.

This exact nonroot plan command passed on codex:

```sh
python3 -I -B build/launch-timesync-userspace.py \
  --tag candidate-01 --base-tag reviewed-02 \
  --project /home/drew/orange-pi-4-pro \
  --extra-deb /tmp/a733-timesync-input.deb --plan
```

The plan validates committed source bytes and the input contract. It does not
read the named base or extra DEB; that absolute DEB path is an illustrative
future input, not an acquired file or permission to execute a package. An actual
run requires the 62,172-byte package pinned in the input contract.

Actual root preflight on the builder refused 3,240,173,568 free bytes at the
**5 GiB** initial guard, before checking the unbuilt base or creating a phase.
The phase remained absent and host package inventory remained unchanged. The
[prepared builder expansion](build-host-capacity-plan.md) still awaits approval;
no storage resize or guard reduction occurred.

## Source, clone and phase contract

Before staging, the launcher authenticates 22 explicit committed project files,
the contract and dependency pins. It binds the base root report to its source
manifest, phase snapshots and Git blobs, exact commands, namespace probe logs,
API-helper success, receipt hashes, canonical BOM and normalized archive.
Only the native `arm-root` and unchanged repository are copied into fresh private
work. Special files and external hardlinks are refused. The in-namespace recipe
then verifies the complete canonical base before making changes.

The measured clone allowance includes regular contents and 4 KiB per node. The
required capacity is at least 5 GiB and at least the 4 GiB configuration floor
plus that allowance, twice the staged source/input bytes and 64 MiB headroom.
Runtime monitoring retains a 3 GiB disk reserve, 1 GB available RAM, one-second
samples and 2,700/900-second configuration/export timeouts. Export also keeps
its 3.5 GB initial guard. Larger root/state/NVMe guards remain separate.

Both phases receive byte-identical project manifests and a declared source set;
API profiles/nonces are fresh for each phase. Work is shared only inside the new
private clone. The trusted 54-operation API setup remains confined to package
configuration. Final success requires terminal receipts, exact manifest and
provenance bindings, streamed archive/BOM verification, unchanged host inventory
and no remaining mapped processes or mounts. The output hash is measured after
construction, never invented or accepted from an unbuilt candidate.

## Propagation prelude and actual diagnostic

The read-only frozen-environment diagnostic found shared/master tags under the
normal nspawn root, proc, sys and other mounts. A separate mount namespace does
not itself guarantee private propagation. The launcher preserves the recipe's
strict check and all seven payload capability drops.

Before either recipe is imported, its stdlib-only runner publishes fresh,
nonce-bound readiness and waits. The controller validates the actual mapped PID,
credentials, capability masks, no-new-privileges, command line and namespace
identities. A short initial-root helper enters six target namespaces while
retaining the initial user namespace. It verifies those identities, a distinct
mount namespace and the frozen root's device/inode before its single recursive
`MS_PRIVATE` operation. The [mount syscall contract](https://man7.org/linux/man-pages/man2/mount.2.html)
describes private propagation; [nsenter](https://man7.org/linux/man-pages/man1/nsenter.1.html)
documents explicit namespace and root-directory selection. No payload capability
is restored, and the helper does not execute a package or time service.

The controller verifies the proof and unchanged payload before publishing an
immutable continuation. The runner checks its nonce, proof hash, private mounts
and unchanged restrictions before importing the recipe. Stage-specific markers
prevent stale configuration readiness from being reused during export.

[Evidence](evidence/timesync-outer-preparation-20261106.json) binds commit
`4d89cd6`, the original read-only diagnostic and the actual prelude-only probe.
The latter used the real frozen OS, helper protocol and restrictive umask 077,
then exited without importing a recipe or cloning a root. It preserved all seven
payload namespaces, all five capability masks and no-new-privileges; READY was
mode 0444. Host namespaces/root mount, 842-package inventory and cleanup checks
passed. Four resource samples recorded minimum free disk 3,246,317,568 bytes and
available RAM 7,488,028,672 bytes. This small diagnostic used a separate 3 GiB
reserve; it did not bypass the production launcher's 5 GiB build guard.

Independent read-only remote checks verified original files and frozen-root
identity. Twenty-three focused tests and the full native suite pass: 1,273 cases,
97 environment skips, four assembly cases and five shell suites. These establish
source/host behavior and the propagation step only.

The successful full run uses the protected source checkout at
`/var/lib/a733-timesync-retry-project-20261107/project`, base tag `retained-01`,
tag `retained-02` and authenticated extra DEB `/tmp/a733-timesync-input.deb`.
Its output is `work/export-timesync-result/` beneath
`/var/lib/a733-clean-replay-20261106/userspace-timesync-retained-02`.
All records and authenticated archive/BOM copies are retained on codex under
`out/timesync-retry-20261107/`. This archive is a userspace build input, not an SD image.

The run required 5,368,709,120 bytes initially and sampled minima of
4,877,975,552 bytes disk free and 7,335,288,832 bytes available RAM. Both phases
pass private propagation, receipts and cleanup. A separate live postcheck sees
unchanged host inventory and no mapped processes or phase/base mounts.
[Verified cache retirement](evidence/builder-deb-retention-20261107.json) supplied
capacity; no resize or guard reduction occurred. The earlier failed attempt
and its clone remain retained; its missing boot-link issue is corrected.

Next integrate explicit profile provenance, matching 221-package root metadata
and persistent clock-state seeding/guards before permitting image composition.
Current builders accept only baseline/normalized profiles; the clock-state audit
is not yet a state-initialization or updater hook. A second fresh construction
and comparison, production root/state/NVMe readback, independent deployment and
physical boot, SSH, NTP, KVM, SD-write, endurance, update/rollback and recovery
results remain required. Follow the [existing deployment guide](deploy-personalized-20261106.md)
for current installation images; this launcher does not replace them.

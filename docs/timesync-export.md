# Authenticated time-profile export preparation

[`export-timesync-userspace.py`](../build/export-timesync-userspace.py) prepares
export of the separately configured 221-package profile. It does not run ARM
programs, install packages, start a daemon or set a clock. Actual configuration
and export remain unperformed; published installation images are unchanged.

The adapter authenticates the pinned [package-step source](timesync-userspace.md),
then its closed source manifest before loading dependencies. It compiles the
unchanged normalized exporter into a private module instance, using explicit
221-package lock/count and derivation-receipt bindings. The original exporter
file, default archive selection, timestamp normalization, complete metadata
scan, archive/BOM readback and initial 3 GiB guard remain unchanged.

## Receipt and publication contract

Configuration and export must use a **byte-identical project source manifest**.
The future outer launcher must stage one explicit committed source set, including
this adapter, for both phases. The configuration recipe permits this declared
superset while rejecting undeclared files and directories. Phase-specific API
profiles may differ outside that project manifest. A receipt from another
source snapshot is rejected rather than attributed to the current sources.

Before cleanup, export authenticates the caller-supplied derivation-receipt hash,
exact 221-package inventory and live status database, package-state descriptor,
identity before/after preservation, fixed policy, service enablement/payloads,
build DEB and daemon suppression. Unexpected clock state, remaining processes
or active root mounts prevent export. The base configure receipt is preserved.

The adapter rechecks stable identity, service, package, receipt and source
bindings after cleanup. It verifies the private staged archive/BOM and enriches
the staged receipt with profile, derivation hash, BOM pin and source provenance.
It fsyncs that receipt and directory before the original exclusive atomic
publication. It does not alter or reread the published directory afterward.
Cleanup can change only the disposable private clone; a failed export must never
be retried against a deployed root.

The interface requires `--output` naming a new
`/a733-export-output/export-timesync-NAME` and
`--derivation-receipt-sha256`. It runs only from the authenticated frozen-builder
source path with `-I -B`. No successful end-to-end invocation is documented yet.
Its output will be a userspace archive, not an SD image or an accepted installer.

## Verification and remaining work

[Recorded source/host checks](evidence/timesync-export-preparation-20261106.json)
bind commit `265352f`, independent Sol/high review, 15 focused adapter tests and
full discovery: 1,250 cases with 97 environment skips, four assembly cases and
five shell suites. Fixtures exercise real synthetic archive/BOM staging and
publication; namespace, receipt-state dependencies and ARM operations are mocked.
They do not construct or validate an actual 221-package userspace artifact.

The same change set repairs shared launcher process cleanup: an already reaped
leader is never signaled, while timeout group signals finish before the owned
leader is reaped. Twenty-four launcher cases pass on the actual Ubuntu 22.04
builder with one environment skip, including four real child-process tests.
Host package inventory is unchanged. Surviving descendants of a reaped leader
still make final process/mount checks reject success; a cleanup timeout can
prevent a final receipt. Inspect actual process handles before retrying.

Use source commit `26cba70` or later for future launches. The protected builder
checkout from the earlier capacity-refusal probe predates this repair and is a
historical record, not the source to execute. The current checkout's read-only
base-pipeline plan passes; this is not a completed builder preflight or build.

The [outer launcher](timesync-outer-launcher.md) now passes source review, an
actual propagation-only probe and capacity refusal. Next satisfy all capacity
guards, stage the current committed sources and execute the real package/export
phases. The [reviewed builder expansion](build-host-capacity-plan.md)
remains unapproved. Then authenticate/register the actual output profile,
rebuild and inspect matching root/state/NVMe images, reproduce deployment and
complete physical time, KVM, boot, SD-write and recovery acceptance.

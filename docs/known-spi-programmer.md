# Known SPI programmer preparation

The trusted caller now registers the reviewed source-backed SD-priority
recovery policy. The A733 ROM tables place SMHC0 before SPI, and the known SD
recovery images have physically booted on this board. The exact registered proof
is SHA-256 `ec9fe1e5ac371f2e984b3da6e053efdc44f2fa89aa624837731f2eabf56aba11`.
The programmer binds that proof and the capture binds the programmer's exact
27,218-byte source, SHA-256
`635581fd3290a5baf574b0f8e77d87e9a8ea28a67c38380591b05e67b947dbe1`.
[Recovery policy and physical procedure](spi-sd-boot-20261111.md).

The earlier `RECOVERY_PROOF_PIN=None` preparation required USB-FEL/RAM evidence.
That specific method was a project precaution, now replaced by the documented
ROM SD priority and tested SD recovery. Registration remains a reviewed source
change. Runtime pin overrides remain unavailable. The operator explicitly
authorized programming this identified chip.

The fixed target is A733 `/dev/mtd0`, character **90:0**, `spi0.0`,
XM25QU128C JEDEC **204118**, **16,777,216 bytes**, **65,536-byte** erase units and
byte writes. The caller also binds the expected boot UUID, initial host namespaces,
exact DT compatible bytes and the known SPCC NVMe serial `134807190FC700149277`.
It does not require SD to be inserted. It refuses KVM consumers, other MTD handles,
read-only character aliases, block aliases/holders, mounts and mappings found
through its all-task census. This assumes trusted, quiescent maintenance;
advisory locking and a census cannot exclude hostile privileged races.

## Sources and execution

`scripts/program-known-spi.py` uses the authenticated
[transaction engine](spi-unit-transaction.md), [MTD backend](spi-mtd-adapter.md),
backup identity helpers and known SD writer's namespace helper. Original/candidate
pins and target geometry cannot be overridden. Helpers, images, plan and proof
must be held single-link root-owned files in a private `/run` tmpfs directory,
with no swap. Helpers and input files use mode 0600; the programmer itself uses
0400 and its immediate directory 0700. Ancestor identity/ownership is checked;
unrelated ancestor-directory timestamp changes do not invalidate an attempt.

`scripts/capture-known-spi-events.py` runs on the separate x86-64 capture host.
Run it with native root privileges when namespace descriptor access requires
them. Its output argument names a new directory, not a log file; every ancestor
must be owned by root or the executing user and must not be group/world writable.
For a root capture, use a private root-owned directory under `/var/lib` and a
root-owned copy of the exact public known-hosts pin. A repository under a
user-owned home directory does not meet that root-capture ancestry requirement.
Its fixed bootstrap authenticates the programmer's exact size and SHA-256,
holds physical source/ancestor descriptors and compiles those authenticated bytes.
The caller requires this binding for write admission and checks its own source
with all guarded inputs. Direct pathname execution is not a write alternative.
The capture's programmer pin must be updated together with any reviewed programmer
change, including eventual registration of real recovery evidence.

SSH targets only `root@192.168.1.116` with the recorded Ed25519 fingerprint.
A sealed, parent-held known-hosts snapshot remains available through OpenSSH's
inherited-descriptor cleanup. Remote arguments are constructed and quoted. Earlier private RAM staging and read-only planning succeeded. The metadata-aware
revision requires its own software review and physical validation. Physical programming and boot results are recorded in
the [current SPI procedure](spi-sd-boot-20261111.md).

## Plan, durable events and failure

Read-only plan mode authenticates the fixed images and reads current SPI with
progress. Write mode requires that reviewed canonical plan digest and unchanged
current flash, plus registered recovery proof and durable capture readiness.
The existing engine controls complete erase-unit reconstruction and header-last
installation and calibrated restoration. Installation preserves validated live
calibration. Restore defaults to the original blank backup; selecting
`--restore-id postboot-20261111` or `postwarm-20261111` also requires a held
`--restore-image` matching that registered backup. Restore options are refused
for installation. See the [metadata policy](spi-calibration-policy.md).
Restore is a separately planned operation; no automatic retry or rollback follows
failure. Malformed parameters stop normal admission, including after an
interrupted metadata write. A separate reviewed recovery registration is required
for such a preimage.

Each bounded event binds a session nonce, sequence and canonical event hash.
The native capture fsyncs the event log and containing directory before its exact
acknowledgement. Failed persistence, malformed acknowledgements, EOF or timeout
stop further mutation. Native storage reserves 256 MiB plus the bounded capture
budget. Transaction readback, successful descriptor close and captured final
outcome are all required for software completion. A close error remains uncertain;
MTD has no assumed `fsync` persistence guarantee. Every receipt retains
`hardware_acceptance=false`.

Host tests use fake descriptors, bytearray NOR, pipes and disposable subprocesses.
They establish software ordering and refusal behavior. Actual MTD operation,
SD recovery and SD-removed NVMe boot require their own physical evidence.
The qualifying workload has ended and its accepted data remains retained.

# Known SPI programmer preparation

The trusted caller and native event capture are **preparation only**. Independent
source review and [2,218 privileged Ubuntu tests](evidence/known-spi-programmer-preparation-20261109.json)
now pass. They have not opened or
programmed physical SPI. `RECOVERY_PROOF_PIN=None` deliberately refuses write
mode before device opening; there is no CLI or environment override. Registering
proof requires the actual [independent ROM/RAM recovery demonstration](rom-recovery-investigation.md),
coordinator acceptance and another reviewed source change. Known storage
[authorization](evidence/storage-write-authorization-20261108.json) already persists.

The fixed target is A733 `/dev/mtd0`, character **90:0**, `spi0.0`,
XM25QU128C JEDEC **204118**, **16,777,216 bytes**, **65,536-byte** erase units and
byte writes. The caller also binds the expected boot UUID, initial host namespaces,
exact DT compatible bytes and the known SPCC NVMe serial `134807190FC700149277`.
It does not require SD to be inserted. It refuses KVM consumers, other MTD handles,
read-only character aliases, block aliases/holders, mounts and mappings found
through its all-task census. This assumes trusted, quiescent maintenance;
advisory locking and a census cannot exclude hostile privileged races.

## Sources and execution

`scripts/program-known-spi.py` uses the unchanged authenticated
[transaction engine](spi-unit-transaction.md), [MTD backend](spi-mtd-adapter.md),
backup identity helpers and known SD writer's namespace helper. Original/candidate
pins and target geometry cannot be overridden. Helpers, images, plan and proof
must be held single-link root-owned files in a private `/run` tmpfs directory,
with no swap. Helpers and input files use mode 0600; the programmer itself uses
0400 and its immediate directory 0700. Ancestor identity/ownership is checked;
unrelated ancestor-directory timestamp changes do not invalidate an attempt.

`scripts/capture-known-spi-events.py` runs on the separate x86-64 capture host.
Its fixed bootstrap authenticates the programmer's exact size and SHA-256,
holds physical source/ancestor descriptors and compiles those authenticated bytes.
The caller requires this binding for write admission and checks its own source
with all guarded inputs. Direct pathname execution is not a write alternative.
The capture's programmer pin must be updated together with any reviewed programmer
change, including eventual registration of real recovery evidence.

SSH targets only `root@192.168.1.116` with the recorded Ed25519 fingerprint.
A sealed, parent-held known-hosts snapshot remains available through OpenSSH's
inherited-descriptor cleanup. Remote arguments are constructed and quoted;
there is no arbitrary command interface. Staging and actual use remain unperformed.

## Plan, durable events and failure

Read-only plan mode authenticates the fixed images and reads current SPI with
progress. Write mode requires that reviewed canonical plan digest and unchanged
current flash, plus registered recovery proof and durable capture readiness.
The existing engine controls complete erase-unit reconstruction and header-last
installation. Restore is a separately planned operation; no automatic retry or
rollback follows failure.

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
independent recovery, flash restoration and SD-removed NVMe boot remain separate
physical gates. Do not run this preparation during the active endurance workload.

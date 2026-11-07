# Offline time-service userspace derivation

The `timesync-20261106` profile is now a source-integrated, explicit opt-in in
[`userspace-profiles.py`](../build/userspace-profiles.py). Baseline remains the
default and normalized behavior is unchanged. The separate input contract
[`host-timesync-inputs.lock.json`](../configs/host-timesync-inputs.lock.json)
adds only ARM64 `systemd-timesyncd` 252.39-1~deb12u2 to the normalized 220-package
base. A [complete actual build](evidence/timesync-execution-20261107.json) now
produces and independently verifies the new archive. Published images and
default/normalized archive pins remain unchanged. The source integration binds
matching profile provenance, package-state metadata, and clock-state policy for
later root/state construction; those production artifacts have not been built.

The two new locks explicitly preserve every existing tuple and binary hash:

- [221 package/version/architecture tuples](../configs/host-timesync-packages.lock.tsv).
- [221 binary archive hashes](../configs/host-timesync-package-hashes.lock.tsv).

The added DEB is 62,172 bytes with SHA-256
`700be69248209d27e58ee0736f1460bc8acb10c1e717efc985ebd23dd5fd7714`.
Its authenticated [package input evidence](evidence/time-sync-package-input-20261106.json)
binds the signed snapshot index. Its exact systemd source version and three
source archive hashes were already retained; no new source version is inferred.
The contract pins those inputs, the original locks, new locks and fixed identity
policy. It also pins the installed unit/daemon bytes and required boot-enablement
links. It declares no output archive hash before construction.

## Recipe boundary

[`configure-timesync-userspace.py`](../build/configure-timesync-userspace.py)
is an in-namespace recipe for a fresh private clone of the native, cleaned
normalized base and its authenticated offline mirror. It is not a replacement
for the foreign debootstrap step. The outer launcher must first create the clone,
stage committed sources and generate the existing nonce-bound API profile.

The recipe authenticates its closed read-only source manifest before importing
dependencies. It verifies every canonical base record against the pinned
4,337,400-byte normalized BOM, including file bytes and metadata, and requires
the exact installed 220-package inventory. It checks the additional package,
lock relationship and unused reserved identity before mutation.

Inputs remain at the established private namespace paths:
`/a733-export-output/arm-root`, `/a733-export-output/repo` and
`/a733-sources/project`. The BOM and extra DEB are separate read-only source
inputs. The existing trusted helper continues to authenticate and bind the
unchanged 220-package, 669-entry repository. The new DEB is not inserted into
that mirror or represented as an old signed-index entry.

Before normal package configuration, the recipe installs the authenticated
[fixed identity policy](timesync-identity.md), restores the pinned build
interpreter and exit-101 daemon suppression, and snapshots the original account
tuples. The helper handshake precedes all ARM package commands. It installs the
local DEB through dpkg, queries the exact 221-package inventory, requires an empty
dpkg audit and runs an APT dependency check. It performs no APT update or network
installation. Namespace restrictions retain the dangerous-capability drops,
including CAP_SYS_TIME; no time daemon or clock-setting operation is permitted
during the build.

For this separate future profile, automatic time on host boot is an explicit
policy. Actual isolated installation showed that the suppression policy leaves
the required links absent. The recipe now creates only the contract's declared
future-boot links explicitly after package/account audits, without invoking a
daemon or systemctl. It walks physical root-owned parents through no-follow
directory descriptors, creates missing parents as 0755 despite restrictive
umasks, accepts identical links and refuses conflicting files or aliases. The recipe
requires the exact sysinit and D-Bus alias links and pinned unit/daemon payloads,
and rejects masks or overrides. Its receipt records the enablement only after
validation. The new archive meets these requirements; the currently published
host has not been rebuilt with this service and no physical startup is claimed.

A distinct `timesync-receipt.json` records this derivation. It does not replace
the base configure receipt or certify a new exported archive. Success requires
identity preservation, exact package state, unchanged restrictions/repository,
daemon suppression and process cleanup checks.

## Verification and next steps

[Source/input evidence](evidence/timesync-derivation-preparation-20261106.json)
binds commit `574a310`, independent Sol/high review, 28 focused cases and
read-only inspection of the authenticated DEB. All 220 original tuples/hashes
remain unchanged; unit/daemon pins match the package tar directly. Full discovery
passes 1,231 cases with 97 environment skips, four assembly cases and all five
shell suites. ARM execution and namespace setup are mocked in these tests.

The [actual attempt](evidence/timesync-execution-attempt-20261106.json) installed
the package and passed inventory/account/audit checks, then failed on missing
service enablement. The [explicit link correction](evidence/timesync-enablement-fix-20261106.json) passes independent Sol/high
review and 34 focused tests; its exporter dependency pin is updated to the new
recipe. A subsequent fresh complete configuration/export passes with independent
full artifact readback. Source registration and state hooks are now integrated
at `bb411e3`; installed service startup remains unproven.

A real run requires the pinned frozen Ubuntu 22.04 AMD64 environment, private
UID/GID/network namespaces, read-only inputs and disk-backed work. Its 4 GiB
free-space floor remains separate from larger production image guards. The
successful run leaves about 4.88 GB free, below the outer launcher's separate
5 GiB initial guard for another fresh construction. Reclaim only verified
redundant generated files before another build.
The prepared [20 GiB expansion](build-host-capacity-plan.md) awaits approval. No resize or
capacity guard reduction has occurred.

The next full construction must rebuild the tracked base against the changed
closed profile manifest before replaying the time profile, preserving all source
closure checks. Time-enabled production roots (4 GiB each), state (8 GiB), and NVMe have not
been built; existing baseline artifacts remain unchanged. Clock-state initialization, quiescent boot audit, and live directory
guard are source-wired, but actual installed transitions, rollback, NTP readiness,
physical zero-SD-write checks, and hardware acceptance remain unproven. No resize
occurred and production capacity guards remain unchanged. The [existing deployment guide](deploy-personalized-20261106.md)
continues to identify current images for physical NVMe boot, SSH and actual KVM testing.

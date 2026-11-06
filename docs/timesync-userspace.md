# Offline time-service userspace derivation

The separate input contract
[`host-timesync-inputs.lock.json`](../configs/host-timesync-inputs.lock.json)
adds only ARM64 `systemd-timesyncd` 252.39-1~deb12u2 to the normalized 220-package
base. This is preparation for a new userspace artifact, not a built archive or an
accepted deployment profile. Published images, default/normalized archive pins
and existing producer recipes remain unchanged.

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
policy. The package post-install script enables the unit; an exit-101 startup
policy and disable-all preset do not prevent that explicit enablement. The recipe
requires the exact sysinit and D-Bus alias links and pinned unit/daemon payloads,
and rejects masks or overrides. Its receipt records the enablement only after
validation. These are requirements for a future built artifact, not claims that
the currently published host contains or runs the service.

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

This recipe has not installed a package or produced a configured 221-package
tree. The [derived exporter](timesync-export.md) now passes independent
source/host review. Outer orchestration, actual configuration/export, full output
inspection and archive-profile binding still need implementation/execution.
Independent source review does not establish successful package configuration or boot.

A real run requires the pinned frozen Ubuntu 22.04 AMD64 environment, private
UID/GID/network namespaces, read-only inputs and disk-backed work. Its 4 GiB
free-space floor remains separate from larger production image guards. The
builder currently has about 3.25 GB free; the prepared
[20 GiB expansion](build-host-capacity-plan.md) awaits approval. No resize or
capacity guard reduction has occurred.

After actual installation and export, authenticate the new archive explicitly,
regenerate per-root package-state metadata, compose matching root/state/NVMe
artifacts and complete full readback. Installed service startup,
clock-state/rollback behavior, NTP readiness and physical zero-SD-write tests
remain necessary. The [existing deployment guide](deploy-personalized-20261106.md)
continues to identify the images for physical NVMe boot, SSH and real KVM testing.

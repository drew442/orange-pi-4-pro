# Package metadata across A/B roots

This is preparation for versioned updates. The current implementation still
binds the entire shared state `/var` over the selected root's `/var`; package
metadata isolation and cross-version updates are not implemented. Both
published root slots contain the same package versions.

The [authenticated archive inventory](evidence/package-metadata-inventory-20261104.json)
records 220 package stanzas, five `/var/lib/dpkg/alternatives` databases,
corresponding `/etc/alternatives` entries and retained APT `extended_states`.
The source recipe clears downloaded package indexes and `.deb` files but retains
those databases. Separating only `dpkg/status` would leave other version-dependent
metadata shared. `/etc` uses the selected root as its lower layer and a fresh
RAM upper layer each boot; its alternatives links must remain paired with the
selected root's databases.

A [broader archive inventory](evidence/package-extra-metadata-inventory-20261104.json)
also finds debconf, ucf, PAM and systemd-generated state, shell registration and
ldconfig cache data. Two APT binary caches occupy 77,282,380 bytes despite the
download cleanup. Classify each as image-owned metadata, unused maintenance data,
regenerable cache or backward-compatible persistent state before adopting the
contract. Remove unnecessary binary caches in a future authenticated build;
do not describe the current archive as cache-free. This design remains subject
to the release-base choice after the hardware baseline.

## Proposed initial-deployment contract

Keep each root's complete dpkg and APT database trees under its immutable
`/usr/lib/orangepi-hypervisor/package-state/{dpkg,apt}`. The builder should move
the authenticated trees there, leave real `/var/lib/{dpkg,apt}` mountpoints,
and record an explicit package-state format and contents in root metadata.
It must reject symlink parents, unsafe destinations and existing output rather
than editing a previously built image.

After binding writable state `/var`, early boot should bind the selected root's
database trees over `/var/lib/{dpkg,apt}` and explicitly make those binds
read-only. Logs, host identity and application state remain on NVMe. Existing
prototype state can retain its covered database copies without treating them
as authoritative; this design requires no on-device deletion or migration.
Database writes from in-place APT/dpkg operations are outside image-based host
maintenance. Timer/service policy also needs review before this is deployed.

The handoff must validate the format and real source/target directories, track
both mounts for failure cleanup, and refuse a missing or incompatible format.
The management validator must require the exact read-only, selected-root-backed
nested mounts and reject foreign or additional overmounts. A whitelist alone
would weaken the existing topology checks.
Require matching format metadata during root/bootstrap packaging, selector
preparation and integrated-image assembly as well as at boot. The current mount
parser does not reject every foreign mount under the future database source and
target paths; extend exact origin, root-path, ownership and nested-mount checks
before claiming those paths are protected.

New root markers change the selector's authenticated marker digest. Build fresh
selection/state/bootstrap artifacts together and check their pairing. A new
bootstrap requiring this format cannot promise to boot older prototype roots.
No such prototype is installed on hardware. Preserve old recovery artifacts
separately; do not advertise an untested legacy migration or rollback path.
After initial deployment with a stable format, routine compatible root updates
should switch NVMe slots without rewriting SD. Kernel/bootstrap maintenance
remains an explicit separate operation.

## Required evidence before adoption

Use tiny distinct A/B database fixtures to prove selection, alternatives
consistency, failed-format rejection, exact mount origins/options and cleanup
at each bind/remount failure. Check actual root artifacts contain the complete
authenticated metadata, not just status files. In private file-backed builder
mounts, require database writes to fail while an unrelated state log remains
writable, and run the packaged ARM `dpkg-query` against the selected view.
Changing slots must change the visible package version without mutating either
root or the covered shared-state database.

These checks would establish software behavior only. Installed A/B switching,
interrupted updates, actual rollback and backup restoration still require the
[hardware acceptance](hardware-validation.md) evidence. The design does not
implement an updater, authorize physical writes or satisfy any release gate.

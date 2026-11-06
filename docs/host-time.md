# Host time synchronization preparation

The current 220-package userspace has no time-sync daemon. The preparation boot
starts in 1970, and HTTPS was verified only after manual UTC correction. Follow
the [current deployment guide](deploy-personalized-20261106.md); its images and
package locks remain unchanged. Automatic host time is an open integration gate.

The [optional package input](evidence/time-sync-package-input-20261106.json) is
`systemd-timesyncd` **252.39-1~deb12u2**, ARM64, 62,172 bytes, SHA-256
`700be69248209d27e58ee0736f1460bc8acb10c1e717efc985ebd23dd5fd7714`.
Its binary index matches the existing signed snapshot release in both compressed
and expanded forms. The archive passed that index's size/hash before extraction.
Existing libc6, libsystemd-shared and systemd satisfy its dependencies; all 220
locked binaries were matched against the signed index, with no conflicting
time-daemon provider. The exact systemd source version is already retained in
the 149-version source archive set. No package was installed or daemon executed.

Upstream [systemd 252 documentation](https://raw.githubusercontent.com/systemd/systemd/v252/man/systemd-timesyncd.service.xml)
describes SNTP synchronization and persistent clock state at
`/var/lib/systemd/timesync/clock`. Its
[unit](https://raw.githubusercontent.com/systemd/systemd/v252/units/systemd-timesyncd.service.in)
uses `StateDirectory=systemd/timesync`, a service user and `CAP_SYS_TIME`.
The intended host location is writable NVMe state through the existing `/var`
bind. Routine SD writes remain prohibited. Starting the service alone does not
prove accurate synchronization; time-sensitive operations need an observed
synchronization result, with offline behavior handled explicitly.

The [stable identity policy and read-only gates](timesync-identity.md) now pass
actual pinned ARM sysusers creation/repeat checks on the builder, with independent
review. UID/GID 996 is reserved for a future profile; published images still have
no timesync account or daemon. Clock-state ownership and A/B transitions remain
unproven.

Before enabling it in a future payload, introduce a separate pinned userspace
profile and regenerate its per-root package metadata. Audit the added service
user and state ownership across A/B updates and rollback. Enable/configure the
unit deliberately, verify actual DNS/NTP reachability on the board, and capture
cold-boot time establishment and clock persistence alongside SD hashes/write
counters. Those integration and hardware steps remain unperformed; package staging and
account fixtures do not pass them.

# Stable time-service identity preparation

Automatic host time remains unimplemented in the published 220-package images.
The [authenticated package input](host-time.md) requests a dynamically assigned
`systemd-timesync` user. A future, separate userspace profile will reserve
**UID/GID 996:996**, keeping persisted NVMe clock-state ownership stable across
roots. This reservation is verified against the normalized base; it is not an
account added to the currently published images.

[`host-timesync-sysusers.conf`](../configs/host-timesync-sysusers.conf) declares
both the group and user explicitly, with home `/` and shell
`/usr/sbin/nologin`. The [systemd 252 sysusers format](https://raw.githubusercontent.com/systemd/systemd/v252/man/sysusers.d.xml)
requires an explicitly declared or preexisting group when specifying `uid:gid`.
The future profile must install this as `/etc/sysusers.d/systemd-timesync.conf`
before package configuration, overriding the package's same-named vendor file.
It must run the normal package installation and regenerate its package-state
snapshot. Copying the executable or just creating an account does not install
the package or enable its service.

## Read-only gate

[`timesync-identity.py`](../build/timesync-identity.py) authenticates the exact
policy bytes and reads an offline root through physical directory descriptors,
without following symlinks. It bounds inputs, rejects duplicate/malformed
accounts and detects changes while reading. It never changes account files,
runs sysusers, enables a unit or sets a clock.

For a private offline root:

```sh
python3 -I -B build/timesync-identity.py /ABSOLUTE/OFFLINE/ROOT --phase before
python3 -I -B build/timesync-identity.py /ABSOLUTE/OFFLINE/ROOT --phase after
```

These are interface examples, not commands to run against the installed board.
The first requires the reserved name, UID and GID to be unused, including any
other user's primary GID. The second requires the exact service account/group
and rejects aliases or supplementary membership. The Python build interface
`compare_accounts(before_root, after_root)` also requires every prior account
tuple to remain unchanged. Reports contain only identity and file hashes;
errors do not echo account fields. The baseline already assigns 998 to
systemd-network, 997 to systemd-resolve and group 999 to systemd-journal.

## Actual ARM fixture

[Evidence](evidence/timesync-identity-arm-20261106.json) binds source commit
`b0c3ba6`, policy, verification scripts, selected runtime bytes and independent
Sol/high review. On the prepared Ubuntu 22.04 builder, the real ARM64
`systemd-sysusers` 252.39-1~deb12u2 ran through the existing pinned QEMU build tool.
The fixture contains only the normalized archive's account databases and the
ELF dependency closure needed by sysusers. It does not clone a full root or
install packages. The subprocess had no-new-privileges and only CAP_CHOWN in its
capability bounding set; it could not set the host clock.

Creation produced the exact 996:996 user/group and preserved all previous tuples.
A repeat run preserved the contents of passwd, group, shadow and gshadow.
Deliberate UID/GID collisions were rejected without modifying those files.
All three ARM commands returned zero; outer host package inventories match.
Free disk was 3,262,922,752 bytes before staging and 3,247,067,136 afterward,
above the fixture's 3 GiB reserve. This small fixture does not change the larger
production build guards. Nineteen focused cases pass; full discovery passes
1,178 cases with 97 environment skips, four assembly cases and five shell suites.

This establishes account creation and read-only audit behavior only. It does not
prove clock-state file ownership, A/B switching, service boot, NTP reachability,
real KVM or zero SD writes. The first broad runtime-selection attempt rejected
an unsupported library alias before execution; the corrected fixture selects
only the sysusers ELF dependency closure. Its retained records identify both
preparation scripts; no failed attempt is counted as a successful run.

The separate [checkpoint guard and private view fixture](timesync-state.md) now
verify file-backed ownership and timestamp preservation. They do not establish
installed boot or actual updater/rollback behavior.

## Remaining integration

Build a separately pinned 221-package profile with systemd-timesyncd installed
through the authenticated offline package path. Apply the fixed policy before
its maintainer script runs, enforce the before/after/comparison gates, and
regenerate per-root package metadata. Existing normalized/default profile locks
and their producer recipes remain unchanged.

The service's `StateDirectory=systemd/timesync` places persisted state under
`/var/lib/systemd/timesync`; the host must expose this through writable NVMe
`/var`. The [systemd execution contract](https://raw.githubusercontent.com/systemd/systemd/v252/man/systemd.exec.xml)
describes state-directory ownership and mount dependencies. Test those actual
mounts and clock-file ownership across new-root boot, old-root rollback and
new-root reactivation, preserving previous identities and clock state. An old
root without the service must not repurpose numeric identity 996.

Enable the service deliberately in the future root. The
[time service documentation](https://raw.githubusercontent.com/systemd/systemd/v252/man/systemd-timesyncd.service.xml)
distinguishes restoring approximate time from the saved clock file from
synchronizing against a reference server. Capture an observed synchronization
result before declaring HTTPS time readiness; successful service startup alone
is insufficient. Finally reproduce cold boots, offline behavior and clock
persistence on hardware with full SD hashes, write counters and firmware audit.

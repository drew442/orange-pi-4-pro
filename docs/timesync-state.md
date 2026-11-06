# Persisted time-state compatibility preparation

The future time-service profile must keep its clock checkpoint on NVMe through
writable `/var`, with the [fixed 996:996 identity](timesync-identity.md). Published
images still contain neither the daemon nor this new build-time guard. No
physical time synchronization or installed rollback has passed.

[`timesync-state.py`](../build/timesync-state.py) audits private, quiescent
installation state without initializing or changing it. It recognizes the
existing `.a733-state` marker and canonical nonzero UUID, checks physical paths
and ownership through `var/lib/systemd`, and permits an absent checkpoint before
first use. An existing `timesync` directory must be owned by 996:996 with mode
0755 and contain only an optional empty, single-linked, regular `clock` file,
owned by 996:996 with mode 0644. The saved timestamp must be nonnegative; future
timestamps are preserved rather than clamped or treated as synchronized time.

The [systemd v252 initialization source](https://raw.githubusercontent.com/systemd/systemd/v252/src/timesync/timesyncd.c)
creates the directory/checkpoint with these modes and restores time from its
mtime. The [manager source](https://raw.githubusercontent.com/systemd/systemd/v252/src/timesync/timesyncd-manager.c)
updates that timestamp after synchronization. This audit checks a deliberately
strict checkpoint format; it never sets the system clock or starts a service.

## Read-only interface

For a private offline state tree, initial-root interface examples are:

```sh
sudo python3 -I -B build/timesync-state.py /ABSOLUTE/OFFLINE/STATE
sudo python3 -I -B build/timesync-state.py /ABSOLUTE/OFFLINE/STATE --require-clock
```

The first reports `uninitialized` if the directory or checkpoint is absent. The
second requires an initialized checkpoint. Neither checks NTP or proves that the
state tree is backed by the intended physical NVMe. Existing installation and
mount-identity guards remain necessary. Do not audit a running daemon's mutable
state with this quiescent-input interface.

The implementation pins all path components and rechecks metadata before
returning. Data/directory reads require `O_NOATIME`; clock inspection uses
`O_PATH`. The marker is checked as a safe regular inode before any readable open,
then reopened through its pinned descriptor in verified Linux procfs. Missing
procfs verification, mismatched PID namespaces, changed paths and unexpected
state fail closed. The report exposes only the state UUID, hashes, metadata and
saved mtime. It neither reads clock content nor touches accounts.

## Executed fixture and limits

[Evidence](evidence/timesync-state-views-20261106.json) binds commit `091950c`,
source hashes, protected root staging and independent Sol/high review. The
prepared builder ran a tiny fixture inside a separate, recursively private
mount namespace. It bind-mounted one generated state `/var` into two tiny root
views in order **old → new → old → new**. These views contain the previously
verified baseline/new account databases, not complete bootable root images.
Its dummy state UUID is unrelated to the installation UUID.

All four views retained the same checkpoint inode, owner 996:996 and mode 0644.
Only new-view writer processes updated its file timestamp. Those writers ran
as UID/GID 996 with no supplementary groups, no capabilities and
no-new-privileges. The final future timestamp `1800000003000000000` remained
unchanged by audits and old views. This is a file mtime, not measured clock
accuracy or a host clock change.

Six negative cases refused missing required state, wrong ownership, hardlink,
symlink, an extra directory entry and wrong mode. Fixture mounts were removed;
the initial mount namespace and outer package inventory remained unchanged.
Free space went from 3,246,858,240 to 3,246,751,744 bytes, above the 3 GiB reserve.
The larger production image guards were unchanged. Native cleanup checks also
confirmed normal completion sends no process-group signal, and timeout cleanup
holds its unreaped leader's identity until group signals finish.

Twenty-five focused tests pass. Full native discovery passes 1,203 cases with
97 environment skips, plus four assembly cases and all five shell suites.
Independent read-only remote postchecks verified the retained files and state.

This fixture establishes checkpoint metadata, UID permission and file-backed
view behavior only. It does not execute the installed initramfs, management
service, root updater or rollback procedure. A separate pinned 221-package
profile still needs installation, enablement, package-state regeneration, actual
root/state composition and full artifact readback. Hardware must then prove
service startup, DNS/NTP, cold/offline clock behavior, real rollback and zero
routine SD writes. Physical KVM and all other release gates remain mandatory.

# Private API filesystems for userspace reconstruction

The [actual API preflight](evidence/userspace-api-preflight-20261106.json) passes
inside the frozen Ubuntu 22.04 builder on `build.drewnet.online`, with host
kernel 6.8.0-138-generic. ARM commands read the supplied proc/sysfs paths and
finish with `API_ARM_OK`. This prepares a bootstrap mechanism; no debootstrap
stage or package maintainer script has run.

The build payload keeps its private UID mapping, PID/network namespaces,
read-only builder OS, no-new-privileges setting and seven prohibited capability
drops. A separate trusted helper running as host root enters the payload’s mount, PID, network, UTS,
IPC and cgroup namespaces while retaining the initial host user namespace.
It performs only reviewed setup and exits before the launcher releases the
waiting payload. This helper is privileged; the payload does not receive those
privileges.

The helper verifies namespace identities, target ownership and empty mount
points before changing mounts. It binds one known empty, root-owned mode-000
ordinary file over five sensitive proc files: kcore, keys, kallsyms,
sysrq-trigger and timer_list. A path absent from proc is recorded and must
remain absent; all five were present in the successful run. It then recursively
copies the private proc tree and nspawn's filtered sysfs tree into the raw ARM
root. All 26 destination mounts are private, read-only, nosuid, nodev and
noexec. The existing empty firmware mask and all five new file-mask inode
identities survive the copy. Source records remain unchanged during copying,
after the explicit masking step.

The launcher binds the helper's authenticated receipt and successful exit to an
atomic continuation file. The restricted payload independently checks mount
records, masks, its namespace-relative self PID/status and filesystem identity,
and the absence of the host driver's PID. The trusted helper checks PID 1's
namespace link; the restricted payload cannot read that link and does not claim
to have done so. Its checks use authenticated helper anchors instead. ARM reads
of `/proc/self/status`, `/proc/1/status`, `/proc/filesystems` and the loopback
sysfs path succeed with empty stderr. Real device creation remains denied.

Independent code and execution-record reviews pass, including twelve focused
tests that perform no mounts. Five resource samples stay above 5,809,790,976 bytes free disk
and 7,474,872,320 bytes available RAM. Host package inventories are identical;
exits leave no mapped processes or output mounts. Raw records, reviewed code
and both cleanly terminated failed attempts are retained under ignored
`out/userspace-api-preflight-20261106/` with recorded hashes.

The failed attempts established two constraints: nspawn settings alone did not
provide the required proc-file masks, and restricted PID 1 namespace-link
access is denied. The final run installs and verifies masks explicitly and
checks private proc rendering without adding payload capabilities. It also
enters the cgroup namespace so helper and payload mount-root views agree.

Pinned references explain the observed behavior:

- [nspawn 249.11 mount setup](https://github.com/systemd/systemd-stable/blob/v249.11/src/nspawn/nspawn-mount.c): private proc setup happens in the inner child; sysfs uses a filtered tmpfs skeleton. Optional proc-mask mount failures are not fatal.
- [Linux 6.8 proc self link](https://github.com/torvalds/linux/blob/v6.8/fs/proc/self.c) and [status rendering](https://github.com/torvalds/linux/blob/v6.8/fs/proc/array.c): PID values use the proc mount's PID namespace.
- [Linux 6.8 tmpfs mount options](https://github.com/torvalds/linux/blob/v6.8/mm/shmem.c): ownership options use the initial user namespace, so this preflight retains exact ownership-option comparisons.

The next step is the reviewed offline bootstrap adapter described in
[userspace reconstruction preparation](userspace-rebuild-preparation.md).
Device/proc setup and cleanup assumptions in stock debootstrap still need
adaptation, followed by complete package configuration and inventory audits.
Production root/state/NVMe builds remain below their staging capacity guards.
Physical installation, real KVM guest execution and release acceptance remain open.

# Offline userspace reconstruction

The [authenticated mirror and foreign extraction run](evidence/userspace-foreign-20261106.json)
passes inside the frozen Ubuntu 22.04 builder. The subsequent
[configuration and cleaned export](evidence/userspace-configured-export-20261106.json)
now also pass for all 220 locked packages. Current installation images and the
[operator deployment procedure](deploy-personalized-20261106.md) are unchanged.

[`offline-userspace-repo.py`](../build/offline-userspace-repo.py) privately copies
and authenticates the pinned keyring, InRelease, ARM64 package index and all
220 locked binaries. Real `gpgv` verification passes, including an independent
check on codex. The original signed index remains unchanged; the local pool
contains the locked packages. Dependencies outside that pool must fail offline.
The original rootfs archive is unused as a construction seed.

[`debootstrap-adapter.py`](../build/debootstrap-adapter.py) authenticates four
exact files from debootstrap 1.0.126+nmu1ubuntu0.9 in the frozen builder. It keeps
the executable and suite scripts byte-identical and appends three narrow
function overrides. The source package version is pinned in the
[builder package lock](../configs/builder-replay-packages-20261106.lock.tsv);
file hashes and the override policy are recorded in each adapter manifest.
Source files remain untouched. New outputs reject symlinks, ambiguous paths,
pseudo filesystems and aliases of protected source paths.

The actual driver called `construct(cache, packages_xz, output)` and
`prepare(output)` with the recorded private namespace paths. It then ran this
foreign command inside the validated namespace:

```sh
DEBOOTSTRAP_DIR=/a733-export-output/adapter \
/bin/sh /a733-export-output/adapter/debootstrap \
  --arch=arm64 --variant=minbase --foreign --force-check-gpg \
  --keyring=/a733-export-output/repo/verified-keyring.gpg \
  --include=ca-certificates bookworm /a733-export-output/arm-root \
  file:///a733-export-output/repo
```

The successful phase is retained at
`/var/lib/a733-clean-replay-20261106/userspace-foreign-01` on the builder.
Its input snapshot, complete namespace command and driver hashes are retained
with the evidence. Existing outputs cannot be overwritten; another build needs
a fresh phase. No ARM maintainer scripts ran: 99 packages were cached, 96 required
archives extracted, and the target dpkg status file remains empty. Device paths
are eight exclusive ordinary zero-length mode-0666 placeholders. Their declared
major/minor values support later archive construction; they provide no device
readiness evidence.

## Configured userspace and export

The adapter's second-stage guards now pass actual ARM execution in the private
builder namespace. The successful configuration phase is
`/var/lib/a733-clean-replay-20261106/userspace-configure-01` on the builder.
It clones the authenticated foreign tree and mirror, verifies every mirror file,
and injects the pinned static QEMU interpreter only as a build tool.
No original rootfs archive seeds this construction.

[`userspace-bootstrap-api.py`](../build/userspace-bootstrap-api.py) validates a
nonce-bound request and namespace anchors before trusted root setup. The helper
enters the payload's mount/PID/network/UTS/IPC/cgroup namespaces while retaining
the initial user namespace. It supplies seven character handles, private pts/shm,
filtered read-only proc/sys views and a read-only repository, including its source
alias. Console remains an ordinary placeholder. All 54 mount operations finish
and the helper exits before the restricted ARM driver continues. The payload
keeps its seven capability drops and no-new-privileges setting; no block devices
or `/dev/kvm` are exposed. The namespace's mounts disappear on exit.

The driver runs stock ARM debootstrap second stage, then authenticated offline
APT with exact `package=version` requests. Its 99 base packages and complete
220-package inventory match their locks. `dpkg --audit` is empty and `apt-get
check` succeeds. Restore `policy-rc.d` returning 101 after stock debootstrap
removes it; retain the disable-all systemd preset through installation. No daemon
remains at the final namespace check. Two APT warnings record fallback from
`_apt` to container root because the cloned root directory was mode `0700`.
Signature enforcement remained active. Future configuration should set the root
to `0755` before APT; this run corrected it during export.

[`export-host-userspace.py`](../build/export-host-userspace.py) requires the
same fixed UID map, Ubuntu release and capability restrictions, the exact package
inventory and an authenticated successful configure receipt. With all temporary
mounts gone, it removes generated SSH host keys, machine identity, caches, logs,
random seeds and build-only policy/interpreter files. It leaves the root password
locked and the machine ID empty. Cleanup is restricted to the configured build
clone; device placeholders remain ordinary files on disk.

The exporter emits eight character-device **archive headers** without creating
real nodes. Its deterministic streaming PAX/gzip writer preserves native
ownership, modes, nanosecond timestamps, hardlinks, symlinks and raw xattrs. It
compares every member's metadata/content to a complete BOM, consumes the entire
compressed stream, rechecks the source, and publishes only after verification.
The 3 GiB free-space floor remains enforced. This establishes one verified export,
not repeatable byte identity across independently configured roots: package setup
can generate timestamps and other transient state.

The new archive is retained on codex at
`out/userspace-export-20261106/rootfs.tar.gz`:

- Compressed: **165,687,301 bytes**, SHA-256
  `1034908833bd6faf934114a6c0c96840db815ba077166438159dcca54a1fe1a7`.
- Raw tar: **539,678,720 bytes**, SHA-256
  `2adb2a05e00df632d057eb812f17202efc84cef5fc86a16efb8e87dd829b81ef`.
- Complete BOM: **15,881 entries**, including eight character headers.

Independent Sol high review passes both recipe/isolation checks and a separate
standalone full archive/BOM readback. All 220 configured tuples, credential
cleanup, three hardlinks and the capability xattr pass. Current native regressions
pass 1,046 cases (95 environment skips), four assembly cases and five shell suites.
Configuration and export retain unchanged host inventories and no leftover mapped
processes/mounts. Resource minima and exact driver hashes are in the evidence.

Actual input snapshots, driver/launcher code, command logs and receipts remain
under `out/userspace-configure-20261106/` and
`out/userspace-export-20261106/`. The evidence binds their hashes. These ignored
records supplement the tracked helper/exporter recipes. The subsequent
[tracked configuration replay](userspace-configuration-replay.md) now passes an
actual second configuration with committed source snapshots and no APT fallback
warnings. Its second archive has the same path set but differing timestamps and
four generated identity/cache files; the comparison is documented there. To repeat
the export in an identically prepared namespace, stage the exporter under a
project directory containing the pinned package lock, then run:

```sh
python3 -I -B /a733-sources/project/build/export-host-userspace.py \
  --output /a733-export-output/export-NEW-NAME \
  --configure-receipt-sha256 94fe0ea170430035802b4a64c46b7dc4639ba7cf8f17fb45f84cddf9ba97c11f
```

That hash authenticates this specific configuration; a fresh construction must
use its own independently verified receipt. The fixed source is
`/a733-export-output/arm-root`, with no active source mounts. Existing output
names cannot be overwritten. Do not run this cleanup against a deployed host or
the retained foreign-stage source. Source cleanup already occurs before archive
publication; a failed export requires inspecting the clone before retrying.

The preceding foreign-stage implementation, driver and actual-record reviews pass. All 669
repository BOM files and all 220 package hashes/control receipts were rechecked
on codex. The complete 106,858,274-byte repository archive is retained under
`out/userspace-foreign-20261106/`; the target tree remains on the builder.
That stage passed 1,004 discovery cases with 95 environment skips, four
assembly cases and all five shell suites. Thirteen build samples stay above
5,407,723,520 bytes free disk and 7,445,274,624 bytes available RAM. Host package
inventory and payload restrictions are unchanged; process/mount cleanup passes.

This builds from pinned binary packages. Source recompilation, independently
repeated userspace construction, production root/state/NVMe reconstruction, physical
installation and actual KVM guest acceptance remain separate open work. The new
archive does not change the published installation images or their hashes.

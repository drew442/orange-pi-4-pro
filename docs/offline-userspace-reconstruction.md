# Offline userspace reconstruction

The [authenticated mirror and foreign extraction run](evidence/userspace-foreign-20261106.json)
passes inside the frozen Ubuntu 22.04 builder. Complete configured userspace
reconstruction remains open. Current installation images and the
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

The adapter also has strict second-stage guards for actual character devices,
private proc/sysfs mounts, namespace-relative PID rendering, no-new-privileges
and prohibited capabilities. Those guards have fixture coverage, but their
complete ARM bootstrap use remains unperformed. The next step must integrate
trusted device/API setup after foreign extraction and before any maintainer
script. That setup must finish and exit before continuation. Preserve the
[verified private API mechanism](userspace-api-preflight.md), provide the seven
required real device handles, and adapt mount cleanup without restoring payload
capabilities or spoofing container identity. Keep service suppression active
through package installation, then verify all 220 configured tuples and audits
before stripping per-machine state and producing a new userspace archive.

Independent implementation, driver and actual-record reviews pass. All 669
repository BOM files and all 220 package hashes/control receipts were rechecked
on codex. The complete 106,858,274-byte repository archive is retained under
`out/userspace-foreign-20261106/`; the target tree remains on the builder.
Native regressions pass: 1,004 discovery cases with 95 environment skips, four
assembly cases and all five shell suites. Thirteen build samples stay above
5,407,723,520 bytes free disk and 7,445,274,624 bytes available RAM. Host package
inventory and payload restrictions are unchanged; process/mount cleanup passes.

This builds from pinned binary packages. Source recompilation, complete package
configuration, final userspace export, production root/state/NVMe reconstruction,
physical installation and actual KVM guest acceptance remain separate open work.

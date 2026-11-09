# Corrected production host roots

Both 4 GiB NVMe root slots now contain the reviewed OverlayFS guest-account fix,
explicit disabling of the unused virtual NIC PXE ROM, and a pre-PID-1 mask for
`systemd-machine-id-commit.service`. Construction and independent artifact
inspection pass. **These are components, not installation-ready images.**
These exact roots now back the protected full-image candidate and running
host. [Current matched maintenance candidates](matched-host-updates-20261109.md)
are retained; physical update/rollback testing remains pending.
New [resolver-readable root components](public-config-roots-20261109.md) are
constructed separately; these historical roots retain the resolver-directory
finding. Their artifacts and evidence are not relabeled.

[Evidence and exact artifact pins](evidence/production-host-roots-runtime-fixes-20261108.json)
bind source `f40c151fb8957db41ede80fbd758880a5d901efe`, all 676 committed files,
the same pinned 138,093,095-byte time-enabled userspace archive, 221 packages,
clock UID/GID 996 and the original compatible kernel, DTB and modules. No new
packages were installed. The 40-file maintenance closure retains host-GIC
selection and KVM-only CPU execution; the GICv2 diagnostic override is not
packaged. The separately produced VGIC DT change now boots with real KVM
execution; preserve the [current external boot stack](current-images.md).

## Construction and inspection

The source-generic launcher needed no new target or weakened input guard.
A successful `--plan` preceded this actual Ubuntu 22.04 build:

```sh
sudo python3 -I -B \
  /var/lib/a733-host-runtime-fixes-20261108/project/build/launch-host-roots.py \
  --project /var/lib/a733-host-runtime-fixes-20261108/project \
  --tag timesync-runtime-fixes-01 \
  --userspace /var/lib/a733-clean-replay-20261106/userspace-timesync-account-dates-01/work/export-timesync-result/rootfs.tar.gz \
  --diagnostic /var/lib/a733-bootstrap-inputs-20261107
```

An existing phase refuses reuse; keep its evidence and select a fresh tag for
a later construction. The consumed phase is
`/var/lib/a733-clean-replay-20261106/host-roots-timesync-runtime-fixes-01`.
Its payload remains under
`work/project/out/host-payload-timesync-runtime-fixes-01`.
The two compressed roots are 143,469,395 and 143,469,243 bytes. Each expands to
exactly 4,294,967,296 bytes, retaining its fixed slot UUID and immutable,
no-journal filesystem format.

The separate full reader ran successfully:

```sh
sudo python3 -I -B \
  /var/lib/a733-host-runtime-fixes-20261108/project/build/verify-host-roots.py \
  --receipt /var/lib/a733-clean-replay-20261106/host-roots-timesync-runtime-fixes-01/receipt.json \
  --receipt-sha256 12a0bb104f29935f75be49e936d120e410a335ee032a3dd486e0f3cf112b6ba2 \
  --output /var/lib/a733-host-roots-runtime-fixes-readback-20261108-01
```

It verified all 16,368 expected paths per slot, regular content, links,
ownership, permissions, device metadata and raw capabilities. It reads regular
image files without mounting or repairing them. Independent Sol/high review
also rehashed all 86 payload artifacts and complete gzip expansions, extracted
the 40-file closure and critical configuration directly from each image, and
reconstructed both payload markers from committed source and manifest data.

Compared with the [previous root repeat](production-host-roots-repeat.md), each
slot adds one root-owned `/etc/systemd/system/systemd-machine-id-commit.service`
symlink to `/dev/null`. Exactly five file contents change: the two corrected
tools, their two provenance copies and the payload marker. Other common-path
content, ownership, permissions, links, devices and attributes match. The vendor
unit and empty mode-0444 root-placeholder machine-id remain unchanged. At boot,
the existing identity policy binds the persistent state file at mode 0644.

There are 464 changed common-path modification times. Filesystem creation fields
and directory hash seeds remain construction-dependent; no byte-identical root
reproduction is claimed. Full readback excludes atime, ctime and crtime and
preserves source modification times only to whole seconds. Those limits are
separate from the verified content and intended source changes.

At the November 8 construction checkpoint, admission required 19,000,336,605
bytes; sampled minima were 19,261,460,480 bytes free disk and 7,521,140,736 bytes
available RAM. The complete 842-package builder inventory was unchanged and no
mapped process or phase mount remained. Construction ended with 19,751,702,528
free bytes. Those are historical construction measurements. Use fresh resources
and the larger current guard for another full-NVMe build. Subsequent
[authorized builder growth](build-host-capacity-addition-20261109.md) completed;
the operator approves available free builder capacity for further use/expansion
without another permission request, retaining exact-target validation and review.

## Retention and subsequent integration

Native metadata, expected trees and full-reader logs remain in
`out/host-runtime-fixes-20261108`. Original raw and compressed roots remain on
the builder. Both current compressed roots and matching update metadata are now
also retained on codex; see [matched maintenance inputs](matched-host-updates-20261109.md).
These file-only preparations did not change the installed host or SD.

The [matched state](production-host-state-runtime-fixes.md) and
[bootstrap](production-host-bootstrap-runtime-fixes.md) passed construction and
independent inspection. Later [protected full-NVMe construction](protected-state-nvme-image.md)
passes two matching streams and independent full artifact comparison. Preserve
the separate historical registrations; do not relabel old candidates as current.

Current protected boot, automatic time and actual KVM CPU/disk/network execution
pass; the fresh 24-hour workload remains active. Physical inactive-slot update,
interruption, booted rollback/recovery and independently reproduced full deployment
remain open. Use the guarded matched maintenance path only after workload evidence
and required backups are retained. These root-component build results alone do
not establish the remaining [hardware acceptance](hardware-validation.md).

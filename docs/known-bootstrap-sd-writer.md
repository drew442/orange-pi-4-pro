# Known bootstrap SD maintenance writer

`scripts/write-known-bootstrap-sd.py` is a Python 3.11 standard-library helper
for the coordinator's maintenance operation in the private SSH RAM installer.
It has no external binary dependencies. Kernel initramfs starts `/` at mode 1777;
normalize that RAM-only directory to 0755 before maintenance.

In this RAM installer, verify the root filesystem before changing its mode:

```sh
awk '$1 == "rootfs" && $2 == "/" && $3 == "rootfs" {ok=1} END {exit !ok}' /proc/mounts && chmod 0755 /
```

This changes RAM metadata only; no physical filesystem is mounted or written. It only accepts the known Orange Pi
4 Pro A733 SD `/dev/mmcblk1`, device `179:0`, model `SU02G`, CID
`035344535530324780716f216e00a8f5`, actual size **1,977,614,336 bytes**,
with 512-byte logical sectors. There are no alternate device or hash switches.
It never selects NVMe or SPI.

The exact raw bootstrap input is **268,435,456 bytes**, SHA-256
`f02c74b4c001f1608d0005b6ff23fe82bffa8c02f71e19e5b6573819125ae383`.
Its builder location is
`/var/lib/a733-vgic-runtime-bootstrap-project-20261108-01/project/out/host-sd-vgic-runtime-corrected-01/a733-host-sd.img`.
This corrected host bootstrap selects state `360d…` and remains **UNBOOTED**.
The source firmware is unchanged. Consult the evolving status document for the
matched full-NVMe construction state and hardware gates.

The known-target write is covered by the operator's persisted
[storage authorization](evidence/storage-write-authorization-20261108.json).
The helper's `--write` flag explicitly selects that maintenance operation;
there is no additional prompt. This helper is not a general disk installer.
Independent source review passes. Live plan checks remain required before its first write. Host tests do not prove
hardware behavior.

## Preparation and commands

The operator/coordinator must confirm the live RAM-installer session and pin
its exact `/proc/sys/kernel/random/boot_id` from the intended session. Passing
an arbitrary freshly read boot ID without checking the session defeats the
caller pin's purpose. Do not invoke this against a normally running host.

Stage the authenticated raw input on **tmpfs** under `/run/a733-payload`, with
a root-owned **0700** directory and a root-owned **0600**, single-link regular
file. `/run` itself must be tmpfs or the directory must be a verified dedicated
tmpfs mount. Confirm available RAM before transfer; the image requires 256 MiB
and is held on RAM storage throughout. Keep ancestors root-owned and without
group/other write permissions. Symlinks and `/dev/shm` are intentionally
rejected. Transfer the writer separately; it requires no adjacent imports.
The writer verifies the full pinned hash before opening the SD for writing.

Using the actual intended boot ID in place of the example placeholder:

```text
python3 -B /run/a733-payload/write-known-bootstrap-sd.py \
  /run/a733-payload/a733-host-sd.img \
  --expected-boot-id ACTUAL-INTENDED-BOOT-UUID

python3 -B /run/a733-payload/write-known-bootstrap-sd.py \
  /run/a733-payload/a733-host-sd.img \
  --expected-boot-id ACTUAL-INTENDED-BOOT-UUID \
  --write --report /run/a733-payload/sd-write-result.json
```

Without `--write`, all safety probes and full source authentication run and
stdout contains the JSON plan. With `--write`, the plan appears on stderr
before opening the target; stdout contains the final JSON result. The optional
report path must be fresh, in a protected tmpfs directory under `/run`, and
cannot replace an existing file. Progress and explicit flush status go to
stderr. Do not pipeline the source: a held, seekable, authenticated file is
required. A refusal or error exits nonzero. Any failed write may leave the SD
partially changed; stdout reports an unknown byte count conservatively.

## Guards and scope

Before execution, the helper requires root, the caller's boot ID, PID/mount
namespace equality with PID 1, a visible proc process census, and an unhidden
proc mount. It reads the live SD CID, model, size, logical sector size and
read-only state from sysfs. It derives partition device numbers from sysfs and
rejects mounts, swap, holders, and open block-device descriptors for the SD
or any partition, including read-only descriptors. Failed probes, permission
errors and malformed input are fatal. Processes or descriptors that disappear
during the census are allowed to disappear; this does not hide permission
failures.

The source's descriptor and path must retain the full original identity after
hashing and immediately before writing. The target is revalidated before and
after opening, with `O_NOFOLLOW`, Linux's block-device `O_EXCL` claim and a
nonblocking exclusive flock. Descriptor ioctls independently require the exact
size, logical sector size and writable state. The idle check runs again after
opening, excluding only the writer's own target descriptor.

The helper streams exactly the first **256 MiB**, writing zero bytes normally
and handling short writes. It never writes beyond that boundary, performs
`fsync` then `BLKFLSBUF`, and does not read back the SD. Readback, if required
for troubleshooting, is a separate explicit operation. It does not reboot,
mount filesystems, change partition tables beyond the pinned image's bytes,
or establish physical boot/guest acceptance. A successful result always says
`hardware_acceptance: false`.

This is a maintenance guard for a trusted, quiescent root RAM installer.
Namespace checks and process censuses do not constitute defense against a
hostile root process, and census snapshots cannot prevent an unrelated process
from opening a device later. Keep the RAM installer quiescent throughout the
operation. The block-device claim and repeat checks reduce accidental races;
physical acceptance still requires the project's real board and KVM tests.

Run the tiny host regressions with:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/test_known_bootstrap_sd_writer.py
```

Tests use mock proc/sysfs/device probes and tiny regular-file stream fixtures;
they never connect to hardware, hash a full native image, or write block devices.

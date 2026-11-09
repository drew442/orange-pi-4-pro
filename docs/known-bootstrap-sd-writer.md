# Known bootstrap SD maintenance writer

`scripts/write-known-bootstrap-sd.py` is a Python standard-library helper for
explicit coordinator SD maintenance. It accepts a quiescent NVMe-rooted host or
the private SSH RAM installer, with the SD unused and the source held on tmpfs.
The current 24-hour workload must finish and its outcome be retained before any
SD write. The helper does not stop workloads or reboot the board.

Its reviewed source SHA-256 is
`4769001ab4a4861ff8ffe26ad6aa427f9abe5430b48bb49918d6c1206b96c52a`.
It has no external binary dependencies. In the RAM installer, initramfs starts
`/` at mode 1777; normalize that RAM-only directory to 0755 before maintenance.

In this RAM installer, verify the root filesystem before changing its mode:

```sh
awk '$1 == "rootfs" && $2 == "/" && $3 == "rootfs" {ok=1} END {exit !ok}' /proc/mounts && chmod 0755 /
```

This changes RAM metadata only; no physical filesystem is mounted or written. It only accepts the known Orange Pi
4 Pro A733 SD `/dev/mmcblk1`, device `179:0`, model `SU02G`, CID
`035344535530324780716f216e00a8f5`, actual size **1,977,614,336 bytes**,
with 512-byte logical sectors. There are no alternate device or hash switches.
It never selects NVMe or SPI.

All three finite profiles write exactly **268,435,456 bytes**:

| Explicit `--profile` | Raw SHA-256 |
| --- | --- |
| `protected-state-20261109` (default) | `ab5e8c2c846cbbd51b61bef6831d7e4d1498c29fefbf47d74301b1f3bb0ad207` |
| `nvme-first-trial-20261109` | `8470ff59290016bb2f99c051f371ab20ac431e75fd17dec4e0e0de5c665613d9` |
| `ssh-ram-recovery-20261108` | `d3a02224efdb2c2eeccc7115d84cd6c4b2d75c7d06580b8f7812d12a546bc3f6` |

The protected profile's raw bootstrap input has SHA-256
`ab5e8c2c846cbbd51b61bef6831d7e4d1498c29fefbf47d74301b1f3bb0ad207`.
Its builder location is
`/var/lib/a733-maintenance-validation-20261108-01/project/out/protected-state-bootstrap-20261109-01/a733-host-sd.img`.
This protected STATE bootstrap selects state `360d…` and now boots physically;
see the [maintenance receipt](evidence/protected-state-maintenance-20261109.json).
It replaces the historical `f02c…` input pin; that earlier image physically
booted, but did not block writes during its initial filesystem probe.
Its firmware is unchanged. The explicit trial profile uses the separately
[reviewed matched trial image](matched-nvme-trial-sd-20261109.md), preserving SD
boot0 and BOOT while changing the registered U-Boot package. It remains unbooted.
The profile is never inferred from a filename; the selected pin applies to
source authentication, plans, writes and receipts. There is no hash override.

The recovery profile writes the already [physically tested private SSH RAM
image](ssh-installer-remote-boot-20261108.md). It contains this board's original
private SSH host key and must be used only on this board. Keep the artifact
private. It starts an independent RAM environment for maintenance; it does not
automatically install NVMe. Its addition and artifact verification have separate
[review evidence](evidence/known-sd-recovery-profile-20261109.json).

The known-target write is covered by the operator's persisted
[storage authorization](evidence/storage-write-authorization-20261108.json).
The helper's `--write` flag explicitly selects that maintenance operation;
there is no additional prompt. This helper is not a general disk installer.
An earlier helper physically wrote the protected SD successfully. The expanded
writer passes independent software review and read-only namespace/target/census
checks on the installed A733. New-profile source plans, maintenance writes and
boot acceptance remain separate. [Preparation evidence](evidence/known-sd-trial-writer-preparation-20261109.json).
Live plan checks remain required before every later write.

## Preparation and commands

The operator/coordinator must confirm the intended quiescent host session and pin
its exact `/proc/sys/kernel/random/boot_id` from the intended session. Passing
an arbitrary freshly read boot ID without checking the session defeats the
caller pin's purpose. For SD-only work the installed root can stay on NVMe;
source data must stay on RAM and all SD safety checks must pass. Whole-NVMe
installation retains its separate RAM-recovery requirement.

Stage the authenticated raw input on **tmpfs** under `/run/a733-payload`, with
a root-owned **0700** directory and a root-owned **0600**, single-link regular
file. `/run` itself must be tmpfs or the directory must be a verified dedicated
tmpfs mount. Confirm available RAM before transfer; the image requires 256 MiB
and is held on RAM storage throughout. Keep ancestors root-owned and without
group/other write permissions. Symlinks and `/dev/shm` are intentionally
rejected. Transfer the writer separately; it requires no adjacent imports.
The writer verifies the full pinned hash before opening the SD for writing.
The installed host's `/run` is initially only 64 MiB: after workload retention,
remount that existing tmpfs with sufficient capacity before staging, or use a
new private dedicated tmpfs. Verify available RAM. Do not mount over existing
`/run` contents, which contain SSH runtime state.

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
For the matched trial image, add `--profile nvme-first-trial-20261109` to **both**
invocations. Keep the default or explicitly select `protected-state-20261109`
when restoring the protected bootstrap. Require exit 0 and one complete valid
receipt; report-file presence alone does not establish completion. A report
write failure can leave multiple JSON objects and a nonzero exit.

To enter the private RAM environment after retaining the workload outcome,
select `--profile ssh-ram-recovery-20261108` for both plan and write, using the
recovery raw image. Retain the successful write receipt off-board, then request
a clean reboot. Read the actual RAM-mode DHCP address from serial/leases and
use the pinned host identity; the previous RAM boot used `.115`. The caller
boot UUID for this write belongs to the installed session, before reboot.
Later RAM operations require that new RAM session's independently verified UUID.

## Guards and scope

Before execution, the helper requires root, the caller's boot ID, the known
Linux initial PID/user namespaces, PID 1's mount namespace, a single-threaded
writer and a complete, unrestricted proc mount. `NSpid` is corroboration;
it cannot establish an initial namespace on its own. The constants and proc
layout follow the pinned vendor
[namespace definitions](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/include/linux/proc_ns.h)
and [proc implementation](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/fs/proc/base.c).
It reads the live SD CID, model, size, logical sector size and
read-only state from sysfs. It derives partition device numbers from sysfs and
rejects mounts, swap, holders, and open block-device descriptors for the SD
or any partition, including read-only descriptors in another thread's FD table.
File-backed mappings are checked through canonical `/proc/TID/map_files` paths
even after their FDs close. The mapping device column describes the node's
filesystem, so the checker obtains the actual mapped inode's block-device number.
Failed probes, permission errors and malformed input are fatal. Disappearing
tasks/mappings require corroboration; a surviving unscannable process is refused.

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

This is a maintenance guard for a trusted, quiescent root environment.
Namespace checks and process censuses do not constitute defense against a
hostile root process, and census snapshots cannot prevent an unrelated process
from opening a device later. Keep the RAM installer quiescent throughout the
operation. The block-device claim and repeat checks reduce accidental races;
physical acceptance still requires the project's real board and KVM tests.

Run the tiny host regressions with:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/test_known_bootstrap_sd_writer.py
```

Tests use mock proc/sysfs/device probes, tiny regular-file streams and harmless
real file mappings. They never connect to hardware or write block devices.

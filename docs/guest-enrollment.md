# Guest enrollment preparation

`scripts/enroll-guest.py` prepares and publishes a new guest directory on the
activated NVMe guest filesystem. It authenticates protected seed files, copies
and reads them back, and publishes without replacing any existing object. It
never starts QEMU, installs a unit, formats storage or changes boot selection.
No physical enrollment or KVM execution has been demonstrated.

## Preconditions and configuration

First complete [storage/account activation](guest-activation.md) on the actual
managed host. The configured locked identity must already exist exactly as
specified. Stage the full trusted checkout on NVMe or RAM. Published images and
the maintenance archive do not yet include these enrollment tools.

Supply a root:root, single-link `0400` configuration file on RAM or selected
NVMe state. The exact schema below uses the existing [pinned guest fixture](guest-fixture.md).
The CPU/RAM values are builder test values, not an allocation recommendation
for the unmeasured board:

```json
{
  "schema": 1,
  "name": "fixture",
  "uid": 60001,
  "gid": 60001,
  "memory_mib": 128,
  "memory_max_mib": 256,
  "cpus": 1,
  "kernel": {"bytes": 5251080, "sha256": "1294959bcaa5a5ad3791913e33cffb8f0893708396aac8be9837c03a0e1f9377"},
  "initrd": {"bytes": 973641, "sha256": "62a07e1a5a7c38c5677a4c3750fc479c2636fe9e611aed67c89d3b9623a2c725"},
  "disk": {"bytes": 134217728, "sha256": "0f78f07743e41fec6655b44792284980536f5cd62d08fdcaea0025bb222c6278"}
}
```

Identity rules match the account controller. Unknown/duplicate fields and
noncanonical hashes are refused. Kernel/initramfs are each capped at 256 MiB;
the raw disk must be sector-aligned. Enrollment checks online CPU count,
measured `MemAvailable`, and free p5 capacity before staging. The configured
memory limit reserves at least 64 MiB beyond guest RAM, and admission reserves
another 128 MiB of available host RAM. These arithmetic checks do not establish
real workload overhead, thermal behavior or safe aggregate overcommit.

Seed files must be protected root-owned regular files with one link, safe
ancestors and no group/world writes. They must reside on the selected root,
state or guest NVMe filesystem. Large payloads on RAM or SD are refused. Hash
checks follow placement checks so reads cannot cause SD atime writes. Existing
guest-owned working disks cannot be used as trusted root-owned seeds; this
command is not a live backup tool.

## Prepare and apply a new directory

Prospective usage after actual hardware inventory and activation follows.
These commands have not been reproduced on the board. Use a root shell and
set all paths from reviewed artifacts and the actual installation:

```sh
: "${A733_GUEST_TOOLS:?Set absolute trusted complete checkout directory}"
: "${A733_GUEST_CONFIG:?Set protected enrollment config path}"
: "${A733_GUEST_KERNEL:?Set protected pinned kernel seed path}"
: "${A733_GUEST_INITRD:?Set protected pinned initramfs seed path}"
: "${A733_GUEST_DISK:?Set protected pinned raw disk seed path}"
: "${A733_GUEST_UUID:?Set host NVMe p5 filesystem UUID}"
set -- --config "$A733_GUEST_CONFIG" --kernel "$A733_GUEST_KERNEL" \
  --initrd "$A733_GUEST_INITRD" --disk "$A733_GUEST_DISK" \
  --guest-uuid "$A733_GUEST_UUID"
umask 077
set -C
/usr/bin/python3 -I -B "$A733_GUEST_TOOLS/scripts/enroll-guest.py" \
  prepare "$@" > /run/guest-enrollment-plan.json
chmod 0400 /run/guest-enrollment-plan.json
cat /run/guest-enrollment-plan.json
```

Use a fresh plan filename if it exists. `A733_GUEST_UUID` identifies the host's
p5 filesystem, not the filesystem inside the guest's raw disk. Preparation
creates no guest files. Review the config, held source identities, hashes,
storage and capacity before the separate apply step:

```sh
/usr/bin/python3 -I -B "$A733_GUEST_TOOLS/scripts/enroll-guest.py" \
  apply "$@" --plan /run/guest-enrollment-plan.json
```

Apply repeats preflight under a RAM lock. It creates private staging below
p5's root-only `.enrollment` directory, performs bounded sparse copies and
complete readback, sets ownership and synchronizes files before atomic
no-replace publication. Every existing destination, including a symlink or
empty directory, is refused. Files remain on NVMe; no SD or raw block write
operation is performed by this controller.

The published directory contract is:

| Object | Owner/group | Mode |
| --- | --- | --- |
| `/run/a733-guests/NAME/` | root / guest GID | `0710` |
| `Image`, `initramfs.cpio.gz` | root / root | `0444` |
| `disk.raw` | guest UID / guest GID | `0600` |
| `enrollment.json` | root / root | `0400` |

The guest can modify its disk, but cannot rename assets, create directory
entries, modify the kernel/initramfs or read the protected manifest. The
manifest binds configuration, expected seed hashes, ownership and p5 UUID.
It is an enrollment record, not a health or successful-boot record. Save the
`manifest_sha256` from a successful published/durable receipt in the trusted
installation record before launching the guest.

Receipts distinguish attempted staging, possible publication, visible
publication and directory synchronization. Failure after publication preserves
the published guest; it does not remove it or claim rollback. Before
publication, cleanup removes only owned inode entries. Uncertain or foreign
objects are retained and reported. Inspect the receipt and namespace before
retrying; an existing guest is never silently reused or replaced.

## Supervision preparation and remaining lifecycle work

`configs/guest_config.py` renders a manually started systemd unit from the
validated config, a trusted readable launcher path and the actual KVM group
ID. It does not install or enable the unit. Packaging must make the launcher
and its adjacent `guest-storage.py` readable to the guest identity through
root-controlled ancestors before a unit can run. The actual `/dev/kvm` identity,
permissions and group still need validation on the board.

The rendered unit uses the locked guest user/group, explicit KVM supplementary
group, no capabilities, no new privileges, explicit `MemoryMax`,
`MemorySwapMax=0`, a task limit, disabled core dumps and journal output. It
retains the original mount namespace so the launcher's placement checks see
the expected mount IDs. It introduces no forwarded listener or accelerator
fallback. No unit was started by the software checks.

`Restart=no` is deliberate: the initial disk hash is embedded in the unit. A
successful guest write changes that checkpoint, so another start must refuse
until the [stopped-guest checkpoint controller](guest-checkpoints.md) updates
the trusted record and the unit is regenerated. Automatic boot integration
remains open.
Stopping this preparation unit sends SIGTERM; this is **not** a guest graceful
shutdown protocol. Controlled shutdown, timeout/force policy, consistent
backup/restore and production lifecycle management remain required. The test
guest's own clean power-off does not establish those operations for other guests.

Upstream: [systemd 252 service execution](https://www.freedesktop.org/software/systemd/man/252/systemd.exec.html),
[service lifecycle](https://www.freedesktop.org/software/systemd/man/252/systemd.service.html),
[resource limits](https://www.freedesktop.org/software/systemd/man/252/systemd.resource-control.html),
and [QEMU 7.2 invocation](https://gitlab.com/qemu-project/qemu/-/blob/v7.2.0/qemu-options.hx).

## Verified software evidence

At source `54346ac`, the [ARM enrollment probe](evidence/guest-enrollment-arm-20261104.json)
used the actual pinned 5,251,080-byte kernel, 973,641-byte initramfs and
134,217,728-byte raw disk. It authenticated all inputs, published and read back
140,442,449 bytes, verified ownership, and used real UID/GID changes to prove
allowed disk writes and forbidden asset/manifest/directory access. Another UID
and a repeated enrollment were refused. Source seed hashes stayed unchanged.
The actual pinned systemd 252 unit verifier accepted the rendered unit without
diagnostics. No unit was started.

The trusted disposable fixture mocked live mount/UUID provenance; these results
cannot prove p5 placement, real admission budgets or physical KVM. The probe
used a captured builder memory sample and native CPU count. ARM binfmt checked
the configured interpreter pathname/hash; its kernel-held F descriptor was not
independently inspected. Elapsed 29.95 seconds, maximum RSS 190,928 KiB.

Exact executed builder command from the private source checkout:

```sh
sudo -i
cd /var/lib/a733-guest-enrollment-work-20261104
/usr/bin/python3.10 -I -B tests/verify_guest_enrollment_arm.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/recipe/out/guest-fixture-20261104-03
```

This root-only fixture requires 3 GiB free fixed storage. It creates no block
nodes or mounts and changes no builder host account. Its files are removed at
exit. Actual guest execution and the full [release acceptance](hardware-validation.md)
remain mandatory.

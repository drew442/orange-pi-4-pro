# Guest runtime preparation

The host uses unprivileged QEMU processes with KVM explicitly required. The
current fixture launcher authenticates kernel, initramfs and raw disk inputs,
retains their open descriptors through exec, and coordinates cooperating
launchers with a nonblocking exclusive disk lock. The
[physical A733 guest](kvm-vgic-results-20261108.md) passes CPU, disk, network,
clean shutdown, checkpoint and restart checks. The [corrected-host restore](evidence/corrected-host-guest-restore-20261108.json)
advances the retained counter-3 guest through 4 and 5 using actual nonroot KVM.
Full hardware release acceptance remains open.
The optional [isolated loopback TCP profile](guest-networking.md) adds a single
explicit host forward while preserving the default restricted backend.

## Storage contract

Actual launch requires the selected read-only NVMe root on p2 or p3 and the
same namespace's p5 guest filesystem mounted at `/run/a733-guests`. The target
directory belongs to RAM `/run`; the mounted filesystem and all guest data
belong to NVMe. The ext4 guest mount must be writable with `nodev,nosuid,noexec`
and have filesystem root `/`. `noexec` permits QEMU to read disk images; it
prevents executing host programs directly from this filesystem.

`scripts/guest-storage.py` reads mountinfo, descriptor mount IDs, block-node
metadata and sysfs identity without opening a block device. It rejects SD,
USB/dm backing, another NVMe namespace, p4 state, alternate binds and nested
mounts. The held disk must belong to the exact guest mount. Kernel/initramfs
files must belong to the selected root mount or the same guest mount. This
checks current placement; bootstrap GPT verification and physical identity
acceptance remain separate requirements.

Storage checks precede payload reads because reads on writable filesystems can
update atime. The launcher repeats placement and held-inode metadata checks
before exec. File-only `--dry-run` intentionally skips placement/KVM/QEMU checks
and reports that omission; dry-run reads can update atime. Run builder checks
on fixed builder storage, never use SD for routine guest work.

[Explicit activation controllers](guest-activation.md) now prepare and apply the
p5 mount and locked service accounts. The prepared personalized root artifacts
include these controllers in the complete maintenance tree at
`/usr/lib/a733-tools/scripts/`; the current-root manifest records their
contents. The current protected host has booted with its authenticated
management tool closure; see [installation status](implementation-status.md).
Activation is explicit. The mount controller grants root-directory
traversal only after validating the mounted device.
[Explicit enrollment](guest-enrollment.md) now publishes protected per-guest
directories and working disks. The account helper recreates fixed,
locked identities in RAM `/etc` without administration or disk memberships.
Physical enrollment, account preparation and launch are recorded in the linked
KVM results; new deployments require their own receipts.
See [current managed host roots](current-host-roots.md) for prepared-artifact
status and hardware gates.

## Input lifetime and launch

Keep a trusted immutable fixture seed off the writable working path. Copy the
raw seed to the chosen NVMe guest directory. Supply trusted hashes and measured
CPU/RAM budgets using the [fixture procedure](guest-fixture.md). Retain both
`scripts/launch-kvm-guest.py` and its adjacent `guest-storage.py` when staging
these tools. The prepared personalized root artifacts include both in the
complete maintenance tree at `/usr/lib/a733-tools/scripts/`. See
[current managed host roots](current-host-roots.md). The current protected boot
pair has booted; this does not validate the prospective normalized full-image
installation.

Input leaves must be nonempty regular files with one hard link. Verification
reads exactly the captured size, rejects inode/size/mode/timestamp/link changes,
and closes all held descriptors on refusal. Actual launch opens the disk
read/write, holds its lock and passes only the three input descriptors beyond
stdio to QEMU. Kernel/initramfs remain opened read-only. QEMU receives
`/proc/self/fd/N` paths, so replacing a pathname cannot redirect its later open.
Unrelated descriptors become close-on-exec. Standard input/output/error must be
provided by the console or supervisor; input descriptors never occupy them.

Locking coordinates cooperating launchers. A privileged process or a process
with write permission that ignores locks can still alter bytes in place.
Metadata checks cannot detect every in-place write, including one that leaves
observed timestamps unchanged. Protect immutable inputs against those writers
and never copy, hash or back up
a running guest disk as though it were a consistent checkpoint. Record the new
disk hash only after a clean shutdown and review before the next launch.

The command fixes `virt,gic-version=host`, `-accel kvm`, `-cpu host`, explicit
memory/vCPU counts and a raw file backend. Restricted user networking exposes
no forwarded listener. The linked physical runs prove CPU, disk persistence,
networking, clean shutdown and restart for that installation. Fixture console markers alone do not
establish KVM acceleration.

Upstream references: [QEMU 7.2 invocation and file backends](https://gitlab.com/qemu-project/qemu/-/blob/v7.2.0/qemu-options.hx),
[Python descriptor inheritance](https://docs.python.org/3/library/os.html#inheritance-of-file-descriptors),
[Linux mountinfo fields](https://www.kernel.org/doc/Documentation/filesystems/proc.txt).

## Remaining operational work

Retain a fresh stopped, checkpointed backup of the current counter-5 guest using
[the backup protocol](guest-backups.md) before reinstalling; the physically
restored counter-3 archive omits later data. Preserve completed and failed
endurance guest directories separately, together with native UART, journals and
telemetry. The automatic outcome preserver captures evidence, not guest disks.
Follow [offline STATE and root preservation](host-state-backup.md) before any
journal replay or destructive install. Host STATE restoration and failed-boot
recovery remain separate unvalidated paths.

The [24-hour KVM workload](endurance-run-20261109.md) remains active until no
earlier than November 10 02:11 UTC, with final outcome capture scheduled for
02:16 UTC. Do not interrupt it. Preserve all remaining
[hardware acceptance gates](hardware-validation.md); the specific guest restore
does not establish complete release acceptance or normalized deployment.

## Verified software checks

At source commit `16ad952`, 449 discovery cases passed on Jammy without skips
(two privileged skips locally), plus four separate diagnostic cases and all
shell regressions. Independent Sol high review accepted descriptor lifetime,
placement/refusal ordering and the second storage-check failure regression.
Actual unprivileged [builder refusal](evidence/guest-storage-builder-refusal-20261104.json)
confirms that its unsupported storage namespace is rejected. Fixture positives
do not establish real NVMe placement.

The [pinned ARM QEMU file-backend probe](evidence/guest-file-backend-arm-20261104.json)
passed on the builder in 9.12 seconds, maximum RSS 53,776 KiB. After the three
input paths were renamed and replaced, ARM `qemu-img` 7.2.22 reported the original
size and copied the original bytes from every held descriptor. The verifier
uses authenticated userspace in a private PID/mount namespace with read-only
proc, only `/dev/null`, no sys/dev bind and no block devices. It uses the
launcher's file-only dry-run mode and read-only descriptors; that probe does not
exercise writable QEMU disks, system kernel/initramfs loaders, VM startup or KVM. The later physical evidence above covers actual
guest execution separately.
The configured binfmt interpreter pathname/hash was checked; the kernel-retained
F interpreter descriptor was not independently inspected.

Exact executed commands, from the private clean builder checkout:

```sh
sudo -i
cd /var/lib/a733-guest-runtime-work-20261104
/usr/bin/python3.10 -B -m unittest discover -s tests -p 'test_*.py'
/usr/bin/python3.10 -B tests/test-assemble-diagnostic.py
for script in tests/test-*.sh; do bash "$script"; done
/usr/bin/python3.10 -I -B tests/verify_guest_file_backend_arm.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz
```

These use existing pinned inputs without compilation or package installation.
Raw logs and exported tool copies are on the builder under
`/home/drew/orange-pi-4-pro-build/guest-runtime-artifacts-20261104/`.

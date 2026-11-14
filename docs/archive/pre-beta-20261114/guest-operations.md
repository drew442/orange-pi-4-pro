> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Guest runtime preparation

The host uses unprivileged QEMU processes with KVM explicitly required. The
current fixture launcher authenticates kernel, initramfs and raw disk inputs,
retains their open descriptors through exec, and coordinates cooperating
launchers with a nonblocking exclusive disk lock. The
[physical A733 guest](../../kvm-vgic-results-20261108.md) passes CPU, disk, network,
clean shutdown, checkpoint and restart checks. The [corrected-host restore](../../evidence/corrected-host-guest-restore-20261108.json)
advances the retained counter-3 guest through 4 and 5 using actual nonroot KVM.
[Hardware release acceptance](../../release-acceptance.md) passes for the recorded
protected-SD configuration.
The optional [isolated loopback TCP profile](../../guest-networking.md) adds a single
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

[Explicit activation controllers](../../guest-activation.md) now prepare and apply the
p5 mount and locked service accounts. The prepared personalized root artifacts
include these controllers in the complete maintenance tree at
`/usr/lib/a733-tools/scripts/`; the current-root manifest records their
contents. The current protected host has booted with its authenticated
management tool closure; see [installation status](../../implementation-status.md).
Activation is explicit. The mount controller grants root-directory
traversal only after validating the mounted device.
[Explicit enrollment](../../guest-enrollment.md) now publishes protected per-guest
directories and working disks. The account helper recreates fixed,
locked identities in RAM `/etc` without administration or disk memberships.
Physical enrollment, account preparation and launch are recorded in the linked
KVM results; new deployments require their own receipts.
See [current managed host roots](../../current-host-roots.md) for prepared-artifact
status and hardware gates.

## Render the enrolled guest's service

After activation and enrollment/restoration, render a service from the **trusted
current** enrollment manifest. Repeat rendering after each durable checkpoint,
because the disk hash changes. Keep the guest stopped throughout replacement.
Use the manifest hash from the reviewed restoration/checkpoint receipt; hashing
an unexplained current file does not supply a trusted replacement pin.

The installed rendering API below produced the physically exercised services
on the normalized host. Its output was independently compared with the actual
inactive RAM unit. For the `firstkvm` fixture, run in an installed root shell:

```sh
: "${A733_ENROLLMENT_SHA256:?Set the trusted current enrollment hash}"
umask 077
A733_UNIT_REVIEW_DIR=$(mktemp -d /run/a733-unit-review.XXXXXX)
python3 -I -B - "$A733_ENROLLMENT_SHA256" > "$A733_UNIT_REVIEW_DIR/a733-guest-firstkvm.service" <<'PY'
from pathlib import Path
import grp, hashlib, importlib.util, sys
root = Path('/usr/lib/a733-tools')
def load(name):
    spec = importlib.util.spec_from_file_location('unit_' + name, root / 'configs' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
raw = Path('/run/a733-guests/firstkvm/enrollment.json').read_bytes()
assert hashlib.sha256(raw).hexdigest() == sys.argv[1]
manifest = load('guest_manifest').validate_manifest(raw)
assert manifest['config']['name'] == 'firstkvm'
assert manifest['guest_uuid'] == '298b281a-d10e-44eb-bdb9-285e27eaafad'
print(load('guest_config').render_unit(manifest['config'],
      str(root / 'scripts/launch-kvm-guest.py'), grp.getgrnam('kvm').gr_gid), end='')
PY
systemd-analyze verify "$A733_UNIT_REVIEW_DIR/a733-guest-firstkvm.service"
cat "$A733_UNIT_REVIEW_DIR/a733-guest-firstkvm.service"
systemctl show a733-guest-firstkvm.service -p ActiveState -p MainPID -p ControlPID
```

Stop on any error, running guest, or unexpected identity/configuration. Review
the nonroot UID/GID, KVM group, resource limits, KVM-only launcher, empty
capability sets, NoNewPrivileges and loopback-only forward before installing:

```sh
install -o root -g root -m 0644 "$A733_UNIT_REVIEW_DIR/a733-guest-firstkvm.service" \
  /run/systemd/system/a733-guest-firstkvm.service
systemctl daemon-reload
```

For acceptance capture, start the [exit observer](../../guest-exit-observer.md) and
require its `ready` record **before** `systemctl start a733-guest-firstkvm.service`.
The fixture waits at most 120 seconds for a host HTTP confirmation. During that
window, capture the real QEMU PID/arguments, KVM VM/vCPU/VGIC descriptors,
nonroot capability restrictions and console CPU/disk results. Read
`http://127.0.0.1:18080/cgi-bin/probe`; only after validating its actual counter,
send one request to the same URL with `?confirm=COUNTER`. Persist that response,
guest powerdown console and the referenced observer's raw exit record. Host
SIGTERM or a later reset unit status does not prove guest-requested shutdown.
Then use [checkpointing](../../guest-checkpoints.md) before another launch. Service
installation alone starts nothing and does not survive a host reboot.

Upstream: [systemd 252 execution restrictions](https://www.freedesktop.org/software/systemd/man/252/systemd.exec.html)
and [service lifecycle](https://www.freedesktop.org/software/systemd/man/252/systemd.service.html).

## Input lifetime and launch

Keep a trusted immutable fixture seed off the writable working path. Copy the
raw seed to the chosen NVMe guest directory. Supply trusted hashes and measured
CPU/RAM budgets using the [fixture procedure](../../guest-fixture.md). Retain both
`scripts/launch-kvm-guest.py` and its adjacent `guest-storage.py` when staging
these tools. The prepared personalized root artifacts include both in the
complete maintenance tree at `/usr/lib/a733-tools/scripts/`. See
[current managed host roots](../../current-host-roots.md). The protected boot pair and normalized full-image installation now pass
[physical deployment and restored-guest KVM/checkpoint/restart](../../independent-deployment-20261110.md).

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

The [preserved counter-5 backup](../../evidence/current-counter5-backup-20261109.json)
has now [restored on fresh normalized A and run actual KVM through 6 and restart
into 7](../../evidence/normalized-guest-restore-20261110.json), with durable checkpoints
and original history preserved. Keep the immutable archive before any later
erasure. [Forensic copies](../../guest-preservation-20261109.md) retain failed and smoke
endurance data separately; outcome preservation is not a guest-disk backup.
[Paired host STATE/root restoration](../../paired-host-restore-20261110.md) and
[controlled offline selection recovery](../../controlled-ssh-recovery-20261110.md)
also pass. Ten [warm boots](../../warm-boot-validation.md) and their SD comparison pass;
ten [cold boots and their final SD comparison](../../evidence/final-cold-boots-20261111.json)
also pass.

The [November 9 KVM workload result](../../evidence/eight-hour-endurance-result-20261109.json)
passes the current 8-hour (28,800-second) criterion with 72,659 measured seconds
between first and last samples. The coordinator stopped the guest and native
collector, CPU workers and management watch; QEMU host stop exited 0 and the CPU
workers exited 2 after intentional interruption; the exit observer recorded the
stop. No guest-requested shutdown or `ENDURANCE_PASS` is claimed. The original
authenticated collector validator and watchdog revalidated all 2,423 samples,
including thermal, nonroot KVM capability, disk and payload-hash checks; all
passed through elapsed 72,669 seconds (cycle 2,422). Whole-card SD SHA-256 for
1,977,614,336 bytes exactly matches baseline
`401df09e666c22db8e8f9adbeecc0f385a910b1f41995e7a1d0bc68be3b64be5`; all
counters are zero. SMART critical warning 0, media errors 0, error-log count 153
and unsafe shutdown count 77 are unchanged from baseline. Full native UART and
bridge snapshots (1,262,306 bytes) are byte-identical. Both preservation phases
are complete. Required host/guest backups and physical normalized deployment
now pass through the separately linked records above. The subsequent [cold-series result](../../evidence/final-cold-boots-20261111.json)
completes boot/SD qualification. See the [completed release checklist](../../release-acceptance.md).

## Verified software checks

At source commit `16ad952`, 449 discovery cases passed on Jammy without skips
(two privileged skips locally), plus four separate diagnostic cases and all
shell regressions. Independent Sol high review accepted descriptor lifetime,
placement/refusal ordering and the second storage-check failure regression.
Actual unprivileged [builder refusal](../../evidence/guest-storage-builder-refusal-20261104.json)
confirms that its unsupported storage namespace is rejected. Fixture positives
do not establish real NVMe placement.

The [pinned ARM QEMU file-backend probe](../../evidence/guest-file-backend-arm-20261104.json)
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

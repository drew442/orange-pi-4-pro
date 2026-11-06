# Frozen-builder userspace reconstruction preparation

Reconstructing the complete 220-package ARM64 userspace remains open. The
[ABI/device preflight](evidence/userspace-arm-preflight-20261106.json) now passes
on the Ubuntu 22.04 build host inside the restored frozen builder. It does not
configure packages or supply physical KVM evidence. Current deployment images
and instructions are unchanged.

All 220 retained `.deb` files match their committed hashes and package/version/
architecture tuples. Three selected inputs—coreutils 9.1-1, dash 0.5.12-2 and
libc6 2.36-9+deb12u14—were privately copied and authenticated before their data
archives were audited and extracted. No original rootfs archive or maintainer
script was used. The resulting 44,014,641-byte raw fixture executes ARM `/bin/true`
and a shell with a nested external command. Frozen-builder and registered
binfmt interpreters have identical pinned QEMU bytes.

A second namespace invocation binds only four identified character devices:
null 1:3, zero 1:5, random 1:8 and urandom 1:9. ARM commands verify their types,
write to null and read 16 bytes from each other device. The zero sample is all
zeros. These are build-time character-device handles, with no storage target.

Both invocations preserve the existing private UID mapping and network,
read-only builder OS, no-new-privileges setting and seven prohibited capability
drops. The real mknod probe remains denied; no block/KVM device is exposed.
The host package inventory is unchanged, and successful exits leave no mapped
processes or output mounts. Three resource samples stay above 5.98 GB free disk
and 7.47 GB available RAM. Thirteen focused regression cases and independent
pre-execution and execution-evidence reviews pass. Code, inputs and raw receipts
are retained under
`out/userspace-arm-preflight-20261106/` with code hashes in the evidence record.

The authenticated ARM64 binary Packages index is already retained under
`tmp/time-sync-inputs-20261106/`, bound to the signed snapshot release by the
[earlier index verification](evidence/time-sync-package-input-20261106.json).
Do not confuse that binary index with the separately retained Sources index.

The next step is a reviewed offline bootstrap adapter. Pinned debootstrap
1.0.126+nmu1ubuntu0.9 still assumes it can create a test device and mount API
filesystems. Its `functions` file, retained from the frozen builder under
`tmp/frozen-debootstrap-reference-20261106/`, has the device sanity check near
line 1635 and device setup near lines 1299–1330. The setup also expects full,
tty and ptmx. Additional disk space does not fix these assumptions. Replace
only the necessary checks/setup with verified prebound devices and appropriate
private API filesystems; do not pretend to be a different container type or
restore prohibited capabilities. Verify that API mounts expose the intended
namespace before any package maintainer scripts run.

After that adapter passes review and execution, compare the complete configured
inventory, package audits and source/cache/host postchecks; strip per-machine
secrets and state as in the current userspace recipe. Reconstructing from locked
binary packages is distinct from recompiling their 149 source versions.
Production root/state/NVMe image staging still needs the pending
[build-host capacity change](build-host-capacity-plan.md). Physical installation,
actual KVM guests and the complete release acceptance matrix remain open.

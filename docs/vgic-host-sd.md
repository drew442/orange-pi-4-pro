# Experimental VGIC host SD candidate

`build/vgic-host-sd.py` prepares a new **regular-file** 256 MiB SD image for the
original installed NVMe state UUID `00fd936c-49fb-4fea-87c8-928b9c1599e0`.
This is an experimental candidate. Recipe tests establish no physical boot,
VGIC availability, actual accelerated guest execution or release acceptance.
The A733, actual 2 GB SD, nominal 250 GB NVMe, minimal host and zero routine SD
write requirements remain in force.

The proposed change is exactly one semantic DT property:

```dts
/interrupt-controller@3400000 {
    interrupts = <1 9 4>; /* GIC PPI 9, level high */
};
```

The [published A733 patch](https://lkml.iu.edu/hypermail/linux/kernel/2610.3/16416.html)
is supporting evidence for a trial. It is an AI-assisted proposal without
Reviewed-by/Tested-by acceptance; publication does not establish that the
installed vendor kernel/firmware combination works with this interrupt.
The current missing GIC interrupt property and observed zero
`KVM_CAP_IRQCHIP` motivate testing the candidate. A reboot with this DT and an
actual KVM guest must establish the result.

The immutable baseline is the original
[deployed host image](deploy-personalized-20261106.md), **not** the later
`fc8…` or `feaa…` candidate profiles:

| Input | Pin |
| --- | --- |
| `out/host-deployment-20261106/a733-host-sd.img` | 268435456 bytes; SHA-256 `2d22d84ada37a5b5d3696cc913caddcb31c0e681e65185005ea56ca0b8396329` |
| Its `manifest.json` | 9116 bytes; SHA-256 `b5af23b9f1a1604e4635bcce98c819328797104e2efe46b447343fe1124edcc0` |
| Kernel source commit | `2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f` |
| EL2 boot package | SHA-256 `b20c3d1133aee6474416bc9846b0bf94d5439aa12d7f7555b44607105ddab3b6` |

The recipe authenticates those inputs, including the original state UUID,
DTB, kernel, initrd, boot command and raw firmware. It checks the 256 MiB
capacity, DOS signature `0xa7330001`, single bootable Linux partition starting
at sector 65536 with 458752 sectors, boot0 at 8192 and EL2 boot package at
16793600 bytes. The retained original image is read only.

To prepare on the frozen Ubuntu 22.04 amd64 builder, first transfer the original
image and manifest and verify their pins above. The original packaging packages
are checked with `assemble-host.py`'s `validate_builder_packages`; the additional
`device-tree-compiler` must match the existing lock at **1.6.1-1 amd64**. No
package installation, source build or dependency download is performed. Existing
assembly and global bootstrap defaults are unchanged.

Run from this checkout, using a fresh name:

```sh
python3 build/vgic-host-sd.py --name trial-01 \
  --image /PATH/TO/ORIGINAL/a733-host-sd.img \
  --manifest /PATH/TO/ORIGINAL/manifest.json
```

The defaults select the original paths in `out/host-deployment-20261106`.
Output is exclusively `out/vgic-host-sd-trial-01/`, reserved atomically; an
existing output, symlink, special input or device path is refused. Names cannot
contain slashes. Run without concurrent mutation of inputs or checkout paths.
The output parent must have at least the existing 256 MiB free-space minimum.
Scratch files stay under the new output and are removed at completion; sparse
partition extraction and SD assembly avoid staging a full NVMe payload. This
operation fits the existing builder capacity envelope; remeasure disk/RAM
before running it. It does not require additional builder storage allocation.
Failures retain the new output directory/log for inspection; use a fresh name
for another attempt.

The recipe extracts the original ext4 partition into a scratch **regular file**
and uses `debugfs` to dump boot files. It uses `fdtput` on a DTB copy. An
independent binary FDT parser compares every node, property, boot CPU and memory
reservation; the sole permitted semantic difference is the newly added three
interrupt cells. The original must lack this property. FDT string/structure
layout may change, so the entire DTB need not remain byte identical.

The existing `assemble-diagnostic.sh` rebuilds the new image with the original
boot command. A fresh extraction then checks that `uImage`, `uInitrd`,
`boot.cmd` and `boot.scr` remain byte identical, the complete first 32 MiB
(including MBR, boot0, EL2 package and padding) remains identical, and the DTB
still has only the permitted semantic difference. Filesystem metadata may
change during reconstruction. The output `manifest.json` records input/output
hashes, original/modified DTB hashes, preserved files, firmware, tool versions,
recipe hashes, source URL and the pending hardware gate; `assembly.log` records
packaging output. Generated artifacts belong only under ignored `out/`.

Before any physical use, independently inspect the output manifest, raw image,
extracted DTB property and preserved boot files. The coordinator must review the
candidate and present the actual SD target and write plan for yes/no confirmation.
This recipe provides no flashing command and never writes SD, NVMe or SPI.

A trial requires full serial capture from power-on, live DT property and VGIC
initialization checks, a repeated `KVM_CAP_IRQCHIP` probe, and the
[first actual KVM guest procedure](kvm-first-guest.md). Guest CPU, disk,
network, persistence, shutdown and restart must be observed. KVM initialization
or `/dev/kvm` alone cannot close the gate. The original UUID/root-selection and
initrd state handling are preserved, so this candidate does not repair or
reinstall NVMe.

Retain the authenticated original SD image off-device before a hardware trial.
If the candidate fails, power off and restore the same original image to the
same identified SD using the reviewed deployment guards and exact-target
confirmation. This restores boot files only: it cannot undo guest/state writes
made during a trial and does not establish NVMe rollback, SPI recovery or zero
routine SD writes. Do not reinstall NVMe as trial recovery.

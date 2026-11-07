# Assemble a SPI candidate without programming flash

The SPI candidate assembler works on regular files on the provisioning host. It
will not open an MTD device or erase/program flash. The production candidate
requires the original flash backup from [the read-only backup procedure](spi-backup.md).
That physical backup and demonstrated recovery are still pending. Synthetic
fixtures exercise the assembler; they must never be used as board firmware.

The component source is the separately built and [registered, reproduced NVMe
U-Boot candidate](uboot-nvme-candidate.md). It preserves the compatible vendor
SPI boot0, monitor and SCP and the reviewed EL2 handoff. Its default boot target
is NVMe. This does not establish working SPI boot on this board.

## Exact file layout and preservation

The observed SPI is XM25QU128C, JEDEC `204118`, 16,777,216 bytes, with
65,536-byte erase units. The pinned [vendor SPI writer layout](spi-boot-investigation.md#pinned-source-findings)
places these components:

| Component | Start byte | Length | End byte, exclusive |
| --- | ---: | ---: | ---: |
| SPI boot0 | 0 | 237,568 | 237,568 |
| Boot package | 262,144 | 1,392,640 | 1,654,784 |

The assembler starts with the complete verified backup and replaces only these
two ranges. It preserves bytes 237,568–262,143 and 1,654,784–16,777,215 exactly.
The components occupy erase units 0 through 25, ending at byte 1,703,936. The
preserved gap and final-unit tail must survive any later erase/program sequence.
Units 26 through 255 must remain untouched. A plan records old and candidate
hashes per affected unit; a unit that already matches need not be erased.

This is smaller than the vendor writer's 50-unit erase span. A future programmer
must consume the complete preserved unit contents, validate the live flash
against the backup, and refuse unexpected changes. This assembler supplies no
programmer. Never write an SD image to SPI: its boot-package offset is beyond
this flash's entire capacity.

## Inputs and output

Use the original `component-retained` directory, which contains the authenticated
parent manifest and complete component files. The registered replay's reference
map is not a self-contained component directory. The assembler checks the
committed registry and original evidence pins, backup metadata and full hash,
parent manifest, and both component hashes. It refuses symlinks, special files,
existing output and unexpected SPI identity or geometry.

The output belongs to a fresh `out/spi-nvme-NAME` directory and remains private.
It contains a complete 16 MiB candidate and a manifest binding its inputs,
component ranges, preservation and erase-unit hashes. All hardware acceptance,
recovery and programming authorization fields remain false. A generated file
is not permission to program SPI.

The [executed assembly evidence](evidence/spi-file-assembly-20261107.json) records
source `4cc78e0`, exact commands and a deliberately synthetic 16 MiB input
(`bytes(range(256)) * 65536`). The Jammy builder and codex produce identical
candidate bytes and manifests; all 256 erase-unit entries and preserved ranges
are checked independently. The fixture output SHA-256 is
`5866447dc4d836da5e67cbc5dacacd57a20fe3a1e76264a573316bc705fdd238`.
This hash identifies a **synthetic test file, never board firmware**.

The builder's executed commands were the following, first with `--plan` and
then without it, redirecting each JSON result to a separate file. Progress is
printed on stderr. Plan mode creates no output directory.

```sh
sudo python3 -I -B \
  /var/lib/a733-spi-file-project-20261107/project/build/assemble-spi-nvme.py \
  --backup-dir /run/a733-spi-file-synthetic-20261107 \
  --components /var/lib/a733-clean-replay-20261106/uboot-nvme-component-ram-02/component-retained \
  --output /var/lib/a733-spi-file-project-20261107/project/out/spi-nvme-synthetic-20261107 \
  --plan
```

The existing fixture output is retained, so this exact output name now refuses
reuse. These are development fixture records, not instructions to prepare the
board. A production invocation cannot be completed until
the physical backup is retained and verified off-device. Keep that original
backup and manifest immutable, alongside the candidate and source provenance.

## Remaining physical gates

Apply the [installed management repair](installed-host-repair-20261107.md) first.
Then verify both SSH keys, retain the original SPI backup off-device, demonstrate
the recovery route, and capture the [U-Boot NVMe read test](spi-boot-investigation.md#resolved-source-policy-and-remaining-candidate-work).
Inspect the actual backup-derived candidate independently before presenting the
exact physical target and proposed write for a simple yes/no confirmation.
No physical SPI write is authorized by a successful assembly or its tests.

After approved programming, prove cold boot with the SD removed, actual KVM
guest execution and the full [hardware acceptance procedure](hardware-validation.md).

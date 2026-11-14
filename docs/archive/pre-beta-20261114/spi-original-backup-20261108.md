> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Original SPI backup and private candidate (2026-11-08)

The running A733 host supplied the first actual SPI backup. The unchanged
[read-only backup tool](../../spi-backup.md) revalidated `/dev/mtd0`, major/minor
`90:0`, as the complete **XM25QU128C**, JEDEC `204118`, **16,777,216 bytes**,
with 65,536-byte erase units and one-byte writes. It opened the device read-only,
performed two complete matching reads, and verified the private RAM file.
The host boot ID was `94851e72-e3b9-4824-88c6-fb82ad7fb610`.

**All original bytes are `0xff`.** The complete original SHA-256 is
`dffab0dd410657cb30c7b2fd7f2586a4792e8472e58882b3532581f8111a646d`.
This describes the observed contents; it does not waive recovery testing.

Verified private copies, including the original manifest, are retained at:

- Codex: `/home/drew/orange-pi-4-pro/out/spi-backup-20261108-01/`.
- Builder: `/var/lib/a733-spi-backup-20261108-01/` on `build.drewnet.online`.

The original manifest remains unchanged: its off-device retention and recovery
fields are false because it describes the local RAM operation. Separate native
and builder retention records verify the copied bytes and hashes. Recovery is
still unproven. Generated firmware stays outside Git and is not published.
The RAM copy may be lost at shutdown without losing either verified retained copy.

## File-only candidate

The existing [candidate assembler](../../spi-file-candidate.md) now also ran against
this actual backup, using the registered `nvme-candidate-20261107-ram02`
components. Plan mode passed before assembly. No MTD device is opened by the
assembler, and no flash programming occurred.

The executed builder source is `14e50d15463226fb75451b59a6fa8463fc705300`:

```sh
sudo python3 -I -B \
  /var/lib/a733-machine-id-mask-20261108/project/build/assemble-spi-nvme.py \
  --backup-dir /var/lib/a733-spi-backup-20261108-01 \
  --components /var/lib/a733-clean-replay-20261106/uboot-nvme-component-ram-02/component-retained \
  --output /var/lib/a733-machine-id-mask-20261108/project/out/spi-nvme-original-20261108
```

The completed output name refuses reuse. The private native copy is
`out/spi-nvme-original-20261108/a733-spi-nvme.bin`, **16,777,216 bytes**,
SHA-256 `83edcd7f448e8353163666016a6bae3e9b70d041a5e4d8454f48931839205e90`.
It is a candidate file, **not an image to flash now**.

Only the registered boot0 range `[0, 237568)` and boot-package range
`[262144, 1654784)` are replaced. The intervening gap and the complete remaining
tail are preserved. The potential erase envelope comprises units 0–25, ending
at byte 1,703,936; actual future programming must preserve complete partial-unit
contents and revalidate the live flash. Units 26–255 remain unchanged.

The [evidence index](../../evidence/spi-original-backup-20261108.json) records source,
retention, construction and independent artifact inspection. Its acceptance is
limited to backup retention and regular-file candidate correctness.

## Remaining gates

A subsequent [read-only live comparison](../../spi-live-plan-results-20261108.md) now
confirms the baseline still matches and all candidate unit/preservation pins
agree. It adds no programming authorization or recovery proof.

The [protected host](../../implementation-status.md) now runs the fresh endurance
workload. Matching protected NVMe BOOT files are installed; U-Boot loading those
files remains untested. Leave the workload undisturbed until its outcome is
retained.

Before programming, demonstrate recovery independent of a programmed SPI
bootloader, test U-Boot NVMe file loading and the matched Linux boot files,
and prepare and independently review the actual programmer and rollback
procedure. Revalidate the exact live target/write. The operator's
[known SPI authorization](../../evidence/storage-write-authorization-20261108.json)
persists; ask yes/no only for unapproved targets or scope.
The all-`0xff` backup and successful SD boot do not by themselves
prove recovery after a bad programmed bootloader. SD-removed cold boots, real
KVM guest completion and full release acceptance remain open.

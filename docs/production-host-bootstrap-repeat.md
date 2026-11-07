# Matching bootstrap for the second state construction

The [November 7 repeat evidence](evidence/production-host-bootstrap-repeat-20261107.json)
records a new **256 MiB** regular-file SD bootstrap for state UUID
`feaaee34-d782-4c18-bd04-1029540ab26a`. It fits the actual
**1,977,614,336-byte SD card**. Frozen construction and complete producer SD
inspection and independent artifact review pass, with complete proof in the evidence.
This image has not booted physically.

It belongs to the [second roots](production-host-roots-repeat.md) and
[second personalized state](production-host-state-repeat.md). The currently
installed board still uses state UUID `00fd…`; the first candidate uses `fc8…`.
**Do not write this SD alone or use it for the existing-install repair.** A
matched second NVMe image and deployment procedure are still required.

## Executed build

Producer revision is `faaa01019189b92e90e263014092657a1fc4e1f9`, with all **627**
source records authenticated. The adapter pins the unchanged historical
bootstrap controller, registered state evidence and state-controller family.
Explicit counted AST constant amendments select the new chain; all original
predicates, source/capsule checks, isolation, budgets and cleanup remain active.
Held template checks run before success publication. Historical defaults and
original evidence remain unchanged.

The committed CLI `--plan` passed before this executed command:

```sh
sudo python3 -I -B \
  /var/lib/a733-bootstrap-repeat-project-20261107/project/build/launch-host-bootstrap-repeat.py \
  --project /var/lib/a733-bootstrap-repeat-project-20261107/project \
  --tag timesync-repeat-01 \
  --userspace /var/lib/a733-sd-replay-inputs-20261106/rootfs.tar.gz \
  --diagnostic /var/lib/a733-bootstrap-inputs-20261107 \
  --busybox /var/lib/a733-sd-replay-inputs-20261106/busybox \
  --state-receipt /var/lib/a733-clean-replay-20261106/host-state-timesync-repeat-01/receipt.json \
  --state-receipt-sha256 01a03e382d90105863285c8625e5f9744cec8ffb535124afd6d9520c7c022c42
```

The phase is
`/var/lib/a733-clean-replay-20261106/host-bootstrap-timesync-repeat-01`.
Outputs under `work/project/out/` are `host-sd-timesync-repeat-01` and
`host-initramfs-timesync-repeat-01`. This tag is consumed.

The baseline userspace archive supplies only locked storage-tool binaries;
installed roots/state retain their separately pinned 221-package time-enabled
archive. Compatible boot0, kernel, DTB and module carrier remain unchanged.
The boot package remains **experimental EL2**, service `0x8000ff40`, SPSR `0x3c9`.
This repeat does not substitute the separate NVMe-first firmware package or
program SPI.

## Artifacts and validation

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `a733-host-sd.img` | 268,435,456 | `64f029e319881c50ae6bc0dc0739f310e6476dec7fa4bac2e2d74c66565d5401` |
| `a733-host-sd.img.gz` | 17,272,073 | `5a6c2859a19e9d3a4e6a737723d48dbba25f65f2bb031b0db9dccce9da4ad072` |
| `uInitrd` | 4,762,443 | `bf2c0c309fe61bdff9e5706ce23dec5418dab5114b27faf0f6ab2a94e9ce0386` |

Inspection checks the MBR/raw boot blobs, kernel/DTB pins, boot-script wrapper
CRC and exact contents, legacy ext4 features, initrd wrapper and all **424 newc
entries**, the selected state UUID and complete gzip expansion. Identity-bound
initramfs bytes and wrapper/compression hashes intentionally differ from the
first `fc8…` bootstrap; no byte-identical repeat is claimed.

All 842 builder packages are unchanged, with no mapped processes or phase mounts
remaining. Sampled minima are **23,617,425,408 bytes free disk** and
**7,324,196,864 bytes available RAM**. Construction ends with
**23,760,064,512 bytes free**, before any new full-NVMe project/capsule staging.
Preserve its unchanged 22 GiB initial recipe guard plus staging overhead;
remeasure before composition. No further LV growth is authorized.

Full raw/gzip/initramfs artifacts, producer/source records and inspection proof
are retained in `out/host-bootstrap-timesync-repeat-01-20261107`. Sparse transfer
preserves the complete raw hash while allocating 36,700,160 bytes on codex
(36,704,256 on the builder). Native free space after transfer is 306,089,984 bytes,
above its separate 256 MiB floor. Do not duplicate large inputs there.

Privileged Ubuntu validation passes **1,631 unit tests without skips**, five
shell suites, five SD assembly cases and Bash/TOML checks. These establish no
physical boot, SSH, KVM, zero routine SD writes, SPI/recovery or endurance result.
The [existing-install repair](installed-host-repair-20261107.md) and full release
acceptance remain pending; published operator images and endpoints are unchanged.

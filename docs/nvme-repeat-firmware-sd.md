# NVMe-first firmware SD for the second production state

The [completed construction](evidence/nvme-repeat-firmware-sd-20261107.json)
produces a 256 MiB SD transport for the registered NVMe-first U-Boot and the
second production state UUID `feaaee34-d782-4c18-bd04-1029540ab26a`. It pairs with
the [second full NVMe candidate](production-nvme-repeat.md). It fits the actual
1,977,614,336-byte SD. Physical default boot, manual fallback, zero routine SD
writes, SPI recovery and actual KVM guest execution remain untested.

The current board uses state UUID `00fd…`; your next action remains the
[one-file machine-id repair](installed-host-repair-20261107.md) with the existing
installer and host SD. Do not flash this transport alone or reinstall to repair
DHCP. Published installation endpoints remain unchanged.

## Reproduction

The [new wrapper](../build/launch-nvme-test-sd-repeat.py) composes the exact
SHA-pinned second-bootstrap and first-firmware adapters. Counted registration
anchors bind the executing controller to its own committed source. Every adapter
and historical template stays held through source/capsule authentication and
final checks before success publication. Original historical controllers,
defaults, state predicates, CLI and resource guards remain unchanged. No generic
state/profile selection is added.

From source revision `41e0e8f8a018147e8b0cfc93f5fca1e0b8d16bc5`, committed-source
planning passed before this successful command:

```sh
sudo python3 -I -B \
  /var/lib/a733-nvme-repeat-transport-project-20261107/project/build/launch-nvme-test-sd-repeat.py \
  --project /var/lib/a733-nvme-repeat-transport-project-20261107/project \
  --tag nvme-firmware-repeat-01 \
  --userspace /var/lib/a733-sd-replay-inputs-20261106/rootfs.tar.gz \
  --diagnostic /var/lib/a733-nvme-test-sd-inputs-20261107 \
  --busybox /var/lib/a733-sd-replay-inputs-20261106/busybox \
  --state-receipt /var/lib/a733-clean-replay-20261106/host-state-timesync-repeat-01/receipt.json \
  --state-receipt-sha256 01a03e382d90105863285c8625e5f9744cec8ffb535124afd6d9520c7c022c42
```

The completed tag cannot be reused. All 644 tracked source records are bound.
Inputs, isolation and the original 4 GiB recipe admission plus staging remain
those of the [first transport](nvme-firmware-test-sd.md). The baseline userspace
archive supplies storage tools only; the NVMe roots retain their 221-package
time-enabled userspace.

## Files and validation

Builder phase:
`/var/lib/a733-clean-replay-20261106/host-bootstrap-nvme-firmware-repeat-01`.
Files are under `work/project/out/host-sd-nvme-firmware-repeat-01`.
Native `out/nvme-repeat-firmware-sd-20261107` retains the gzip, initramfs and small
proofs; the raw image remains on the builder.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `a733-host-sd.img` | 268,435,456 | `e8a0a7a7b1edb3bb86cb485dfc6e83ed5e7c7c971caa031463e18faad9c16717` |
| `a733-host-sd.img.gz` | 17,272,072 | `17da6486bd6669b61959e7ae5b32f6cdd58177856b1cec1bc2211f24d617a2ae` |
| `uInitrd` | 4,762,443 | `bf2c0c309fe61bdff9e5706ce23dec5418dab5114b27faf0f6ab2a94e9ce0386` |

The initramfs exactly matches the second production bootstrap. Producer checks
cover MBR/raw firmware, compatible kernel/DTB, script CRC/body, filesystem
features, all 424 newc entries, selected state and complete gzip expansion.
Independent artifact review also passes: all bytes outside the boot-package
range `[16793600,18186240)` equal the second production bootstrap. It separately
checks the gzip/raw hashes, filesystem files, CRCs, all 424 newc entries,
644 source blobs/checkouts and complete capsule. No physical test is implied.
Independent source review passes 28 focused cases. Privileged Ubuntu discovery
passes **1,676 tests without skips**, five shell suites, five assembly cases and
12 Bash parses; all five TOML files parse. The unchanged installer's pure manifest
validator accepts the second retained NVMe delivery manifest; this performs no
installation or physical target check.

All 842 host packages remain unchanged; final mapped-process and phase-mount
lists are empty. Initial admission required 4,660,659,354 bytes; sampled minima
were 21,783,724,032 bytes free and 7,334,268,928 bytes available RAM. Completion
left 21,926,354,944 bytes free on the builder. Recheck each future admission;
no guard reduction or further LV growth is authorized.

## Physical test gate

After a deliberately matched deployment and management validation, follow the
[first transport's read-only hardware test](nvme-firmware-test-sd.md#future-hardware-test-gate)
using this matching SD/NVMe pair. Capture the firmware environment, NVMe file
reads, default NVMe boot and separate manual `run bootcmd_mmc0` fallback with full
serial logs and root/state identities. Never use `saveenv` or flash commands in
that test. Source behavior does not prove either path works physically.

Original off-device SPI backup, demonstrated independent recovery, inspection of
the actual backup-derived candidate and an exact live target/write plan must
precede the agreed yes/no programming confirmation. SD-removed boot and the
complete [hardware acceptance matrix](hardware-validation.md) remain required.

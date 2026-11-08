# Read-only SPI backup preparation

This tool prepares the original SPI backup needed before SPI-to-NVMe programming.
The first [physical backup and off-device retention](spi-original-backup-20261108.md)
now pass; the complete observed flash contains `0xff` bytes. The tool also has
host safety tests and independent source review. [Preparation evidence](evidence/spi-preparation-20261107.json)
records the tests and review. It does not erase/program flash, install anything,
change boot selection or prove recovery.

Installed DHCP and both SSH keys now pass after the repair. The next operator
action is the [VGIC SD trial](next-host-boot-20261108.md); no additional backup
command is needed for that trial. The commands below remain available for a
later capture of changed flash. Keep both heatsinks attached. The target is:

| Field | Required value |
| --- | --- |
| Device | `/dev/mtd0`, character device for the complete SPI flash |
| Name/type | `spi0.0`, NOR |
| Part / JEDEC | `XM25QU128C` / `204118` |
| Capacity | 16,777,216 bytes |
| Erase / write geometry | 65,536 / 1 byte |
| Partition map | Exactly one complete MTD device; no additional partitions |

[`backup-spi.py`](../scripts/backup-spi.py) opens the device read-only and uses only
[`MEMGETINFO`](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/include/uapi/mtd/mtd-abi.h#L175)
to inspect its geometry. It checks the kernel device number, sysfs
SPI ancestry and identity, and complete MTD map. Backups require a new private
directory beneath `/run` on **tmpfs**, at least 24 MiB free and **no active swap**.
[Tmpfs can use swap](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/Documentation/filesystems/tmpfs.rst#L12).
These checks are repeated during the operation. Do not disable host swap or
weaken guards to force a backup on an unexpected environment; return the error.

The tool reads the full SPI twice, compares those hashes and verifies the RAM
file before publishing `manifest.json`. It prints progress each MiB. Two source
reads are deliberate firmware-backup checks; normal NVMe installation readback
remains optional. The tool is standalone; it is not in the published maintenance
package or installer image.

## Run after SSH works

From codex, set the address actually reported by the host. Keep SSH host-key
checking enabled and use the fingerprint already compared with the serial
console. These commands copy only this small script into board RAM:

```bash
set -euo pipefail
cd /home/drew/orange-pi-4-pro
A733_BOARD_IP=REPLACE_WITH_ACTUAL_BOARD_IP
scp scripts/backup-spi.py root@"$A733_BOARD_IP":/run/a733-backup-spi.py
ssh root@"$A733_BOARD_IP" 'python3 -I -B /run/a733-backup-spi.py plan --device /dev/mtd0'
```

The read-only plan must show the identity above and `writes_to_flash=false`.
If it refuses the device, stop and return the error instead of changing identity
checks. When it matches, read into a fresh board RAM directory:

```bash
ssh root@"$A733_BOARD_IP" 'umask 077; python3 -I -B /run/a733-backup-spi.py backup --device /dev/mtd0 --output /run/a733-spi-backup-01'
```

A completed local backup reports `status=verified-local-ram-backup`,
`bytes=16777216`, `source_reads=2`, and the actual SHA-256. Its
`off_device_retention_verified` and `recovery_proven` fields remain false.
An existing output directory is refused: use a new suffix for another attempt.
Failed private output may remain, but it has no successful final manifest.

## Retain off-device before powering down

The RAM copy is lost at poweroff. Copy the binary and manifest to a new private
codex directory, then check the copied bytes once:

```bash
(
set -euo pipefail
umask 077
A733_SPI_RETAINED=/home/drew/orange-pi-4-pro/out/spi-backup-20261107-01
test ! -e "$A733_SPI_RETAINED"
mkdir -m 700 "$A733_SPI_RETAINED"
scp root@"$A733_BOARD_IP":/run/a733-spi-backup-01/spi.bin \
    root@"$A733_BOARD_IP":/run/a733-spi-backup-01/manifest.json "$A733_SPI_RETAINED/"
python3 -I -B - "$A733_SPI_RETAINED" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
manifest = json.loads((root / 'manifest.json').read_text())
assert manifest['status'] == 'verified-local-ram-backup'
assert manifest['bytes'] == 16777216 and manifest['source_reads'] == 2
assert manifest['image'] == 'spi.bin'
data = (root / 'spi.bin').read_bytes()
assert len(data) == 16777216
assert hashlib.sha256(data).hexdigest() == manifest['sha256']
print('Off-device SPI copy verified:', root, manifest['sha256'])
PY
)
```

Keep that directory outside Git and retain the original manifest unchanged.
Return the complete plan/backup console output and off-device verification
result. Do not upload the firmware publicly. This establishes a retained backup
only; it does not establish a usable recovery route.

A compatible regular-file SPI candidate, demonstrated recovery, and an exact
programming plan remain required. Before programming, present the physical
target and proposed write and request a simple **yes/no** confirmation. See [SPI boot preparation](spi-boot-investigation.md).

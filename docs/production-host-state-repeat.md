# Second personalized state construction

The [November 7 repeat evidence](evidence/production-host-state-repeat-20261107.json)
records a fresh **8 GiB** time-enabled state image built in the frozen Ubuntu
22.04 namespace, complete read-only filesystem verification and passing
independent artifact review. It binds the [second root construction](production-host-roots-repeat.md).
The [first state](production-host-state.md), installed board and published
operator images remain separate.

The new state UUID is `feaaee34-d782-4c18-bd04-1029540ab26a`. Its machine-id is
newly generated; SSH host-key storage is empty. Never clone this identity to a
second installation. Both authorized administration keys and the existing
management configuration are preserved. The root-owned machine-id is `0644`;
private keys/configuration paths retain their original restrictive modes. Clock
storage is empty, owned by 996:996 with mode `0755`.

## Executed construction and readback

The [registered adapters](state-repeat-controller.md) authenticate unchanged
historical controller templates and amend only explicit counted constants for
the new parent chain. Producer revision is
`3221a1cf9f589576f875a3c742ac819998a140c0`; all **621** source records and copied
capsule inputs pass authentication. The committed CLI `--plan` passed first.

```sh
sudo python3 -I -B \
  /var/lib/a733-state-repeat-project-20261107/project/build/launch-host-state-repeat.py \
  --project /var/lib/a733-state-repeat-project-20261107/project \
  --tag timesync-repeat-01 \
  --userspace /var/lib/a733-clean-replay-20261106/userspace-timesync-account-dates-01/work/export-timesync-result/rootfs.tar.gz \
  --config /home/drew/orange-pi-4-pro-build/operator-inputs-20261105/management.json \
  --admin-keys /home/drew/orange-pi-4-pro-build/operator-inputs-20261105/admin.pub \
  --roots-receipt /var/lib/a733-clean-replay-20261106/host-roots-timesync-repeat-01/receipt.json \
  --roots-receipt-sha256 64e413bbdd158da4e998a193e32d40c8a62c65d3051c6cb2f6aa4b955ab2efbc \
  --state-uuid feaaee34-d782-4c18-bd04-1029540ab26a
```

The actual phase is
`/var/lib/a733-clean-replay-20261106/host-state-timesync-repeat-01`;
its payload is `work/project/out/host-state-timesync-repeat-01`. This tag is now
consumed; another attempt requires a fresh tag and fresh installation identity.

```sh
sudo python3 -I -B \
  /var/lib/a733-state-repeat-project-20261107/project/build/verify-host-state-repeat.py \
  --receipt /var/lib/a733-clean-replay-20261106/host-state-timesync-repeat-01/receipt.json \
  --receipt-sha256 01a03e382d90105863285c8625e5f9744cec8ffb535124afd6d9520c7c022c42 \
  --output /var/lib/a733-host-state-repeat-readback-20261107-01
```

Both commands completed successfully. The independent reader authenticates all
38 payload artifacts and the actual new parent root proof, then checks all
**1,172 paths**, 1,115 unique regular inodes totaling 7,337,572 bytes, three
symlinks, ownership, modes and attributes. There are no device records or raw
attributes; only the allowed empty `lost+found` is additional. Creator policy
remains filesystem root `0755` versus staged root `0700`, child mtime at whole
seconds and root mtime from the superblock. Atime, ctime and crtime are outside
comparison. The image is writable with its journal enabled.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `state.ext4` | 8,589,934,592 | `218f0c8dcceb18651e334d130834026ced970657e941a4ab3254cfbe4d159656` |
| `state.ext4.gz` | 10,009,260 | `703a21d9db90c0d10b9ea1de6971c26cacaf673b71867cfe8980d2b11d4cf73d` |

Independent review rehashes every source/payload, verifies the copied capsule,
fully expands the native gzip to its raw pin and corroborates all 20 debugfs log
batches. Complete dumped-file byte comparisons remain attributed to execution of
the authenticated reader; temporary dumps were deleted after comparison.

## Comparison and remaining integration

The staged namespace matches the first state. Only the fresh state marker and
machine-id contents differ, together with **13 additional mtime-only changes**.
Other contents, types, links, modes, ownership and attributes match; provenance
and tools are identical. The selector is unchanged because the parent root
marker bytes are unchanged; distinct root image/receipt/proof pins bind the new
chain. New UUID, machine-id and filesystem construction metadata intentionally
prevent byte-identical personalized state.

Native records, all 37 non-raw payload files including gzip, expected trees and
full readback logs are in `out/host-state-timesync-repeat-01-20261107`. The raw
image remains on the builder. Host package inventory is unchanged, with no
remaining mapped processes or phase mounts. Sampled minima are
23,479,717,888 free bytes and 7,314,558,976 available-memory bytes; construction
ends with **24,086,933,504 bytes free**. Recheck before matching bootstrap and
full NVMe composition; preserve all capacity guards.

Matching bootstrap and NVMe repeat construction remain open. This component is
not an SD or whole-drive installation image. Do not replace the current host SD
or delivery endpoint with a mismatched generation. The
[existing-install repair](installed-host-repair-20261107.md), both-key SSH,
automatic time, actual KVM guest, SPI/recovery, SD-write and endurance tests and
independently reproduced deployment remain physical gates.

# Encoded retention of the first NVMe candidate

The [November 7 retirement evidence](evidence/nvme-raw-retention-20261107.json)
records removal of one generated regular file on the build host:

```text
/var/lib/a733-clean-replay-20261106/nvme-timesync-03/work/project/out/nvme-image-timesync-03/nvme.img
```

Its complete contents remain encoded in **both** exact gzip copies below.
All other phase files, parent components, source caches and published operator
images remain available. No Orange Pi storage was written. This retirement
recovers **975,032,320 allocated bytes** for subsequent build stages; the logical
file size was 256,060,514,304 bytes.

| Retained gzip | Location |
| --- | --- |
| Build host | Original payload directory above, `nvme.img.gz` |
| Codex | `/home/drew/orange-pi-4-pro/out/nvme-candidate-retained-20261107/nvme.img.gz` |

Each gzip is 545,633,126 bytes, SHA-256
`9e8be89dbcabe30799744201ea94de93ce343134d0a8e034fa44281c8dd13955`.
The [original full stream proof](production-nvme-composition.md) authenticates its
entire expansion to 256,060,514,304 bytes, SHA-256
`2c7e69cf5f0370b818b47c4d36a6c1eec7cd7258773794ad06de7cd5f5ab499f`.
Retirement reuses that pinned expansion proof after full encoded checks on both
hosts; it does **not** claim a new decode. Both prepare and execution separately
hash the complete existing raw file, with progress every 15 seconds.

The fixed controller checks exact inode, file type, single link, ownership,
mode, size, full hashes, protected physical ancestors and held parent identity.
It rejects process/thread descriptors, mapped files, mounts, loops and active
mapped UID/GID tasks. Trusted root orchestration stays quiescent. Independent
source review, five root-run Ubuntu 22.04/Python 3.10 fixtures and separate
approval of the actual prepared record precede execution. A durable exclusive
intent records the exact target before FD-relative unlink, followed by parent
fsync, retained-file checks and a completion record. Any existing intent or
completion prevents retry; inspect a failed operation instead of restarting it.

Controller code, helper, proofs, preparation and journal remain root-owned in
`/var/lib/a733-nvme-raw-retention-20261107` on the builder. Small copies and reviews
are under `out/nvme-raw-retention-20261107` on codex. The native gzip retains its
ordinary `drew` ownership/private permissions; the copy is not claimed immutable
against its owner. Exact current hashes were checked independently before removal.

## Restore the regular image when needed

The original raw path now requires restoration before any historical command
that opens it. Full restoration after this retirement has not been executed.
Retain the original records and use a new, empty private directory on a filesystem
that supports sparse files. Reserve expansion space plus the unchanged 3 GiB working floor. The old
975 MB allocation is a planning observation; chunked sparse restoration may
allocate more. Recheck space and monitor it during restoration.
Verify the encoded hash above before restoring. On the build host:

```sh
restore_dir=/var/lib/a733-nvme-restoration-20261107-new
archive=/var/lib/a733-clean-replay-20261106/nvme-timesync-03/work/project/out/nvme-image-timesync-03/nvme.img.gz
sudo mkdir -m700 "$restore_dir" &&
sudo bash -o pipefail -o noclobber -c \
  'gzip -dc -- "$1" | dd bs=1M conv=sparse status=progress > "$2/nvme.img"' \
  a733-restore "$archive" "$restore_dir"
```

Stop if either command fails. Check the exact logical size and full raw hash
before using the result; this read displays progress:

```sh
sudo stat -c '%s' "$restore_dir/nvme.img"
sudo dd if="$restore_dir/nvme.img" bs=4M status=progress | sha256sum
```

Compare against the raw size/hash above, then inspect the unchanged original
manifest, GPT/partition proof and parent pins. Do not overlay an existing phase
or substitute a restored file silently into old evidence. Device/inode numbers,
ctime, atime and allocation are not reproduced by gzip. GNU documents the
[sparse copy option](https://www.gnu.org/software/coreutils/manual/html_node/dd-invocation.html).

Sparse copying here applies only to a newly created **regular file**. Physical
flashing must write zero bytes as well. The operator installer still performs
one expanded write with progress and optional readback; builder retention adds
no onboard installation passes. Actual boot, KVM, SPI/recovery, SD-write and
endurance acceptance remain pending.

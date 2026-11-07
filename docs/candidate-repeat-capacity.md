# Capacity preparation for the second NVMe candidate

The [November 7 evidence](evidence/candidate-repeat-capacity-20261107.json)
records three byte-preserving sparse conversions and archive retention of one
historical, nondeployable raw fixture. The next NVMe composition keeps its
unchanged 22 GiB recipe guard plus staging. Native retention keeps its separate
256 MiB free-space floor. No Orange Pi storage was accessed and no additional
LV growth occurred.

## Unchanged images with smaller allocation

Each operation used a fixed path/inode/hash, protected controller and exact
reviewed readiness pin. The control held an exclusive file lock, checked process
and thread descriptors, maps, mounts and loop backings, rehashed complete bytes,
and invoked `fallocate --dig-holes` through the held regular-file FD. Complete
hashes, logical sizes, identities, ownership, modes, link counts and mtimes match
before/after. Ctime changes are expected. Durable intents/completions prevent
blind retries.

| Host / generated file | Allocated before | Allocated after |
| --- | ---: | ---: |
| Codex `out/installer-helper-20261105/a733-installer-prep.img` | 268,439,552 | 54,792,192 |
| Builder `host-bootstrap-nvme-firmware-01/work/project/out/host-sd-nvme-firmware-01/a733-host-sd.img` below the replay base | 268,439,552 | 32,591,872 |
| Codex protected `/var/lib/a733-userspace-space-retention-20261106/retained-0.ext4` | 1,073,745,920 | 508,276,736 |

The first builder preparation was rejected by independent review: it lacked the
stated reserve check, and a shell wildcard had left control-file ownership/modes
incorrect. No extent operation ran under that preparation. A new protected
`builder-02` store adds explicit reserve checks and root-owned `0400` control
files; fresh readiness and separate review preceded execution. The original
findings and rejected preparation remain available.

Each profile passed five real root-run sparse-file fixtures. Read-only parent
policy and reserve-refusal cases passed separately. Independent review also
checked the actual target, installed controls, full hash and real kernel idle
guard before each sole execution. These are host-file checks, not hardware boot
or SD-write acceptance.

## Historical diagnostic fixture retention

The ordinary raw path below was retired after native and builder gzip copies
were fully authenticated:

```text
/home/drew/orange-pi-4-pro/out/full-root-metadata-fixture-20261106/work/full-root-fixture/diagnostic-1g.ext4
```

| Representation | Bytes | SHA-256 |
| --- | ---: | --- |
| Raw ext4 | 1,073,741,824 | `744ad9dc411e8342fc0f45038595adbcaa3e90e9276b70725a72e6cea4bb6bb6` |
| Gzip | 167,200,129 | `8aecfbfa35cfdc742e0aa22a916f2cc680925f63500b6add9ea60e26c775dbbd` |

Both hosts retain the gzip at
`/var/lib/a733-native-fixture-retention-20261107/diagnostic-1g.ext4.gz`.
Preparation hashes the full raw file while encoding deterministic gzip9, then
verifies the complete encoded bytes, CRC and 1 GiB expansion. The writer caps
encoded output at 230 MiB and checks the 256 MiB floor plus an 8 MiB allocation
margin before writes. The builder receiver checks full transfer/readback hashes,
root-owned private ancestry, file identity and fsync durability. Its decoded hash
binds the already verified native expansion; no new remote decode is claimed.

Independent review approved source and then the exact actual prepared/mirror
records before removal. Review also required explicit fsync of the newly created
store directories' parent chains; root completed and recorded those operations
on both hosts before unlink. The controller rechecked raw/archive hashes,
metadata, protected proofs and the real idle guard, recorded durable intent,
unlinked only the exact basename through its held parent FD, fsynced and closed
the raw FD, then verified the retained archive and published completion. The raw
file released 508,342,272 allocated bytes. Atime is excluded from stable equality;
controller reads use `O_NOATIME`, while harmless external reads remain permitted.

The older protected raw copy **still exists** at
`/var/lib/a733-userspace-space-retention-20261106/retained-0.ext4`, now sparse as
listed above. Its full hash matches the raw pin. The two new gzip copies provide
additional independently retained representations. The unrelated composition
archive `full-root-metadata-fixture-02` does not contain this image.

## Recovery and current capacity

Historical commands opening the ordinary raw path need a deliberate restoration
into a fresh private directory. Verify the exact gzip pin, reserve expansion
space plus the free-space floor, then decompress into a new regular file with
sparse copying and progress. Verify its exact logical size and raw hash before
use; restore numeric owner/mode/mtime/xattrs from the pinned prepared metadata.
The protected raw copy above is another authenticated source. Do not overlay
old paths silently. Full restoration after this retirement has not been executed;
inode numbers, ctime, atime and allocation are not restoration guarantees.

Controls, proofs, reviews and logs are retained under
`out/candidate-capacity-20261107` and `out/native-fixture-retention-20261107`.
Root-private runtime stores named in the evidence retain their original records;
the large gzip is referenced rather than duplicated into `out/`.

Native free space immediately after the final sparse conversion is
**1,410,519,040 bytes**. Builder free after its sparse conversion was
**23,995,502,592 bytes**; archive transfer and subsequent NVMe construction consume
headroom. Recheck current resources and actual artifact size before any copy or
build. Sparse regular-file operations do not change the installer policy: one
expanded physical write with progress and optional readback.

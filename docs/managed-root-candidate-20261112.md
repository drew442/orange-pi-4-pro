# Managed host root candidate

Source `2996365a11cb07deb20eb4325c90841de8ab820e` now has two matching A/B
constructions, independent complete artifact/filesystem checks and production
reader interoperability. **Neither new root has booted on the board.** These are
4 GiB NVMe root components. Keep the retained 2 GB recovery SD unchanged.

[Build records](evidence/managed-root-builds-20261112.json),
[independent artifact review](evidence/managed-root-artifact-review-20261112.json)
and [3589-test combined validation](evidence/candidate-integration-validation-20261112.json)
record the exact scope, retained verifier corrections and remaining hardware gates.

An [independent third construction](evidence/independent-managed-root-reproduction-20261113.json)
now reproduces the documented command from a fresh source/worktree, using the
declared retained inputs. All 133 artifacts and the manifest match the table
below; full gzip expansion and filesystem readback pass. This is independent
operator execution on the qualified builder, with physical deployment still pending.

## Artifact identity and custody

On codex, relative to `/home/drew/orange-pi-4-pro`:

```text
out/managed-root-candidate-20261112-02/artifacts/root-A.ext4.gz
out/managed-root-candidate-20261112-02/artifacts/root-B.ext4.gz
out/managed-root-candidate-20261112-02/artifacts/manifest.json
```

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Root A gzip | 143662109 | `0f066fea6c32bfdfa4161b761b132889dabbdadbd1fd70b77ddb8554902c400d` |
| Root B gzip | 143661932 | `380ec5c9c20e35f8480e5ff6d40ddc1ab258924f1074252668f84010b08dfbe0` |
| Root A raw | 4294967296 | `a75cf5945c3572d4741841e9a411719bf422e6b3f533b52d7e122029156eb230` |
| Root B raw | 4294967296 | `fe842ff05584205b4fb9f930b3620bac04155c9f751488a8fb20a0d798dbd87c` |
| Manifest | 51618 | `65f69d6a2f0b6346c0ca2f0257daa26836dd791918a4af795e70de730f8f424a` |

Both complete producer outputs remain on `build.drewnet.online` under:

```text
/var/lib/a733-clean-replay-20261106/host-roots-managed-runtime-20261112-02/work/project/out/host-payload-managed-runtime-20261112-02
/var/lib/a733-clean-replay-20261106/host-roots-managed-runtime-20261112-03/work/project/out/host-payload-managed-runtime-20261112-03
```

Encoded copies and the manifest have verified readback on codex; raw images stay
on the builder. The independent evidence archive also has verified copies on
both hosts. Keep the current normalized A rollback input and original/calibrated
SPI backups. [Exact retained A custody](evidence/normalized-a-compatibility-custody-20261112.json).

## Reproducible construction

The two root-owned clean checkouts are
`/var/lib/a733-managed-root-candidate-20261112-02/project` and `project-repeat`.
Each binds all 1103 committed files and tree
`9b1c9d866b10aea352a4c950c21042ae29a8121b`. The source bundle is 4594605 bytes,
SHA-256 `f80da61374c5dea19202b1e5903b5b8f8892b7ce7ca0c65182d7ca16bb7dca33`.

Both ran `build/launch-deterministic-host-roots.py` with their own `--project`,
`--tag managed-runtime-20261112-02` or `-03`, and these unchanged inputs:

```text
--userspace /var/lib/a733-clean-replay-20261106/userspace-timesync-account-dates-01/work/export-timesync-result/rootfs.tar.gz
--diagnostic /var/lib/a733-bootstrap-inputs-20261107
```

Invoke Python with `-I -B`. The actual transient services used CPUQuota=200%,
MemoryMax=3G, RuntimeMaxSec=2h and UMask=0077; compilation remains limited to two
jobs. The unchanged launcher applies its own 3600-second timeout and resource
floors. Both jobs completed with unchanged package inventory and no remaining
mapped processes or phase mounts. Tags 02/03 are consumed; any later repetition
needs new tags and fresh guarded admission. See the
[creator and dependency policy](deterministic-host-roots.md).

This proves repeated fresh constructions on one qualified Ubuntu 22.04 builder.
It does not establish independently provisioned outer-builder reproduction.

## Compatibility and verification

Each construction authenticates 133 emitted files. Independent checks compare
complete corresponding raw/gzip bytes, verify all four gzip expansions and read
all four complete filesystem trees. Package contents match 84 sources, 85 runtime
files and 22 entrypoints. Four management files and two separately bound guest
boot units retain their exact bytes/modes. All reachable inode timestamp fields
and normalized superblock properties pass.

The complete historical 869-file checkout at
`50bc9e70698d4a09c3705594dcc61c64e1715cb9` accepts all four new roots through its
actual `Source` and `Manifest` readers with production defaults. Current readers
accept the exact retained normalized A and reproduce its selector bytes. These
checks use whole checkouts and actual 4 GiB artifacts. They are regular-file
checks, not live target staging or booted rollback.

The existing initramfs invokes the selected root's management preparation through
`chroot /newroot .../host-management.py prepare`. The new root supplies conditional
guest units; the compatible external BOOT/kernel/DTB/SPI components stay fixed.
The diagnostic DTB recorded in root provenance is not a replacement BOOT payload.

## Next physical steps

First complete the reviewed SPI restore/SD recovery/reinstall demonstration from
the retained recovery card. Then prepare a fresh concrete inactive-B deployment
plan, preserving working normalized A, BOOT, STATE and the manual `firstkvm`
cohort. Existing known-target authorization persists; fresh live identity and
independent plan review remain required.

[Deployment preparation](evidence/managed-candidate-deployment-preparation-20261112.json)
retains the already reviewed B selector and producer manifest on codex. After
fresh live guards, expand the pinned gzip directly into the private candidate's
`root.ext4`, with byte progress, exact length/hash, sparse output and fsync. Reuse
the prepared metadata; omit an extra onboard preparer copy/hash. The historical
updater still authenticates the candidate completely. Its initial migration
requires legacy confirmation/readback behavior; the new optional-readback UI
becomes available only after booting the new root. The research transfer draft
is not executable until its live guard precedes every STATE write.

Use the [artifact-bound fixture setup](managed-fixture-deployment.md) after matched B boots.
After deploying matched B, publish its explicit storage policy before runtime
initialization, prepare the disposable locked account and payload, then register
and enable the managed fixture. Prove actual nonroot KVM, unattended boot,
guest-owned shutdown, durable checkpoint/restart, coherent backup/restore,
disable, installed maintenance interruption/retry, host rollback/return and target
mount/shutdown ordering. The current updater must be exercised on an inactive
slot with disposable data, then the authenticated normalized A rollback root
restored before testing rollback and return. Follow the
[production plan](spi-production-readiness-plan.md) and
[lifecycle contract](guest-autostart-design.md). Complete the final SPI boot
counts and independent procedure reproduction. Reuse accepted endurance data.

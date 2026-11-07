# Retention for production repeats

The [November 7 evidence](evidence/repeat-build-retention-20261107.json)
records verified retention of eleven superseded expanded build leaves. Complete
archives and inventories remain on **both build and codex**. Current dated
userspace inputs, production components, exports, source caches and published
operator images remain available. No Orange Pi storage was accessed.

The retained builder directory is `/var/lib/a733-repeat-retention-20261107`;
the protected codex copy is the same directory with `/mirror` appended.
Each archive is `TAG.tar.gz`, with its `TAG.inventory.json`. The evidence binds
the exact catalog, controller hashes, mirror proof, independent review and
execution journal. Original phase receipts and every `work/repo` remain in place.

Only these leaves under `/var/lib/a733-clean-replay-20261106` are retired:

| Phase | Leaf below `work/` |
| --- | --- |
| `userspace-pipeline-retained-01` | `arm-root` |
| `userspace-pipeline-repeat-01` | `arm-root` |
| `userspace-foreign-recipe-01` | `arm-root` |
| `userspace-timesync-retained-01` | `arm-root` |
| `userspace-timesync-retained-02` | `arm-root` |
| `userspace-configure-recipe-01` | `arm-root` |
| `userspace-configure-01` | `arm-root` |
| `userspace-foreign-01` | `arm-root` |
| `normalized-profile-03` | `restored` |
| `root-metadata-backend-01` | `restored` |
| `time-profile-component-02` | `restored` |

The archive and inventory pairs total **1,609,440,455 bytes**. GNU tar compared
them against the original sources. Full independent codex verification checked
all archive/inventory hashes, exact namespaces, every member's content and
metadata, internal hardlinks and raw attributes. Native transfer and verification
kept the separate **256 MiB** free-space floor; verification ended with
434,700,288 bytes free.

Preparation initially refused final receipt publication because copied controller
files retained UID/GID 1000. All eleven archives already passed comparison; no
source was deleted. After correcting controller ownership, a separately reviewed
finalizer revalidated all sources, archives, pins, idle state and resources before
publishing the prepared receipt. No archive validation was bypassed.

The exact retirement controller passed four root-run end-to-end fixtures on
Ubuntu 22.04/Python 3.10, including refusal paths, rename/deletion, durable journal
and preserved sentinels. Independent source and actual-artifact reviews preceded
a successful read-only builder check. Execution rechecks complete retained
inputs, source identities, process/mount/loop references and protected phase
parents. It renames only the selected leaf through a held parent descriptor,
fsyncs, verifies its inode, deletes the quarantine and journals completion.
Inspect `retirement.jsonl` after an interruption; never rerun blindly.

All eleven removals and final postchecks passed. Observed builder free space rose
from **19,624,583,168** to **24,798,740,480 bytes** during retirement. This admits
the fresh root recipe; it does not reserve capacity for later stages. Keep the
unchanged 22 GiB full-composition guard and remeasure before every construction.

## Restore a historical leaf

Full restoration of these eleven actual archives has **not** been executed;
the disposable end-to-end fixture and complete archive verification are the
available evidence. Verify the exact archive hash from the linked evidence and
reserve expanded storage before restoring into a new, empty private directory:

```sh
restore_path=/var/lib/a733-restoration-userspace-foreign-01-new
sudo test ! -e "$restore_path" && sudo mkdir -m700 "$restore_path"
sudo tar --extract --gzip --sparse --numeric-owner --same-owner \
  --same-permissions --acls --xattrs --xattrs-include='*' \
  --file /var/lib/a733-repeat-retention-20261107/userspace-foreign-01.tar.gz \
  --directory "$restore_path"
```

The extracted top-level directory is `arm-root/` or `restored/`, as listed above.
Compare complete contents and recoverable metadata against the authenticated
inventory before use. Device/inode numbers, ctime, atime and allocated blocks
are observations of the old filesystem, not restoration guarantees. Keep
historical restoration separate from fresh build attempts. GNU tar documents
[extended attributes](https://www.gnu.org/software/tar/manual/html_node/Extended-File-Attributes.html)
and [sparse files](https://www.gnu.org/software/tar/manual/html_node/sparse.html).

## Sparse SD packaging

The same capacity preparation converted seven native generated SD files to
sparse allocation, recovering **1,540,018,176 bytes** with unchanged full hashes,
logical sizes, identities, ownership, modes and modification times. Only zero
extents were released; ctime changes are expected.

Committed source `626e3222d9d98587179722a925208fdca34127ee` also keeps new
regular-file SD copies sparse. Five assembly tests and all 1,614 privileged
Ubuntu unit tests pass, together with five shell suites and Bash checks. An
actual frozen-builder construction produces exactly the prior NVMe-first SD's
raw/gzip bytes: 268,435,456 logical bytes, **36,704,256 allocated bytes**.
The evidence records full hashes and the frozen receipt. This construction does
not prove physical boot or reproduce the full production deployment.

Sparse copy applies only to new regular image files. Physical flashing must
write zero bytes too, so old device contents are replaced. Installer policy
remains one expanded write with progress and optional readback.

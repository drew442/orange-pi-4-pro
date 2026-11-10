# Restore the recorded host's paired roots and STATE

**Physical paired restoration now passes:** interrupted p2 write, full retry
with readback, restored management/configuration and an actual nonroot KVM guest.
[Result and limitations](paired-host-restore-20261110.md).
The [original offline backup](evidence/offline-host-preservation-20261110.json)
remains immutable. [Independent preparation review](evidence/known-host-state-restore-preparation-20261109.json)
and privileged software validation remain distinct from this physical result.
This procedure restores the preserved host; it does not migrate the old selector
onto the normalized root pair.

`scripts/restore-known-host-state.py` restores only `/dev/nvme0n1p2`, `p3` and
`p4`, in that order. The fixed namespace is the 256060514304-byte SPCC M.2 PCIe
SSD, serial `134807190FC700149277`. ROOT-A/B are 4 GiB each; STATE is 8 GiB,
UUID `360dfb39-f49f-487e-b307-0bb887c854c5`. The helper does not mount, replay a
journal, select a root or reboot. BOOT, GPT, guest storage, SD and SPI are preserved.
Its existing storage authorization requires no new permission prompt.

Restoration is nontransactional. Any failure or missing final success means
remain in RAM recovery and retry the complete authenticated set. An unexpected
power cycle can expose partially restored partitions; write order does not make
this safe. Keep original dirty STATE bytes immutable off-device, including
after successful journal replay on the restored copy.

## Bind a verified preservation set

The source is the complete three-archive set from one offline, read-only
preservation session. Before making an index, review all three successful
receiver receipts, matching source/after-transfer boot and disk identities,
block protection and exclusive-session observations. Verify retained compatible
BOOT and the backup custody record. Boolean fields in an index are declarations
of those completed checks, not checks the restore tool can independently prove.
The index hash must come from this trusted off-device record, never merely from
the newly staged copy on the board.

In the private builder preservation directory, create `index.json` with these
exact keys. Replace capitalized placeholders from reviewed receipts; this is a
schema example, not a ready-to-run backup claim. Each archive must be named
`p2.img.gz`, `p3.img.gz` or `p4.img.gz` beside the index.

```json
{
  "schema": 1,
  "operation": "known-host-paired-backup",
  "serial": "134807190FC700149277",
  "model": "SPCC M.2 PCIe SSD",
  "namespace_bytes": 256060514304,
  "state_uuid": "360dfb39-f49f-487e-b307-0bb887c854c5",
  "export_boot_id": "PRESERVATION_RAM_BOOT_UUID",
  "boot_sha256": "cc2ad988fdc57579178766acaf09c71d688aaadec94b722593192cb24426c313",
  "exported_cohort": true,
  "offdevice_verified": true,
  "partitions": [
    {"number": 2, "archive": "p2.img.gz", "encoded_bytes": 0,
     "encoded_sha256": "ARCHIVE_SHA256", "raw_bytes": 4294967296,
     "raw_sha256": "DECODED_SHA256"},
    {"number": 3, "archive": "p3.img.gz", "encoded_bytes": 0,
     "encoded_sha256": "ARCHIVE_SHA256", "raw_bytes": 4294967296,
     "raw_sha256": "DECODED_SHA256"},
    {"number": 4, "archive": "p4.img.gz", "encoded_bytes": 0,
     "encoded_sha256": "ARCHIVE_SHA256", "raw_bytes": 8589934592,
     "raw_sha256": "DECODED_SHA256"}
  ]
}
```

For each entry, `encoded_bytes`/`encoded_sha256` come from receipt
`archive_bytes`/`archive_sha256`; `raw_sha256` comes from `decoded_sha256`.
Require `decoded_bytes` equal the fixed raw size and receipt `success: true`.
Record the exact index size and SHA-256 off-device. Receipts' raw hashes are
receiver-derived, not separate source-device measurements. Their completed full
archive decode is essential provenance for the compressed-only restore preflight.

## Prepare and apply in RAM recovery

Use the [known SSH RAM recovery environment](deploy-protected-20261109.md#enter-the-existing-private-ram-recovery-environment).
Require the initial host namespaces, visible proc, AArch64 pinned maintenance
kernel, RAM root, dedicated `/run` tmpfs and no active swap. Confirm exact disk
identity and compatible BOOT; no NVMe partition may be mounted or in use.

Stage the index, archives, new helper and its pinned adjacent
`write-known-nvme-boot.py` dependency privately over authenticated SSH. The helper
executes only that dependency's authenticated bytes. Use a root:root-owned mode-0700
directory `/run/a733-restore`; all staged files are mode 0600, regular and
single-link. Authenticate both source scripts against the reviewed source pins
before invoking them. The restore helper SHA-256 is
`2332cae64cafdc03cde0e7023df460235a89efcdd5b2d9ed376389421e9e0c9f`;
its adjacent dependency is 28,670 bytes, SHA-256
`39adee8bd81186bc1665701843d393c9ceb7f16466fca304b6a3cf43993999d9`.
No private STATE archive belongs in Git or public delivery.

The total encoded set must fit the fixed 2 GiB limit and available tmpfs; at
least 3 GiB available memory is required. Expand the bounded RAM staging mount
only after checking capacity. Do not stage any raw 4/8 GiB partition image.
If preservation left block read-only flags set, clear them explicitly for the
known namespace and its five partitions only when beginning this authorized
offline restore; verify every flag is zero. This grants write capability, not
permission to skip target or consumer checks.

In the board's RAM root shell, set the current boot UUID and trusted off-device
index pin. Use fresh plan/report paths for each attempt:

```sh
set -eu
umask 077
: "${A733_RESTORE_BOOT_ID:?Set current verified RAM boot UUID}"
: "${A733_BACKUP_INDEX_SHA256:?Set trusted off-device index pin}"
python3 -I -B /run/a733-restore/restore-known-host-state.py prepare \
  --backup-index /run/a733-restore/index.json \
  --index-sha256 "$A733_BACKUP_INDEX_SHA256" \
  --expected-boot-id "$A733_RESTORE_BOOT_ID" \
  --plan /run/a733-restore/plan.json --report /run/a733-restore/prepare.json \
  --readback > /run/a733-restore/prepare-terminal.json
```

Review the plan, source pins, fixed targets and write order. Retain the plan and
prepare receipt off-device and obtain its exact plan hash from that trusted
copy. The optional `--readback` must be the same for prepare and apply; include
it for the first physical restoration acceptance run. Routine use may omit it.

```sh
: "${A733_RESTORE_PLAN_SHA256:?Set reviewed off-device plan pin}"
python3 -I -B /run/a733-restore/restore-known-host-state.py apply \
  --backup-index /run/a733-restore/index.json \
  --index-sha256 "$A733_BACKUP_INDEX_SHA256" \
  --expected-boot-id "$A733_RESTORE_BOOT_ID" \
  --plan /run/a733-restore/plan.json \
  --plan-sha256 "$A733_RESTORE_PLAN_SHA256" \
  --report /run/a733-restore/apply.json --readback \
  > /run/a733-restore/apply-terminal.json
```

Preparation authenticates compressed archives and bounded archived superblock
prefixes. It does not decode all 16 GiB again. Apply checks stream CRC/EOF,
expanded lengths and raw hashes while writing; a discrepancy is a partial
restore. All three partition claims must succeed before any write. Existing
STATE filesystem validity is not an admission requirement, permitting retry
following an interrupted STATE write. A source, context, claim, write, flush,
readback, report or cleanup failure cannot establish completion.

Require process exit zero. The stored report is deliberately conservative; it
never grants final success. Verify the single terminal JSON record and its
exact progress-report binding, then retain both files and console progress
off-device before leaving RAM:

```sh
python3 -I -B - <<'PY'
import hashlib, json
from pathlib import Path
base = Path('/run/a733-restore')
terminal = json.loads((base / 'apply-terminal.json').read_bytes())
progress = (base / 'apply.json').read_bytes()
assert terminal['status'] == 'restore-complete'
assert terminal['progress_report_sha256'] == hashlib.sha256(progress).hexdigest()
assert json.loads(progress)['status'] == 'operations-complete-awaiting-terminal-receipt'
print('Terminal/progress binding matches; physical acceptance remains pending.')
PY
```

This check does not replace the recorded process exit. A terminal file alone
also cannot prove success if its output write failed. Final software
success is not hardware acceptance: boot the preserved compatible route, verify
selected root, restored configuration and pinned management identity, then run
an actual KVM guest. Preserve interrupted-retry evidence separately. The [recorded physical run](paired-host-restore-20261110.md) meets these
restored-host checks; fresh normalized installation remains separate.

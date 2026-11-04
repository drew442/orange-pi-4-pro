# Restore directly from HTTPS backup storage

The HTTPS restore source avoids staging a compressed archive on the destination
NVMe. A pinned chunk index authenticates each 1 MiB of encoded data before the
gzip decoder receives it. The full archive size/hash, canonical archive framing,
manifest, history and payload hashes must also pass before the new guest is
published. The [fresh-destination restore guards](guest-restoration.md) remain
mandatory. No physical board network or restored KVM test has passed.

## Create the index on the backup machine

First finish [backup transfer and independent receiver readback](guest-backups.md).
Use the resulting protected archive and independently trusted pins. In a shell
on that separate machine, with a trusted complete portable tool tree and fixed storage:

```bash
set -euo pipefail
umask 077
set -C
: "${A733_BACKUP_TOOLS:?Set absolute trusted complete portable tool tree}"
: "${A733_VERIFIED_BACKUP:?Set absolute verified receiver output directory}"
: "${A733_BACKUP_ARCHIVE_SHA256:?Set successful authenticated source archive pin}"
: "${A733_BACKUP_MANIFEST_SHA256:?Set trusted source manifest pin}"
: "${A733_BACKUP_CHUNK_INDEX:?Set new absolute index pathname on fixed backup storage}"
/usr/bin/python3 -I -B "$A733_BACKUP_TOOLS/scripts/index-guest-backup.py" \
  "$A733_VERIFIED_BACKUP/archive.tar.gz" \
  --archive-sha256 "$A733_BACKUP_ARCHIVE_SHA256" \
  --manifest-sha256 "$A733_BACKUP_MANIFEST_SHA256" \
  --new-output "$A733_BACKUP_CHUNK_INDEX" \
  > "$A733_BACKUP_CHUNK_INDEX.receipt.json"
cat "$A733_BACKUP_CHUNK_INDEX.receipt.json"
```

Require `success`, `published` and `durable` all true. Save its `index.sha256`
alongside the archive and source manifest pins in the trusted backup inventory.
Do not replace it with an unexplained downloaded index hash. The generator
checks the entire compressed archive before parsing its first manifest and
publishes a new `0400` index atomically. It neither repeats every extracted
payload readback nor proves guest restorability; full receiver verification
remains a prerequisite.

The format fixes 1,048,576-byte encoded chunks, a 251 GiB archive cap and a
32 MiB index cap. The last chunk may be shorter. Chunk count is derived from
the authenticated archive length; no missing or additional chunk is accepted.
The index contains the original archive hash and source manifest pin. Its
maximum encoded chunk count is 257,024; it never contains guest disk contents.

## Configure a protected HTTPS endpoint

Make the exact archive and index available from an existing protected HTTPS
static-file service on the backup machine. Restrict network access to the
authorized destination and retain the backup machine's existing access policy.
This repository does not configure or expose a public backup server. The archive
contains private guest data; compression does not encrypt stored files.

The service must use a certificate trusted by the destination, correct hostname
and TLS 1.2 or newer. Responses must have status 200, one exact `Content-Length`,
no `Transfer-Encoding`, and no content encoding except optional `identity`.
Honor `Connection: close` and serve the stored bytes unchanged. Redirects,
proxies, HTTP, URL credentials, query strings and fragments are refused. No
range requests, automatic retries or partial-download resume are implemented.
Use stable credential-free URLs on the restricted network. Endpoint deployment
and real board DHCP/DNS/TLS reachability still need reproduction.

The destination uses system CA trust by default. For a private CA, stage its
public certificate in a root:root, single-link `0400` or `0444` file with
protected ancestors and add `--ca-file /absolute/protected/ca.pem` to both
commands below. The certificate bytes, path and held metadata are bound into
the plan. Verification cannot be disabled. Source TLS contexts ignore
`SSLKEYLOGFILE`, so they do not create environment-selected local key logs.
See Python's [TLS client contexts](https://docs.python.org/3.11/library/ssl.html#ssl.PROTOCOL_TLS_CLIENT)
and [HTTP opener behavior](https://docs.python.org/3.11/library/urllib.request.html#urllib.request.build_opener).

## Prepare and apply on the destination

Complete storage/account activation and source-backup verification as described
in the restoration guide. Keep the guest identity stopped and choose an absent
destination. In its root shell, set trusted values:

```bash
set -euo pipefail
umask 077
set -C
: "${A733_GUEST_TOOLS:?Set absolute trusted complete installed or portable tool tree}"
: "${A733_BACKUP_ARCHIVE_URL:?Set protected HTTPS archive URL}"
: "${A733_BACKUP_CHUNK_INDEX_URL:?Set protected HTTPS index URL}"
: "${A733_BACKUP_CHUNK_INDEX_SHA256:?Set trusted successful index receipt pin}"
: "${A733_BACKUP_ARCHIVE_SHA256:?Set original trusted compressed archive pin}"
: "${A733_BACKUP_MANIFEST_SHA256:?Set trusted source manifest pin}"
: "${A733_DESTINATION_GUEST_UUID:?Set verified destination p5 UUID}"
/usr/bin/python3 -I -B "$A733_GUEST_TOOLS/scripts/restore-guest.py" prepare \
  --archive-url "$A733_BACKUP_ARCHIVE_URL" \
  --chunk-manifest-url "$A733_BACKUP_CHUNK_INDEX_URL" \
  --chunk-manifest-sha256 "$A733_BACKUP_CHUNK_INDEX_SHA256" \
  --archive-sha256 "$A733_BACKUP_ARCHIVE_SHA256" \
  --manifest-sha256 "$A733_BACKUP_MANIFEST_SHA256" \
  --guest-uuid "$A733_DESTINATION_GUEST_UUID" \
  > /run/guest-https-restore-plan.json
chmod 0400 /run/guest-https-restore-plan.json
cat /run/guest-https-restore-plan.json
```

Selected-host, namespace, guest-root storage and destination UUID checks precede
network contact. Guest-specific account, service, UID-process, resource and
free-space checks follow the authenticated initial read-only scan and precede
destination writes. Preparation creates no guest files. Review URLs, index pin,
CA policy, archive/source manifest pins, history envelope, configuration,
destination UUID/storage and new manifest pin.

Apply the same options and plan from an interactive TTY:

```bash
/usr/bin/python3 -I -B "$A733_GUEST_TOOLS/scripts/restore-guest.py" apply \
  --archive-url "$A733_BACKUP_ARCHIVE_URL" \
  --chunk-manifest-url "$A733_BACKUP_CHUNK_INDEX_URL" \
  --chunk-manifest-sha256 "$A733_BACKUP_CHUNK_INDEX_SHA256" \
  --archive-sha256 "$A733_BACKUP_ARCHIVE_SHA256" \
  --manifest-sha256 "$A733_BACKUP_MANIFEST_SHA256" \
  --guest-uuid "$A733_DESTINATION_GUEST_UUID" \
  --plan /run/guest-https-restore-plan.json
```

The same `SOURCE BACKUP VERIFIED` and exact `RESTORE` authorization phrases
apply. Require final `success`, `published` and `durable`; retain the new manifest
pin and receipts. Local `--archive` and HTTPS `--archive-url` are mutually
exclusive. Network-only options are refused with a local archive.

The source fetches the complete archive three times: preparation, apply review
and private staging. Every pass checks the pinned chunks and full archive;
changed service contents cannot silently change a restored guest. Reserve the
normal full logical payload/history plus 256 MiB margin, without an additional
local encoded archive. RAM holds the bounded index, history and streaming
buffers. Large-index peak memory still needs measurement against actual board
RAM; the 128 MiB fixture does not establish full-capacity board performance.

Each index fetch and archive scan has a total deadline, default 86,400 seconds,
with a socket timeout at most 30 seconds. Override identically in both commands
using `--network-timeout-seconds N` within 1–172,800. A changed timeout or other
source description invalidates the plan. The Linux timer guard requires the
main thread with no pre-existing real-time alarm; underlying uninterruptible
kernel operations and every libc DNS behavior are outside that guarantee.

Malformed transport, wrong chunk, truncation, suffix, callback error or failed
readback leaves staging unpublished. A late failure may follow earlier private
writes, which are cleaned using owned inode identities. Publication failure
handling and provenance follow the restoration guide. No encoded archive is
written by the HTTPS source.

## Software evidence and remaining acceptance

Real TLS regressions cover certificate/hostname/redirect refusal, changed
chunks before decoding, exact framing/EOF, deadlines, full restore readback
and a changed final chunk after private writes. The opt-in ARM probe uses the
pinned rootfs and guest fixture with a real loopback TLS server:

```bash
sudo /usr/bin/python3.10 -I -B tests/verify_guest_restore_https_arm.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/recipe/out/guest-fixture-20261104-03
```

It compares HTTPS restoration against local restoration, including exact
manifest/history and all payload bytes, and generates the same index natively
with actual fixed-storage guards. Hardware policy boundaries are substituted
only inside the disposable ARM fixture. Both ends run on the builder. Actual
off-device retention, production endpoint deployment, board networking, full
capacity/resource behavior, restored KVM execution and physical recovery remain
required before release.
The [recorded ARM evidence](evidence/guest-restore-https-arm-20261104.json)
contains source hashes, archive/index pins, native index results and fixture limits.

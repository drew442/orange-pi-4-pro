# Normalized resolver-readable full NVMe image

Two fresh-checkout constructions now match **every raw and gzip byte** and pass
[independent actual-artifact verification](evidence/normalized-full-nvme-image-20261109.json).
The selected distribution is **unpersonalized and unbooted**. Physical installation,
resolver-policy checks and independent deployment reproduction remain pending.
Leave the running endurance host undisturbed.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Logical NVMe image | 256,060,514,304 | `1fa9144d7a1269066c91a94bfb105a9ac4e4083d4707dd6791e8ac2f26902cd9` |
| `nvme.img.gz` | 545,581,074 | `3bc02f46d272dcac591d435fff2b1bc98d4fb048569fb2120746145929a8ad64` |
| Selected `manifest.json` | 118,399 | `3cbe28d6154d1bdeb8d364bd0f8bf82b61fd40af7be27fbd49695771c4fad08f` |

Native selected files are under `out/normalized-nvme-candidate-20261109/` on
codex, including `verification.json`. The second manifest is 118,399 bytes,
SHA-256 `37f63f75595747254811722b8a7e1131ba96ca33068c708b2caf5f1bb84ca281`;
its resource/timing measurements differ while both image files match.
Builder outputs remain under
`/var/lib/a733-normalized-nvme-source-20261109-01/project-NN/out/public-config-nvme-image-deterministic-20261109-NN`,
for `NN=01,02`, source `81f907481b12e9639ad23752966e05166074f96f`.
Both producer units exited 0; quotas were two and one CPU respectively, each
with a 3 GiB ceiling. This is repetition on one qualified builder.

## Fixed composition and invocation

`build/public-config-nvme-image.py --root-profile deterministic-20261109`
authenticates the protected `675d6b…` base, [normalized roots](deterministic-host-roots.md)
and [matched STATE](public-config-state.md). It replaces only
**[537,919,488, 17,717,788,672)**: 4 GiB ROOT-A, 4 GiB ROOT-B and 8 GiB STATE.
Protected BOOT, GPT, GUESTS and every other byte are preserved. The unchanged
256 MiB SD bootstrap fits the actual 1,977,614,336-byte card.

The root manifest is `c1bcc7d2…`, raw A `a9715b81…`, raw B `42fb2bec…`, STATE
`2e9d3dcf…`, selected A marker `286494…` and STATE UUID
`360dfb39-f49f-487e-b307-0bb887c854c5`. Complete pins are in source and linked
evidence. The finite profile exposes no hash, UUID or layout override.
The default `public-dirs-20261109` profile preserves the historical construction;
select `deterministic-20261109` explicitly for this distribution.

The following form was exercised with each fresh project checkout and output
suffix `deterministic-20261109-01` or `-02`. Those names are consumed; use a fresh
output suffix for another construction:

```sh
: "${A733_IMAGE_PROJECT:?Set the authenticated recipe checkout}"
: "${A733_BASE_IMAGE:?Set retained protected-state-nvme-image-20261109-01 directory}"
: "${A733_NEW_ROOTS:?Set host-payload-timesync-deterministic-02 directory}"
: "${A733_NEW_STATE:?Set host-state-timesync-public-dirs-01 directory}"
python3 -I -B "$A733_IMAGE_PROJECT/build/public-config-nvme-image.py" \
  --root-profile deterministic-20261109 \
  --base-gzip "$A733_BASE_IMAGE/nvme.img.gz" \
  --base-manifest "$A733_BASE_IMAGE/manifest.json" \
  --roots-manifest "$A733_NEW_ROOTS/manifest.json" \
  --root-a "$A733_NEW_ROOTS/root-A.ext4" \
  --root-b "$A733_NEW_ROOTS/root-B.ext4" \
  --state-manifest "$A733_NEW_STATE/manifest.json" \
  --state "$A733_NEW_STATE/state.ext4" \
  --output "$A733_IMAGE_PROJECT/out/public-config-nvme-image-FRESH"
```

Admission remains 23,760,999,827 available bytes; runtime floors remain
3,000,000,000 disk bytes and 1,000,000,000 available memory bytes. The compressed
output ceiling is 2 GiB plus 16 MiB scratch headroom. No full raw staging file
is created. Remeasure resources before another job.

## Verification and deployment scope

The producer authenticates all seven inputs before parsing JSON, validates
kernel/UUID/operator/selector pairing, checks bounded single-member CRC/EOF and
complete base/replacement hashes, and separately decodes its complete output.
Exact generated wire bytes, all held inputs and recipes are reauthenticated;
files/directories are synced and the manifest is published last. A final filename
without successful exit and complete manifest is insufficient.

The independent verifier imports no producer code. It directly compares both
complete new raw streams and gzip files, checks every replacement byte against
held components, recomputes full raw/partition/range hashes and validates both
GPT tables. Each complete unchanged-region hash matches the authenticated
[prior full direct old/new proof](evidence/public-config-nvme-image-20261109.json),
covering 238,880,645,120 bytes per image. That composition avoids decoding the
old 256 GB stream again; this run does not claim a new direct old-byte comparison.

These regular-file checks do not add Orange Pi installation readback, which
remains optional. Use the [prepared same-board deployment guide](deploy-protected-20261109.md)
after retaining endurance/backup evidence and the changed-slot recovery tests.
Preserve the existing private identity and restore current guests. This image is
not a backup of the running board. Physical boot, corrected resolver policy,
KVM/recovery and final release acceptance require their own evidence.

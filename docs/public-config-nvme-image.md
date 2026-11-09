# Matched resolver-readable full NVMe recipe

`build/public-config-nvme-image.py` prepares a complete regular-file distribution
from the authenticated protected image, [corrected roots](public-config-roots-20261109.md)
and [matched STATE](public-config-state.md). [Independent code review and 2,167 privileged Ubuntu tests](evidence/public-config-nvme-preparation-20261109.json)
pass. The first full production stream now completes with producer exit 0;
independent actual-artifact review is running. Repeat construction and physical
installation remain pending; the running endurance host is unchanged.

The recipe replaces only the contiguous ROOT-A, ROOT-B and STATE extents:
**[537,919,488, 17,717,788,672)**, totaling 16 GiB. The complete logical image
remains **256,060,514,304 bytes**. Protected BOOT, both GPT tables, GUESTS and all
other bytes are preserved. The 256 MiB SD bootstrap remains unchanged and fits
the actual 1,977,614,336-byte SD card.

All seven component/authority inputs have fixed size and SHA-256 pins in the
recipe. There are no pin or layout overrides. Before JSON interpretation, the
producer authenticates the entire input closure, then validates UUID, kernel,
operator-key/configuration and root/STATE pairing. The output manifest updates
both the three partition payload hashes and the selected root marker. Old
`source_manifest` metadata remains historical; new resource/tool measurements
and derivation proofs describe this invocation.

## Prepared invocation

Use a fresh authenticated checkout containing the committed reviewed recipe,
create its ignored `out` directory, and select an unused output tag:

```sh
: "${A733_IMAGE_PROJECT:?Set the authenticated recipe checkout}"
: "${A733_BASE_IMAGE:?Set retained protected-state-nvme-image-20261109-01 directory}"
: "${A733_NEW_ROOTS:?Set host-payload-timesync-public-dirs-01 directory}"
: "${A733_NEW_STATE:?Set host-state-timesync-public-dirs-01 directory}"
python3 -I -B "$A733_IMAGE_PROJECT/build/public-config-nvme-image.py" \
  --base-gzip "$A733_BASE_IMAGE/nvme.img.gz" \
  --base-manifest "$A733_BASE_IMAGE/manifest.json" \
  --roots-manifest "$A733_NEW_ROOTS/manifest.json" \
  --root-a "$A733_NEW_ROOTS/root-A.ext4" \
  --root-b "$A733_NEW_ROOTS/root-B.ext4" \
  --state-manifest "$A733_NEW_STATE/manifest.json" \
  --state "$A733_NEW_STATE/state.ext4" \
  --output "$A733_IMAGE_PROJECT/out/public-config-nvme-image-FRESH"
```

The exact retained builder inputs are linked by the component guides and
[protected full-image evidence](evidence/protected-full-nvme-image-20261109.json).
ROOT-A is `299b1f2f…`, ROOT-B `48b9876f…`, STATE `2e9d3dcf…`; their authority
manifests are `1f82…` and `0c6a…`. The base is the protected `675d6b…` logical
image, gzip `584a…`, manifest `f19b…`. Complete hashes are fixed in source and
linked evidence; abbreviated hashes here are labels, not verification commands.

Admission remains **23,760,999,827 available bytes**. Runtime floors remain
3,000,000,000 disk bytes and 1,000,000,000 available memory bytes. The 2 GiB
compressed-output ceiling plus 16 MiB headroom bounds scratch use; it does not
assert the expected output size. No full 256 GB raw staging file is created.

## Verification and publication

The source is decoded as exactly one gzip member with CRC/trailer, EOF and a
hard expansion bound. The complete original raw hash and old partition hashes
must match. The producer hashes complete replacement extents and unchanged
prefix/suffix, plus all partitions and bounded GPT structures. GPT checks cover
protective MBR, both header/table CRCs, geometry and identities.

A separate complete output decode verifies the new logical hash and every
captured range. An online SHA-256/count of the one-member encoder's exact wire
bytes must also match the reread compressed file: appended empty gzip members
or altered headers cannot be silently adopted. All component and recipe inputs
are held and reauthenticated before publication.

Wire data, manifest and publication directories are synced. `manifest.json` is
published last; failed tags are reserved. Require a successful producer exit,
the complete manifest and independent actual-artifact verification. A final
filename alone does not prove a successful or durable build.

These checks run on builder regular files. They do not add installation
readback on the Orange Pi; physical installation readback remains optional.
The image is an unpersonalized distribution, not a backup of the running board.
Retain its outcome, identity and current guests before any later physical
installation. Actual boot, KVM, resolver policy and release acceptance require
their own hardware evidence.

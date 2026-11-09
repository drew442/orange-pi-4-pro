# Protected STATE full NVMe distribution recipe

`build/protected-state-nvme-image.py` packages the fixed protected BOOT component
inside a complete **256,060,514,304 logical-byte** NVMe gzip distribution. This is
software preparation for the Orange Pi 4 Pro A733, 2 GB SD and nominal 250 GB NVMe
minimal host with zero routine SD writes. The recipe has ordinary-file tests;
**full construction and independent artifact review are pending**. It grants no
physical boot, dirty-STATE recovery, KVM or endurance acceptance.

The [authorized builder expansion](build-host-capacity-addition-20261109.md)
left 25.30 GB free; remeasure before each invocation. Admission remains
**23,760,999,827 free bytes**, with runtime floors of **3,000,000,000 disk bytes**
and **1,000,000,000 available memory bytes**. Keep the guard unchanged. The operator approves available free host capacity
for future expansion; exact-target validation and independent review remain required. The 2 GiB encoded-output ceiling plus 16 MiB metadata/headroom
is included in the scratch budget; admission requires the larger of the existing
guard and disk floor plus this budget. The encoded-output ceiling is enforced
while writing; it is a refusal limit, not an assertion of the future gzip size.
There is no 256 GB raw staging file or sparse raw staging image.

After capacity qualifies, use held, absolute regular-file paths without symlinks
or hardlinks, a quiescent checkout and a fresh output tag:

```sh
python3 build/protected-state-nvme-image.py \
  --base-gzip /absolute/retained/nvme.img.gz \
  --base-manifest /absolute/retained/manifest.json \
  --new-boot /absolute/protected-component/boot.ext4 \
  --output "$PWD/out/protected-state-nvme-image-FRESH"
```

There are no pin overrides. Before parsing the manifest, the producer authenticates
these complete fixed inputs:

| Input | Bytes | SHA-256 |
| --- | ---: | --- |
| Historical whole gzip | 545632496 | `3e560264bb64a9bc23b342febdff2146955ca97cb6e0279e5240c29d4e6d85bc` |
| Historical manifest | 35023 | `addfcafe239a2aa075435383cc4742869d256d98beecd2526806da206d98f4a2` |
| New raw BOOT | 536870912 | `cc2ad988fdc57579178766acaf09c71d688aaadec94b722593192cb24426c313` |

The historical manifest is retained locally as
`out/vgic-nvme-image-20261108/build-02.stdout`; new BOOT provenance is in
`out/serial-recovery-20261109/protected-nvme-boot-build-01.stdout` and
[the protected component recipe](protected-state-nvme-boot.md). These ignored
records are evidence inputs, not dependencies of the regression tests.

The base gzip is expanded once, checking one-member EOF/CRC, exact raw size and
whole raw SHA-256
`835031b81d0320d6f9f35e74b9be67b494006c26a25fa9070f3e0a8f434ebc11`.
Expansion stops on the first byte beyond the fixed nominal size. At offset
1,048,576, exactly 536,870,912 bytes are substituted with held new BOOT bytes.
The consumed original BOOT must match
`5860889755d7479e619e86b83bc32237e51bacd3a3fd2e2d2ebe976898f44899`.
Every original byte is consumed and authenticated, including the replaced span.

The producer hashes old/new prefix and suffix independently, all five partition
extents and each GPT extent. It permits differences only inside p1. ROOT-A,
ROOT-B and STATE additionally match their authenticated historical payload pins;
GUESTS receives a complete fresh before/after hash. Both GPT tables, header and
array CRCs, protective MBR, GUIDs, five-partition layout and fixed extent pins are
validated from bounded in-memory captures. Disk metadata, roots, STATE and
GUESTS are inherited unchanged from the authenticated stream.

The new full logical raw hash is computed during substitution. Gzip uses level 9,
mtime zero and an empty filename. A separate complete `gzip.GzipFile` expansion
checks the actual output CRC/trailer, exact logical size, full new raw hash and
all captured ranges before publication. All input and imported recipe files
remain held and are reauthenticated before publication. Determinism applies to
the same Python/zlib implementation; both versions are recorded.

The manifest inherits only unchanged payload identity, geometry and boot policy
through an explicit allowlist. The complete historical manifest is retained under
`source_manifest`; its build duration, allocated raw staging bytes, builder
packages, source epoch and older provenance remain historical. New `resources`,
`tool_versions`, `sources`, `files` and derivation proofs describe this packaging
run. Historical construction measurements are never copied as current evidence.

Only `nvme.img.gz` and `manifest.json` are published. During work the stream is
`nvme.img.gz.partial`. A failed directory is reserved and cannot be reused;
absence of the final manifest means incomplete packaging. Independent complete
artifact inspection remains required after an actual full construction.

This stream is an **unpersonalized distribution**, not a backup of the current
board. Its whole-device hash cannot describe the installed personalized NVMe.
Do not write it over the running board to deploy this BOOT fix. Physical BOOT
maintenance uses the separate reviewed component updater and live rollback
capsule. The ongoing physical 24-hour KVM run and its independent UART capture
must remain undisturbed.

Validation uses:

```sh
python3 -m unittest discover -s tests -p test_protected_state_nvme_image.py
```

Twelve tests cover scaled substitution across chunk boundaries, zeros, deterministic
gzip bytes, full decode/hash, short/overlong/corrupt or multimember sources,
short replacement, changed outside-range proof, compressed-output refusal,
unsafe paths and held-file mutation, existing-output refusal, resource floors,
prepublication source changes and both GPT table/header CRC/geometry checks.
The integration fixture exercises publication with ordinary small files and a
separate synthetic GPT fixture; it does not construct the full distribution.

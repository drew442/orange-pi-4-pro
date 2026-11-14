> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Registered full NVMe repeat controller

`build/launch-nvme-host-repeat.py` binds the verified second
[roots](../../production-host-roots-repeat.md), [state](../../production-host-state-repeat.md)
and [bootstrap](../../production-host-bootstrap-repeat.md) to a fresh regular-file
NVMe composition. The [source review](../../evidence/nvme-repeat-controller-20261107.json)
approves this adapter; it does not establish a constructed image or stream pass.

Committed-source planning and the full privileged Ubuntu suite pass at producer
revision `339eef1`: **1,639 unit tests without skips**, five shell suites and five
assembly cases. Actual composition now passes at tag `timesync-repeat-01`, with a complete
producer receipt. Independent stream verification is running; no stream pass or
physical acceptance is claimed yet.

The adapter authenticates its unchanged historical controller and applies only
12 counted AST string-value amendments. It fixes all three component records by
raw, canonical JSON and Git blob pins. The nested bootstrap/state families hold
their authenticated templates through final publication. The original actual
receipt/manifest/readback checks remain active; bootstrap provenance is
explicitly attributed to its existing authenticated evidence.

The CLI remains `--project ABSOLUTE_CHECKOUT --tag FRESH_TAG`, with `--plan`
for committed-source planning. The output is a new regular file at
`/var/lib/a733-clean-replay-20261106/nvme-TAG/work/project/out/nvme-image-TAG`.
A used tag cannot be reused. Commit reviewed source, run the actual CLI plan,
verify builder resources, then construct in the frozen namespace. Preserve the
22 GiB recipe guard **plus** metadata/Git staging and all runtime floors; do not
increase the LV or lower guards. Physical geometry remains the recorded
256,060,514,304-byte SPCC device with 512-byte sectors.

Root/state/BOOT/GUESTS parent-byte and filesystem checks, exact target evidence,
4096-descriptor admission, capsule/source authentication, private UID/network,
firmware mask, compression and cleanup remain unchanged. The boot stack remains
experimental EL2 with the reviewed SPI-to-NVMe BOOT script. This does not program
SPI, select physical storage or establish boot without SD.

The historical independent NVMe stream reader fixes the first producer's
revision/phase/UUID. A separate authenticated repeat reader must be prepared
once the new producer revision is fixed, then verify the complete gzip/raw/GPT
and partition streams. Do not relabel the first report as a second construction.
Hardware boot, both-key SSH, actual KVM, SD-write, recovery/endurance and
independently reproduced deployment remain mandatory. Use the
[existing-install repair](../../installed-host-repair-20261107.md) for the current board;
keep operator images and active endpoints unchanged.

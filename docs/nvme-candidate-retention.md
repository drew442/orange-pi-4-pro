# Retained production NVMe candidate

The [full production NVMe construction](production-nvme-composition.md) now has
an independently reviewed encoded delivery copy on codex. The
[retention evidence](evidence/nvme-candidate-retention-20261107.json) records the
exact copy, derived delivery manifest and capacity checks. No new deployment
endpoint or physical installation was performed.

The retained directory is:

```text
/home/drew/orange-pi-4-pro/out/nvme-candidate-retained-20261107
```

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `nvme.img.gz` | 545,633,126 | `9e8be89dbcabe30799744201ea94de93ce343134d0a8e034fa44281c8dd13955` |
| `manifest.json` | 10,481 | `b48b237b82281ac61b0c02354430f3d5481badeeef6a24257ebb01fdef1262e0` |
| `source-manifest.json` | 9,393 | `fa01243f5884bfaf3dae92f5974a2899dd93836b7c6c23d3d0a509d7c572e90c` |

The gzip is the original frozen-builder output, transferred without
recompression. A held source descriptor verifies its complete hash and unchanged
identity; the receiver verifies the incoming bytes, fsyncs the private file and
hashes its local readback. The prior independent stream report already verified
its full 256,060,514,304-byte expansion. This copy operation does not repeat that
expensive decode or imply a second construction.

The delivery manifest preserves every original source-manifest field. It adds
only `files.nvme.img.gz` and `distribution`, recording the original level-9,
zero-mtime, empty-filename compression and the completed stream proof. The
original source manifest and stream report remain unchanged alongside it. Both
acquisition and installer manifest validation pass against current source,
which matches the refreshed installer's relevant manifest policy. Physical
acquisition/install and a tested matched delivery procedure remain open.

## Capacity recovery

Before retaining the gzip, the native host lacked capacity for it plus the
unchanged **256 MiB** free-space reserve. An independently reviewed, fixed-file
controller converted these three generated image copies to sparse allocation:

| Generated file under `out/` | Allocated bytes before | Allocated bytes after |
| --- | ---: | ---: |
| `diagnostic-20261104/a733-diagnostic.img` | 268,439,552 | 31,481,856 |
| `el2-diagnostic-20261104/a733-el2-diagnostic.img` | 268,435,456 | 31,481,856 |
| `host-deployment-20261106/a733-host-sd.img` | 268,435,456 | 32,591,872 |

This recovers **709,754,880 allocated bytes** by removing allocated zero extents.
All three full hashes, logical sizes, inodes, ownership, modes and mtimes match
before/after. Ctime changes as expected. No physical board media was touched,
and the published host SD content remains unchanged. The controller checks held
FD identity, complete hashes, all-thread/loop inactivity, exact reviewed code
and readiness pins, and durable execution intent/results. Five actual fallocate
fixtures and eight independent read-only parent-policy checks pass. The
one-off code and complete records are retained in
`out/native-image-sparse-20261107`; its completed operation cannot be rerun.

Free space after sparse conversion is 1,111,334,912 bytes; the gzip retention
reports 565,215,232 bytes remaining, above 268,435,456. Later small records consume
some of that margin. Builder capacity is separate: the completed NVMe build
left 22,245,568,512 bytes, below its next 22 GiB initial admission guard before
staging overhead. No further LV growth or guard reduction is authorized.

## Next board action

The new candidate and its matching bootstrap use state UUID
`fc8bbef6-9385-4ddb-90ba-e41eaccc035b`; the currently installed state uses
`00fd936c-49fb-4fea-87c8-928b9c1599e0`. Do not flash the new bootstrap alone,
replace the active delivery files or reinstall to fix DHCP. Apply the
[existing-install machine-id repair](installed-host-repair-20261107.md), then
return the complete serial log, DHCP address and SSH results with both keys.

Clean construction repeats, independent deployment reproduction, original SPI
backup/recovery, authorized programming, SD-removed boot, actual KVM guest
execution and the full hardware acceptance matrix remain open. The encoded
copy and its review provide no additional hardware acceptance.

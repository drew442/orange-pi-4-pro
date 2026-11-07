# Second full production NVMe construction

The [completed second construction](evidence/production-nvme-repeat-20261107.json)
combines the independently inspected second [roots](production-host-roots-repeat.md),
[state](production-host-state-repeat.md) and matching
[bootstrap](production-host-bootstrap-repeat.md). The actual gzip and its entire
256,060,514,304-byte expansion pass independent stream verification. This is a
second software construction with documented timestamp, identity and ext4
metadata differences; byte-identical reproducibility and physical deployment
acceptance are not established.

Keep the existing installation and follow the
[one-file machine-id repair](installed-host-repair-20261107.md). Published endpoints
and operator images are unchanged. This candidate uses state UUID
`feaaee34-d782-4c18-bd04-1029540ab26a`; it cannot use the installed `00fd…` or first
candidate `fc8…` bootstrap. No new flashing procedure is released here.

## Reproduction and checks

The actual committed producer was `339eef1ffcd1370dffc65fec81861f7081cb2daf`:

```sh
sudo python3 -I -B \
  /var/lib/a733-nvme-repeat-project-20261107/project/build/launch-nvme-host-repeat.py \
  --project /var/lib/a733-nvme-repeat-project-20261107/project \
  --tag timesync-repeat-01
```

Planning passed first. Completed tags are single-use. The
[registered launcher](nvme-repeat-controller.md) authenticates 633 source files,
Git/capsule provenance, all three parent registrations and consumed artifacts.
The frozen Ubuntu builder keeps inputs read-only and isolates UID/network,
capabilities and firmware access. All 842 host packages remain unchanged;
final mapped-process and phase-mount lists are empty. Only generated files are
written. The partition layout and SPI-ready NVMe BOOT profile remain those in
[the first composition](production-nvme-composition.md).

Producer readback checks complete BOOT/GUESTS namespaces, five regular boot
files, selected root/state metadata, GPT, geometry and all root/state bytes.
The [independent repeat stream reader](../build/verify-nvme-repeat-stream.py),
committed separately at `b3caa81`, then checks the actual compressed wire, gzip
CRC/single-member structure, complete decoded hash, GPT, superblocks and every
root/state partition byte. It stages no expanded image. Namespace inspection
remains producer-attributed. Independent Sol/high artifact and retained-manifest
review approves this file-artifact scope without claiming hardware acceptance. The immutable producer receipt retains its original
pending-independent-expansion field; the later successful stream report supplies
that evidence separately.

Privileged Ubuntu validation passes 1,669 tests without skips, five shell suites,
five assembly cases, 12 Bash parses and five TOML parses. A missing `rg` caused
only the original wrapper's final syntax loop to be skipped; a checked `find`
fallback completed that step separately. The
[controller evidence](evidence/nvme-repeat-stream-controller-20261107.json)
records the original and corrected logs.

## Retained artifacts and capacity

Builder output is under
`/var/lib/a733-clean-replay-20261106/nvme-timesync-repeat-01/work/project/out/nvme-image-timesync-repeat-01`.
The encoded copy is retained on codex under
`/home/drew/orange-pi-4-pro/out/nvme-repeat-candidate-retained-20261107`.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Raw NVMe image, builder only | 256,060,514,304 | `d8f842f806c8ea4257b6efaf45d080b95e42a1f06bbde0a35f07d67c7416c7d9` |
| `nvme.img.gz`, both hosts | 545,633,737 | `4b6cbe6eb16cc3d3cd236b0d8701908408d38e5993d5aca5ffb4882708766df0` |
| Original manifest | 9,393 | `b95ff7047bfcf99809d7d9fd39b0eb4e697a5d185694d89c7dc6e881a2979d60` |
| Derived delivery manifest | 10,481 | `c0f036dcc5d300a24785ebaac4c438bb0aef7cf1b8b073dc97a2d15a7896f563` |
| Independent stream report | 5,322 | `683d48fadbbf7481ccc5d0973923e37640af730f95e75179b81dba3babd7e63d` |

Encoded transfer verifies held-source identity/hash, incoming bytes and fsynced
local readback. It does not recompress or repeat the 256 GB decode. The derived
manifest preserves original fields and adds gzip/distribution provenance.
Small receipts, full readback records and independent review remain under
`out/nvme-timesync-repeat-01-20261107`; generated files stay outside Git.

Initial admission required 23,754,445,683 bytes. Runtime minima were
20,733,865,984 bytes free and 7,548,633,088 bytes available RAM. Inner recipe completion
left 22,269,272,064 bytes free; the outer launcher ended with 22,253,371,392
bytes free after its final records. Encoded retention left 855,748,608
bytes free on codex, above the unchanged 268,435,456-byte reserve. Remeasure before
further work; no guard reduction or additional LV growth is authorized.

Hardware SSH, automatic time, actual KVM execution, boot/write endurance,
interrupted update/rollback, backup/recovery and independently reproduced
installation remain open. The matching NVMe-first firmware transport for this
second identity also remains to be built and physically tested. SPI programming
still requires the original off-device backup, independent recovery evidence,
exact live target/write plan and the agreed yes/no confirmation.

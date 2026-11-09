# Live SPI candidate comparison (2026-11-08)

The reviewed [read-only planner](spi-live-plan.md) ran successfully on the actual
A733 host using the existing retained backup-derived candidate. Both complete
16 MiB reads match the original all-`0xff` baseline SHA-256
`dffab0dd410657cb30c7b2fd7f2586a4792e8472e58882b3532581f8111a646d`.
The live device remains `/dev/mtd0`, `90:0`, XM25QU128C, JEDEC `204118`,
16,777,216 bytes with 65,536-byte erase units and one-byte writes.
The boot ID remained `94851e72-e3b9-4824-88c6-fb82ad7fb610`.

All 256 original/candidate unit hashes match the fixed manifest. The prospective
plan changes units 0–25, **1,703,936 complete-unit bytes**, preserving the
24,576-byte gap and 49,152-byte final-unit tail. Units 26–255 remain unchanged.
These are proposed programming extents, not performed writes. Raw output,
progress and source hash are retained under `out/spi-live-plan-20261108`.
The [evidence record](evidence/spi-live-plan-20261108.json) binds their pins,
source review, regression results and independent inspection of the live result.

The planner and its inputs were staged in private `/run` tmpfs directories.
The reviewed command is in its documentation. It opens MTD read-only and offers
no erase/program operation. All five gates—physical writes, recovery proof,
authorization, hardware acceptance and programmer availability—remain false.
This is an observed point-in-time match. Before any programming, prove independent
recovery and matched NVMe boot-file loading, review a programmer/rollback path,
revalidate the live target and honor the persisted
[known-target authorization](evidence/storage-write-authorization-20261108.json).
Request yes/no only for unapproved targets or scope. No SPI, SD or NVMe
write occurred during this comparison, and it does not establish boot acceptance.

# Build reproduction status

Updated 2026-11-09. This index describes construction evidence. Physical release
acceptance and independent deployment reproduction remain incomplete; see
[implementation status](implementation-status.md).

## Current component chain

| Component | Verified result | Remaining scope |
| --- | --- | --- |
| Pinned userspace | [Account-date/time-profile replay](account-date-replay-driver.md) preserves the 221-package archive and full 15,909-member readback; archive SHA `0ead62…` | Complete independently provisioned builder reproduction remains open |
| Normalized A/B roots | [Two fresh 4 GiB pairs](deterministic-host-roots.md) match all raw/gzip bytes, all four full filesystem readbacks and maintenance markers | Same qualified builder; physical corrected-root policy verification is pending |
| Matched STATE | [Full 8 GiB construction/readback](public-config-state.md), SHA `2e9d3d…`, selects normalized A marker `286494…` and preserves UUID `360dfb39-f49f-487e-b307-0bb887c854c5` | This is an unpersonalized component, not a backup of the running board |
| Protected boot pair | [Protected SD/NVMe BOOT evidence](evidence/protected-state-maintenance-20261109.json); 256 MiB SD SHA `ab5e…`, NVMe BOOT SHA `cc2a…`; actual protected host boot passes | Firmware loading Linux files from NVMe and SPI/SD-removed boot remain unperformed |
| Normalized full NVMe | [Two fresh-checkout streams](evidence/normalized-full-nvme-image-20261109.json) match all raw/gzip bytes; complete independent hashes, framing/EOF, GPT and replacement inclusion pass | Fresh installation and independent deployment reproduction remain pending |
| Matched maintenance | [Normalized candidates](normalized-maintenance-20261109.md) pass full source/copy comparison and offline old/new API checks; old A rollback pair is retained separately | Live guards, interruption, activation and manual rollback remain physical gates |
| SPI preparation | [Caller/capture](known-spi-programmer.md) and underlying engine/backend pass independent source review and the privileged Ubuntu suite | Recovery proof is unregistered; write admission is disabled before device opening |

The selected full gzip is **545,581,074 bytes**, SHA-256
`3bc02f46d272dcac591d435fff2b1bc98d4fb048569fb2120746145929a8ad64`.
Its logical size is **256,060,514,304 bytes**, SHA-256
`1fa9144d7a1269066c91a94bfb105a9ac4e4083d4707dd6791e8ac2f26902cd9`.
Both 118,399-byte manifests retain separate timing/resource measurements.
The native selected artifact is `out/normalized-nvme-candidate-20261109/`.
It is unbooted and must be personalized for this same board before use.

## What repetition proves

Source `81f907481b12e9639ad23752966e05166074f96f` was checked out into two
fresh builder directories. The normalized profile authenticates fixed inputs,
including the protected `675d6b…` base, normalized roots and matched STATE.
Both full streams match exactly. Independent verification decodes both new
streams completely and directly compares every new raw/gzip byte. Complete
unchanged-region hashes match the authenticated earlier direct old/new proof;
the old 256 GB stream was not decoded again in this review.

This establishes repeatability on one qualified Ubuntu 22.04 builder with those
fixed component inputs. It does not establish an independently provisioned
builder, a fresh rebuild of every toolchain component or a second operator's
physical installation. Fresh machine identity is intentionally installation
specific; the same-board personalization step preserves this board's private
identity without changing the selected root pairing.

Latest complete privileged validation is **2,264 Python tests**, zero skips,
errors or failures, plus five shell suites and five diagnostic assembly cases.
Five TOML files parse separately on codex. [Retained suite and isolated-composer preparation](evidence/isolated-nvme-composition-preparation-20261109.json)
record the source pins and initial validation-environment failures. These are
software checks.

## Resources and unresolved inputs

Use the [prepared Ubuntu builder](build-host-assessment.md), retain initial and
runtime resource guards, and begin compilation with two jobs. All free builder
capacity is approved for use/expansion after exact target revalidation and review.
Native evidence storage has a separate unchanged 256 MiB floor and reserved
outcome budget; keep bulk source/guest data on the builder when needed.

The [exact compiler-source inventory](toolchain-sources.md) still lacks GCC and
DejaGNU copies. [Twenty-six vendor boot-input grants](evidence/vendor-boot-license-inventory-20261106.json)
remain unresolved; public binary redistribution clearance is not claimed.

Historical construction failures and superseded artifacts remain in their linked
evidence and Git history: [cross-day account dates](userspace-account-date-replay.md),
[earlier production chain](production-nvme-composition.md),
[earlier repeat differences](production-nvme-repeat.md),
[protected full-image predecessor](evidence/protected-full-nvme-image-20261109.json)
and [resolver-readable predecessor](evidence/public-config-nvme-image-20261109.json).
Their old pending-status statements describe those checkpoints, not current gates.

The [isolated final-composer launcher](isolated-nvme-composition.md) now passes
independent review and the full suite. It does not change the producer or held
components; its actual namespace execution remains pending.

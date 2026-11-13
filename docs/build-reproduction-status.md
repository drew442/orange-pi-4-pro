# Build reproduction status

Updated 2026-11-13. This index describes construction evidence.
[Independent physical deployment reproduction](independent-deployment-20261110.md)
and [release acceptance on the protected-SD route](release-acceptance.md) pass. See
[implementation status](implementation-status.md).

## Current component chain

| Component | Verified result | Remaining scope |
| --- | --- | --- |
| Pinned userspace | [Account-date/time-profile replay](account-date-replay-driver.md) preserves the 221-package archive and full 15,909-member readback; archive SHA `0ead62…` | Complete independently provisioned builder reproduction remains open |
| Normalized A/B roots | [Two fresh 4 GiB pairs](deterministic-host-roots.md) match all raw/gzip bytes, all four full filesystem readbacks and maintenance markers | Same qualified builder; corrected policy passes on both normalized B and fresh A ([physical evidence](implementation-status.md#acceptance-evidence)) |
| Managed A/B roots | [Two fresh matching 4 GiB A/B constructions](managed-root-candidate-20261112.md) from frozen source `2996365` pass complete artifact/filesystem review and historical/current production-reader checks | Uninstalled and unbooted. SPI recovery/reinstall, B deployment, managed KVM, rollback, final SPI counts and revised SPI procedure reproduction remain pending; see [production plan](spi-production-readiness-plan.md) |
| Matched STATE | [Full 8 GiB construction/readback](public-config-state.md), SHA `2e9d3d…`, selects normalized A marker `286494…` and preserves UUID `360dfb39-f49f-487e-b307-0bb887c854c5` | This is an unpersonalized component, not a backup of the running board |
| Protected boot pair | [Protected SD/NVMe BOOT evidence](evidence/protected-state-maintenance-20261109.json); 256 MiB SD SHA `ab5e…`, NVMe BOOT SHA `cc2a…`; actual protected host boot passes | SPI-to-NVMe boot with SD removed is physically verified ([operational evidence](evidence/spi-operational-20261111.json)); final SPI counts and revised recovery/reinstall remain pending |
| Normalized full NVMe | [Two fresh-checkout streams](evidence/normalized-full-nvme-image-20261109.json) match all raw/gzip bytes; complete independent hashes, framing/EOF, GPT and replacement inclusion pass | [Isolated serialization also passes](evidence/isolated-nvme-composition-20261109.json); [fresh installation and independent deployment reproduction pass](independent-deployment-20261110.md) |
| Matched maintenance | [Normalized candidates](normalized-maintenance-20261109.md) pass full source/copy comparison and offline old/new API checks; old A rollback pair is retained separately | [Live guards, interruption, activation and manual rollback pass physically](physical-host-update-rollback-20261110.md) |
| SPI preparation | [Caller/capture](known-spi-programmer.md), underlying engine/backend and metadata-aware plans pass independent review and software validation | SPI/NVMe SD-removed cold and warm boots plus actual manual KVM are physically verified ([operational evidence](evidence/spi-operational-20261111.json)). Revised physical restore/recovery/reinstall demonstration and fresh reviewed live plans remain pending ([metadata validation](evidence/spi-metadata-validation-20261112.json)) |

The selected full gzip is **545,581,074 bytes**, SHA-256
`3bc02f46d272dcac591d435fff2b1bc98d4fb048569fb2120746145929a8ad64`.
Its logical size is **256,060,514,304 bytes**, SHA-256
`1fa9144d7a1269066c91a94bfb105a9ac4e4083d4707dd6791e8ac2f26902cd9`.
Both 118,399-byte manifests retain separate timing/resource measurements.
The native selected artifact is `out/normalized-nvme-candidate-20261109/`.
The distribution was installed and personalized on this board; normalized A and
restored KVM/checkpoint/restart pass. The factory artifact still requires
same-board personalization on any later full installation.

## What repetition proves

Source `81f907481b12e9639ad23752966e05166074f96f` was checked out into two
fresh builder directories. The normalized profile authenticates fixed inputs,
including the protected `675d6b…` base, normalized roots and matched STATE.
Both full streams match exactly. Independent verification decodes both new
streams completely and directly compares every new raw/gzip byte. Complete
unchanged-region hashes match the authenticated earlier direct old/new proof;
the old 256 GB stream was not decoded again in this review.

This establishes repeatability on one qualified Ubuntu 22.04 builder with those
fixed component inputs. The newer managed A/B pair likewise establishes fresh
construction and repeated artifact/reader checks on that same qualified builder;
neither result establishes independently provisioned-builder reproduction or a
fresh rebuild of every toolchain component. The separate
[physical reproduction record](independent-deployment-20261110.md) establishes
that later installation. Fresh machine identity is intentionally installation
specific; the same-board personalization step preserves this board's private
identity without changing the selected root pairing.

Latest complete privileged validation is **3,651 Python tests**, zero skips,
errors or failures, plus five shell suites and five diagnostic assembly cases.
Five TOML files parse separately on codex. The
[SPI collector integration validation](evidence/spi-collector-integration-validation-20261112.json)
records the latest 3,651-test run and actual terminal results. The older
[cold-collector validation](evidence/cold-boot-collector-validation-20261110.json)
is historical evidence. Earlier
[isolated-composer preparation](evidence/isolated-nvme-composition-preparation-20261109.json)
retains its corrected validation-environment failures. These are software checks.

## Resources and unresolved inputs

Use the [prepared Ubuntu builder](build-host-assessment.md), retain initial and
runtime resource guards, and begin compilation with two jobs. All free builder
capacity is approved for use/expansion after exact target revalidation and review.
Native evidence storage has a separate unchanged 256 MiB floor and reserved
outcome budget; keep bulk source/guest data on the builder when needed.

The [outer-host supplement](outer-builder-package-supplement.md) retains the exact
seven updated binaries and full signed metadata with independently verified
second copies. [Independent review](evidence/builder-outer-supplement-independent-review-20261113.json)
passes. This closes that acquisition gap; it does not rehash the other 835 old
archives or demonstrate a freshly provisioned native outer builder.

The [exact compiler-source inventory](toolchain-sources.md) still lacks the exact
GCC and DejaGNU source inputs. [Twenty-six vendor boot-input
grants](evidence/vendor-boot-license-inventory-20261106.json) remain unresolved;
public binary redistribution clearance is not claimed.

Historical construction failures and superseded artifacts remain in their linked
evidence and Git history: [cross-day account dates](userspace-account-date-replay.md),
[earlier production chain](production-nvme-composition.md),
[earlier repeat differences](production-nvme-repeat.md),
[protected full-image predecessor](evidence/protected-full-nvme-image-20261109.json)
and [resolver-readable predecessor](evidence/public-config-nvme-image-20261109.json).
Their old pending-status statements describe those checkpoints, not current gates.

The [isolated final-composer execution](isolated-nvme-composition.md) now passes
independent review, with exact encoded identity to the canonical target and
unchanged frozen/source/host state. It does not change the producer or held
components and does not establish a new OS/kernel/toolchain rebuild.

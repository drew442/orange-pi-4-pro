# U-Boot challenge and exact-artifact reproduction

The current deployment candidate remains pinned and unchanged. This investigation
addresses a separate release gate: reproducing an existing U-Boot/package from
its recorded build inputs. No new firmware has been flashed, and no replay
build result is claimed here yet.

## Pinned source facts

U-Boot commit `b791be842935b27268ae3d00e943a9075495f30a` generates 128 random
bytes from `/dev/urandom` in `Makefile:1579-1586`, converts them with `xxd`, and
writes `board/sunxi/sunxi_challenge.c` during preparation. The board Makefile
always links `sunxi_challenge.o`. Fresh builds therefore differ even when their
other inputs match. [Existing provenance](evidence/diagnostic-provenance-20261104.json)
retains the original generated C, unpacked binary, configuration, build log and
hashes instead of normalizing the random field.

`arch/arm/cpu/armv7/spare_head.c:70` exports the challenge address through the
firmware header. `include/private_uboot.h:81-89` describes a security-check field
used with a salt. Its downstream opaque-firmware consumer remains unestablished.
The visible `board/sunxi/sunxi_mips.c:172-208` consumer salts the challenge and
uses certificate/hash verification for a USB debug path; its object is guarded
by `CONFIG_SUNXI_MIPS`, disabled in the retained A733 configuration. That does
not establish that the exported header field is unused.

Source references:

- [Pinned generator](https://github.com/orangepi-xunlong/u-boot-orangepi/blob/b791be842935b27268ae3d00e943a9075495f30a/Makefile#L1579)
- [Header export](https://github.com/orangepi-xunlong/u-boot-orangepi/blob/b791be842935b27268ae3d00e943a9075495f30a/arch/arm/cpu/armv7/spare_head.c#L70)
- [Salted USB verifier caller](https://github.com/orangepi-xunlong/u-boot-orangepi/blob/b791be842935b27268ae3d00e943a9075495f30a/board/sunxi/sunxi_mips.c#L172)
- [Certificate/hash verification](https://github.com/orangepi-xunlong/u-boot-orangepi/blob/b791be842935b27268ae3d00e943a9075495f30a/board/sunxi/sunxi_image_verifier.c#L260)

## Reviewed replay boundary

Exact-artifact replay may supply the **same retained challenge** as an explicit,
authenticated build input for that existing artifact. This preserves its bytes;
it does not disable authentication, substitute zeros, or select one constant
challenge for every future release. Fresh-build vendor randomness remains the
default. No conclusion about the complete firmware security role is implied.

The separate `build/uboot-replay.py` recipe is implemented and independently
reviewed, with 22 guard regressions. Actual reproduction builds remain pending.
It authenticates retained provenance against committed
trusted evidence, parse the C as canonical data with exactly 128 bytes, and
compile only a private validated copy. Its generator patch applies only inside
a fresh private source tree, fails on unknown context, and preserves the original
random-generation branch. Version suffix, dirty state and timestamp are explicit
inputs; the existing target records `-gb791be8-dirty` and epoch `1772630733`.

Two fresh private trees must compile sequentially with two jobs each. Both their
unpacked binaries and packaged boot files must match the original target's
trusted hashes, not merely each other. A mismatch remains a failed reproduction;
it must not be accepted by changing the target record. Preserve patches, input
copies/hashes, compiler/package inventories and logs. Exact log identity is not
expected.

Even successful replay using the prepared builder, source/download caches and
existing kernel output would establish only that U-Boot/package replay. It
would not establish a fresh builder image, clean package/source acquisition,
complete firmware licensing, physical deployment reproduction or actual KVM
execution. Those remain separate release requirements.

## Source and license inventory

The [boot-input inventory](evidence/vendor-boot-license-inventory-20261106.json)
accounts for all 26 pinned paths and rechecks their hashes. It records nearby
repository/license/provenance evidence and missing component-specific records.
The vendor root's GPL text is not treated as proof of attribution or a source
offer for every opaque input. It makes no legal conclusion. Required source,
license notices and redistribution terms still need their release audit.

## Prepared-builder package drift

The first invocation refused before creating build output because five package
versions differed from the original full inventory. The compiler and target
hashes were unchanged. The [profile record](evidence/builder-replay-profile-20261106.json)
retains both complete inventory hashes and the exact APT/CA/Twisted differences.
The original `configs/builder-packages.lock.tsv` remains immutable. A separate
versioned replay profile records all 842 actual packages; it is explicitly
selected and must match in full before and after a build. There is no automatic
fallback or package-version exception. Original target binary/package hashes
remain the reproduction criterion under the newly recorded environment.
This is still the prepared builder, not a newly acquired clean builder image.

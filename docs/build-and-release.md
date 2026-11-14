# Build and package a beta release

This workflow is implemented in the overhaul PR, including packaging. It is also
the continuing release workflow: change authenticated inputs and reuse applicable
evidence instead of editing historical recipe pins for every new image.

A package is not a physical acceptance result.
[Current status](implementation-status.md) identifies actual hardware work and acceptance.
No existing image is relabeled as the new manual profile.

## 1. Freeze the source and reuse the qualified builder

Use a clean committed checkout and the existing qualified Ubuntu 22.04 AMD64
builder/frozen namespace. The current launchers retain their package inventory,
source snapshot, isolation, free-space and cleanup checks. Keep compatible verified
userspace, kernel/diagnostic inputs and previous recovery artifacts. Do not rebuild
the kernel/toolchain, re-provision another machine, or construct twice solely for
beta. Run [affected software checks](validation-policy.md) before construction.

The fixed-builder prerequisites and retained inputs remain in
[clean builder](clean-builder.md), [userspace profiles](userspace-profiles.md), and
[the historical construction index](build-reproduction-status.md). These are
input references, not instructions to repeat every historical phase. The
launcher `--plan` performs preflight; an existing tag is not reusable.

## 2. Construct the current manual roots once

On the qualified builder, set real absolute input paths and a fresh tag:

```sh
: "${A733_REPO:?Set absolute clean committed checkout}"
: "${A733_ROOT_TAG:?Set a new unique build tag}"
: "${A733_DIAGNOSTIC_DIR:?Set authenticated diagnostic input directory}"
: "${A733_ROOTFS_TAR:?Set authenticated compatible userspace archive}"
sudo -n python3 -I -B "$A733_REPO/build/launch-deterministic-host-roots.py" \
  --project "$A733_REPO" --tag "$A733_ROOT_TAG" \
  --diagnostic "$A733_DIAGNOSTIC_DIR" --userspace "$A733_ROOTFS_TAR" \
  --guest-management-profile manual-beta --plan
```

After the concrete plan's existing prerequisites pass, use the same command
without `--plan`. The maintained wrapper passes `manual-beta` to the **current**
payload builder and verifies the resulting profile. It retains the frozen
launcher's isolation/fakeroot/normalization machinery; historical replay bodies
and their pins stay unchanged. `managed-experimental` is an explicit development
choice and is ineligible for the beta packager.

Retain the actual source-manifest, root manifest, construction receipt and artifact
verification. The immutable payload and root manifest must explicitly contain
`guest_management_profile: manual-beta`. Do not patch the live immutable root or
assemble a mixture of old/new tool trees. Root A/B partition images are not
whole-drive images.

## 3. Compose matching NVMe installation data

Use [the composition input example](../configs/beta-composition.example.json) to
create an ignored, protected input configuration with actual paths and trusted
pins. The maintained isolated compositor's `--input-config` mode accepts current
roots and either a verified compatible STATE image or `state_build` inputs for
current userspace, management configuration and administrator public keys.

For `state_build`, the existing root selector and STATE builder run in the same
frozen builder context. They bind STATE to the new root marker and generate fresh
machine identity. The compositor preserves the proven base's BOOT, VGIC device
tree, kernel, initramfs, SPI/NVMe command and GPT while streaming the new root A,
root B and STATE components. It does not expand a second full NVMe staging image.

```sh
: "${A733_COMPOSITION_CONFIG:?Set absolute authenticated input configuration}"
: "${A733_COMPOSITION_TAG:?Set a new unique composition tag}"
sudo -n python3 -I -B "$A733_REPO/build/launch-isolated-nvme-composition.py" \
  --project "$A733_REPO" --tag "$A733_COMPOSITION_TAG" \
  --input-config "$A733_COMPOSITION_CONFIG" --plan
```

Execute the same command with `--execute` in place of `--plan`. Preserve the
actual output manifest, compressed NVMe image, retained STATE outputs and receipt.
Use a fresh bytecode-free checkout for this launcher after running tests; it
refuses ignored `build/` or `configs/` Python caches before construction. Retain
validation receipts and use the same committed source; no builder reprovisioning
or software retest is implied. The phase directory is
`/var/lib/a733-clean-replay-20261106/isolated-nvme-$A733_COMPOSITION_TAG/`.
Its `receipt.json` records construction; `output/public-config-nvme-image-isolated-$A733_COMPOSITION_TAG/`
contains `manifest.json` and `nvme.img.gz`. Generated STATE and selector outputs
are retained alongside that image directory under `output/`.
A failed plan/construction remains a failure; do not edit its result to satisfy
packaging. The original fixed-input mode remains available for historical replay,
but is not the current beta image builder.

**Identity constraint:** retained BOOT resolves the recorded STATE UUID. This
workflow keeps that UUID/layout and checks the actual new selector. It does not
claim arbitrary UUID/layout or another board model support. Another identical
Orange Pi 4 Pro unit needs its own MAC, configuration, machine/SSH identity and
physical target plan; it must not clone an already personalized STATE or recovery
image. Changing the BOOT-bound UUID requires matching BOOT reconstruction, not a
text substitution in a manifest. [Installation](install-and-recovery.md).

## 4. Record finite acceptance and reuse

Use the [beta gates](beta-scope.md) and relevant dependency decisions in
[validation policy](validation-policy.md). Retain the source build revision
separately from the release source revision; a documentation-only source advance
can reuse identical packaged runtime. A completed candidate construction is not
an automatic deployment or guest pass.

The packager plans these gates: software, construction, SPI boots, manual KVM
lifecycle and recovery. It does not add another soak or general upstream tests.
Populate pinned receipts/raw observations using actual results. Retained SPI
observations count toward three cold plus three warm for compatible boot
components; at least one manual-profile observation must match the planned
current runtime fingerprint. The manual lifecycle includes restart/persistence;
recovery includes SPI recovery, host rollback and stopped-guest backup/restore.

A reused record needs an explicit applicability assessment. `hardware_compatibility`
describes the actual compatible board/component scope, and `manual_lifecycle_context`
pins the guest fixture and storage plan. Old SD-route boots cannot be promoted to
SPI observations. Missing results remain missing. The JSON schema performs
consistency checks; it cannot independently witness a physical event.

## 5. Stage the release package

Copy [the package example](../configs/beta-package.example.json) to ignored
`out/package-spec.json`, replacing every placeholder with actual authenticated
records. Paths resolve relative to the spec directory; absolute paths avoid
ambiguity. Assets are supplied by their existing producer manifests and JSON
pointers to size/hash records. NVMe composition must bind the exact current root
manifest and A/B payloads; the packager refuses an old managed image accompanied
by unrelated manual-profile metadata.

```sh
python3 scripts/package-beta.py --spec out/package-spec.json --plan

# Coherent package before physical acceptance, always marked candidate.
python3 scripts/package-beta.py --spec out/package-spec.json \
  --candidate --output out/private-beta-candidate

# Accepted release package: every required gate must actually pass or be reused.
python3 scripts/package-beta.py --spec out/package-spec.json \
  --output out/private-beta-release
```

Use a new output directory with an existing parent. Source must be clean and
committed; operator inputs and logs belong under ignored `out/`. A plan validates
metadata and sizes and reports required fingerprints; it deliberately does not
rehash all large images. Assembly streams/copies each supplied compressed asset
once with size/hash checks. It does not rebuild, decompress all disks, write a
device, create a GitHub release or upload anything.

Outputs include assets, `release-manifest.json`, `SHA256SUMS`, release notes,
content-addressed producer/evidence/source records, the exact Git source archive,
operator documentation and notices. Checksum files establish byte identity; they
are not signatures or a substitute for a trusted distribution channel. Extract
the source archive for complete build/configuration/script references.

## 6. Private and public distribution

Choose visibility explicitly. The current personalized recovery SD contains the
original unit's private SSH host key, and some vendor redistribution grants remain
unresolved. Keep such assets private. Per-unit data stay per unit even if a binary
is technically bootable elsewhere.

Public packaging requires hash-bound eligibility assessments for the actual
assets, source and bundled metadata, with referenced grants and absence of private
identity/personalization. The metadata assessment also binds emitted declarations.
These are operator assertions, not automatic legal clearance or secret scanning.
Do not set flags merely to get a green result. Review only what will actually be
published; reconstructing every historical compiler component is not a functional
beta gate. [Known issues](known-issues.md) retains current provenance limitations.

A PR changes source, defaults, tests and packaging machinery. It does not publish
private images or claim an unperformed physical release. After the actual finite
gates pass, publish the eligible package and release notes through the chosen
release channel; no follow-up packaging implementation PR is intended.

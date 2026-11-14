> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Registered runtime-fixes NVMe controller

`build/launch-nvme-host-runtime-fixes.py` composes the accepted November 8
corrected roots, fresh STATE and matching bootstrap as regular files. It keeps
the historical NVMe launcher unchanged and accepts its `--project`, `--tag` and
`--plan` CLI, under `python3 -I -B`.

The three component registrations are:

| Component | Committed evidence | Producer source | Phase suffix |
| --- | --- | --- | --- |
| Roots | [original root artifact record](../../evidence/production-host-roots-runtime-fixes-20261108.json) | `f40c151` | `host-roots-timesync-runtime-fixes-01` |
| STATE | [accepted STATE](../../evidence/production-host-state-runtime-fixes-20261108.json) | `6dbf6d9` | `host-state-timesync-runtime-fixes-02` |
| Bootstrap | [accepted bootstrap](../../evidence/production-host-bootstrap-runtime-fixes-20261108.json) | `0a3d2a1` | `host-bootstrap-timesync-runtime-fixes-01` |

All phases are beneath `/var/lib/a733-clean-replay-20261106`; payloads retain
their registered paths beneath each phase's `work/project/out`. The STATE UUID
is `360dfb39-f49f-487e-b307-0bb887c854c5`, distinct from the installed `00fd…`
state and prior candidates. Never deploy its bootstrap alone against the
current installation.

## Authentication and the addressing exception

The adapter authenticates the SHA-pinned historical launcher, then replaces
only exact counted AST string constants for the launcher paths, evidence names,
registered phase/output names and UUID. Historical predicates and execution
statements remain intact. It holds every template and physical ancestor through
nested bootstrap/STATE adapters, preserving their original final checks before
publication. Bootstrap source SHA256 is
`de01fe092e5262d8433e7b2ca4c1f56f740ac840467b2b98f41d4762fef8384e`.

All three records have independent raw size/SHA256, canonical JSON SHA256 and
Git blob pins. The nested STATE registration independently authenticates the
[full root proof binding](../../evidence/production-host-roots-runtime-fixes-state-binding-20261108.json),
including each slot's expected tree, stability proof and namespace count. The
original root artifact record retains its original shorter `expected_trees`
shape. The adapter checks that both registrations describe the same root
producer, policy, images, manifest and complete readback. The entire registered
bootstrap controller family must match its source-derived binding. A
`runtime_fixes_controller` report records provenance, including the separately
pinned root readback binding.

One addressing exception is necessary: historical `records()` expects
`root.readback.builder_output` and `root.readback.receipt`, fields absent from
the accepted original root record. The separate
[readback address binding](../../evidence/production-host-roots-runtime-fixes-nvme-readback-binding-20261108.json)
registers that existing reader output, its exact receipt pin and complete parsed
receipt. Its raw/canonical/Git pins are independently checked. The receipt
contents must equal the original root record's complete readback and agree with
the registered producer receipt and manifest. `records()` receives a deep copy
of the policy with only those two addressing fields added. The returned policy,
producer evidence and historical receipt comparison remain unchanged. No proof
is flattened, relabeled or rewritten.

## Checks and limits

Source-only regressions exercise the actual committed registrations, nested
source policy and complete tree binding; counted AST changes; source, path,
UUID and target rejection; readback addressing without policy mutation; held
nested final checks; and admission/resource floors. Run:

```sh
python3 -m unittest discover -s tests -p test_nvme_runtime_fixes_controller.py
```

The explicit additional native integration check requires the ignored retained
metadata from the accepted root, STATE and bootstrap builds. It opens and hashes
all nine real metadata files with the existing held-input implementation,
authenticates their actual field shapes through the unchanged historical
functions, and checks rejection of modified proofs:

```sh
python3 tests/test_nvme_runtime_fixes_controller.py --native
```

This check fails when required native inputs are unavailable; ordinary unit
discovery does not depend on ignored build products. It does not reread the
large root/STATE image bytes; full held-input authentication on the builder
remains a separate coordinator gate.

The registration itself launched no composition. After [verified seed retention](../../component-retention-20261108.md),
full admission planning was repeated and [construction and independent full
stream verification](../../production-nvme-runtime-fixes.md) now pass for tag
`timesync-runtime-fixes-01` from the original `6c34629` producer project.
The last observed builder free space before phase creation was **23,814,090,752
bytes**, above the unchanged **23,760,999,827-byte** total admission. Runtime floors remain
3,000,000,000 free bytes and 1,000,000,000 available memory bytes, with a 10,800
second timeout and process descriptor soft limit of at least 4,096. The exact
SPCC target remains 256,060,514,304 bytes with 512/512-byte sectors.

Host checks establish no physical installation or acceptance. Current installed
NVMe remains untouched; the separate reviewed VGIC SD boot awaits operator
approval. Automatic time, complete KVM guest networking/restart, zero routine SD
writes, matching NVMe-first firmware, SPI recovery and remaining hardware gates
are unchanged. See [implementation status](../../implementation-status.md).

## Actual committed-source readiness

[Readiness evidence](../../evidence/nvme-runtime-fixes-readiness-20261108.json) records
source `6c34629c424a1bb414780b26461e06ccd2854ae9`, all **696** tracked files,
independent source review and a passing committed CLI plan:

```sh
sudo python3 -I -B \
  /var/lib/a733-nvme-runtime-fixes-project-20261108-01/project/build/launch-nvme-host-runtime-fixes.py \
  --project /var/lib/a733-nvme-runtime-fixes-project-20261108-01/project \
  --tag timesync-runtime-fixes-01 --plan
```

The separate read-only integration runner held and authenticated **144 actual
files, 17,861,946,198 bytes**, including both complete 4 GiB raw roots, 8 GiB
state, 256 MiB SD and every manifest-listed artifact. The unchanged historical
component authentication passed with real held descriptors; source and input
identities remained stable. This extends the earlier native metadata check to
the actual large payloads. It does not construct or inspect a new NVMe image.

The readiness run passed **1,746 privileged unit tests without skips**, five
shell suites and five assembly cases. The later independent reader validation
passes **1,777 tests** with no skips. The replay lock matches all 842 host
packages; audit is empty. Completed construction has separate final idle and
inventory evidence in its immutable receipt. The readiness record itself is
preconstruction; its absent phase and unused tag are historical observations.
The tag is now consumed and must not be reused.

Exact initial admission was **23,760,999,827 bytes**, including the unchanged
23,622,320,128-byte recipe guard. The first input-authentication sample was
4,901,614,995 bytes short. Subsequent [component and seed retention](../../component-retention-20261108.md)
completed with protected postchecks, preserving recoverable backups, active
builder, seed probe/mask and corrected components. A fresh admission then passed.
[Native archive retention](../../native-archive-retention-20261108.md) separately made
room for the actual compressed copy above its 256 MiB floor. The [completed
construction record](../../production-nvme-runtime-fixes.md) reports actual minima,
final resource samples and retained artifact pins. No guard was reduced and no
additional LV growth occurred. Raw logs remain in
`out/nvme-runtime-fixes-20261108`; no physical storage was written.

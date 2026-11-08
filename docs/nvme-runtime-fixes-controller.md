# Registered runtime-fixes NVMe controller

`build/launch-nvme-host-runtime-fixes.py` composes the accepted November 8
corrected roots, fresh STATE and matching bootstrap as regular files. It keeps
the historical NVMe launcher unchanged and accepts its `--project`, `--tag` and
`--plan` CLI, under `python3 -I -B`.

The three component registrations are:

| Component | Committed evidence | Producer source | Phase suffix |
| --- | --- | --- | --- |
| Roots | [original root artifact record](evidence/production-host-roots-runtime-fixes-20261108.json) | `f40c151` | `host-roots-timesync-runtime-fixes-01` |
| STATE | [accepted STATE](evidence/production-host-state-runtime-fixes-20261108.json) | `6dbf6d9` | `host-state-timesync-runtime-fixes-02` |
| Bootstrap | [accepted bootstrap](evidence/production-host-bootstrap-runtime-fixes-20261108.json) | `0a3d2a1` | `host-bootstrap-timesync-runtime-fixes-01` |

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
[full root proof binding](evidence/production-host-roots-runtime-fixes-state-binding-20261108.json),
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
[readback address binding](evidence/production-host-roots-runtime-fixes-nvme-readback-binding-20261108.json)
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

No composition was launched by this registration work. The last recorded
builder free space is **18,874,372,096 bytes**, below the unchanged **22 GiB**
initial recipe guard, before metadata capsule and Git staging overhead. Fresh
verified capacity is required before construction. Runtime floors remain
3,000,000,000 free bytes and 1,000,000,000 available memory bytes, with a 10,800
second timeout and process descriptor soft limit of at least 4,096. The exact
SPCC target remains 256,060,514,304 bytes with 512/512-byte sectors.

Host checks establish no physical installation or acceptance. Current installed
NVMe remains untouched; the separate reviewed VGIC SD boot awaits operator
approval. Automatic time, complete KVM guest networking/restart, zero routine SD
writes, matching NVMe-first firmware, SPI recovery and remaining hardware gates
are unchanged. See [implementation status](implementation-status.md).

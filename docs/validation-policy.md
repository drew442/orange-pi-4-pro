# Validation and evidence reuse

## Principle

Use upstream Linux/KVM/QEMU testing as the baseline for unchanged components.
Validate this project's boot integration, generated configuration and custom
storage/lifecycle code. Keep inexpensive useful regressions; successful scripts
should require one compact result, not repeated model reasoning. See the finite
[beta gates](beta-scope.md). There is no new soak, upstream conformance campaign,
second independent construction or managed ten-plus-ten boot series.

## Software runner

The standard-library runner auto-discovers all Python `test_*.py` modules, the
retained diagnostic Python suite and shell `test-*.sh` programs. The small
[policy registry](../configs/validation-policy.json) declares conservative domain
dependencies; new unclassified tests cannot disappear from coverage.

```sh
# BASE is the merge base / accepted baseline for this change.
python3 scripts/validate-project.py --base "$BASE" --plan
python3 scripts/validate-project.py --base "$BASE" --scope ci \
  --output out/validation/change

# One full unprivileged software check, useful for a broad candidate change.
python3 scripts/validate-project.py --mode full --scope ci \
  --reuse out/validation/change --output out/validation/full-ci

# Required native checks on the qualified controlled builder, when needed.
python3 scripts/validate-project.py --mode beta --scope native \
  --reuse out/validation/prior-native --output out/validation/beta
```

Use a new output directory per run. `--changed relative/path` is repeatable for an
explicit focused plan. A plan is not a pass. `summary.json` and `summary.txt`
contain selected stage IDs, outcomes, counts and log references. Inspect them once.
Raw stdout/stderr and receipts stay in the output directory. On failure inspect
the bounded error and relevant log, correct the cause, then select the affected
stage or paths. Do not rerun unrelated successes to refresh a commit label.

`ci` is explicitly unprivileged. Declared missing root/native/tool fixtures are
reported as exclusions; a fully excluded stage is `NOT_APPLICABLE`, and no
applicable tests produces `NO_APPLICABLE_TESTS`, not a blanket pass. Unknown
skips, command failures, timeouts, missing worker results and empty test stages
cannot pass. `beta` requires native scope and blocks unavailable required cases.
CI does not run untrusted PR code as root on the board or a privileged builder.
Physical gates always remain `NOT_EVALUATED` by this software runner.
The CI job names Ubuntu 24.04 explicitly; distro upgrades are deliberate changes,
not an implicit `ubuntu-latest` transition. A check requiring the qualified
builder's pinned native tool version is explicitly excluded on other versions,
and remains required in native beta scope.

Python counts distinguish collected, run, executed and skipped/excluded cases.
A shell suite counts as one assertion program, not an invented number of internal
assertions. The per-stage timeout is finite. Existing experimental tests remain
available; shared failures are never discarded based on a `managed` filename.
Until isolation is established, full/beta conservatively includes them. Do not
add new experimental qualification requirements to the release.

## Reuse contract

A software receipt is reusable only when its stage definition, selected input
bytes/membership, test inputs, runner/policy, environment, scope, successful
counts and retained logs match. Reuse is displayed as `REUSED` with the original
receipt reference. Missing, failed, stale, incomplete or corrupt receipts rerun
only the applicable stage. Test fixture edits conservatively invalidate dependent
coverage; unknown runtime changes fall back to broad coverage. Documentation-only
changes do not run host tests unless a test consumes that document as a fixture.

Whole-source provenance remains in build records. It is not the test applicability
key. Root image, package or firmware changes must be mapped to the checks that
actually exercise them; a narrative edit does not invalidate accepted KVM or SPI
operation. Keep reviewed success records instead of asking another agent to
reconstruct acceptance. A repaired collector can reinterpret sufficient retained
raw observations, but cannot manufacture an unobserved hardware result.

## Physical evidence

For each reused gate, record the original receipt/raw observations, supported
configuration, validated relevant dependencies, current relevant dependencies,
and an explicit applicability decision. Use the release packager's evidence
schema to pin that assessment. Preserve original failures and scope limitations.

| Gate | Relevant dependencies |
| --- | --- |
| SPI boot | Board/component compatibility, SPI firmware/calibration policy, NVMe BOOT files, DT/kernel/config, root startup behavior and manual profile |
| KVM/manual lifecycle | Relevant boot/kernel and QEMU package, launch/account/admission/unit configuration, guest fixture and storage/checkpoint behavior |
| SPI recovery | Programmer/planner, flash geometry/device identity, calibration handling, recovery firmware and documented restoration procedure |
| Host/guest recovery | Writer/selector/backup/restore code, layout, compatibility contract and source/destination artifact identity |

Three cold plus three warm SPI observations are a **total**, not a new campaign
for every root or documentation edit. At least one must cover the final manual
profile. One complete applicable manual lifecycle suffices; do not repeat it on
every boot. Existing long-run observations supersede any desire to start another
soak. Only a material new risk justifies a short targeted check, normally within
30 minutes and with an explicit stop condition.

## Cheap preflight and stop rules

Validate syntax, TOML, links, selected unit rendering and dependency availability
before expensive construction. Reuse unchanged downloaded sources and verified
build products. Use the current root launcher's `--plan` to check qualified
builder inputs/capacity before its one construction. Do not retire artifacts,
rehash every old image or re-provision a builder simply to resume a session.

A failed normal supported operation, data corruption, wrong-target write,
serious privilege failure or loss of recovery blocks release. A minor unsupported
edge is documented. Before expanding work, apply [scope admission](roadmap.md):
promise/defect, observation, smallest action including cost, finite stop condition.

# Future work and scope admission

## Before beta

Only the explicit [beta gates](beta-scope.md) and a concrete serious defect in
supported behavior are prerequisites. Routine work inside this approved scope
continues without renewed permission. Historical TODOs and resume commands do
not create new obligations.

For every proposed addition, record four short fields in its issue/PR/task:

| Field | Required answer |
| --- | --- |
| Promise or defect | Which supported capability or concrete serious failure is affected? |
| Evidence | What observation makes it necessary now? |
| Smallest action | What change/check resolves it, including reasoning, machine and operator cost? |
| Stop condition | What finite result ends the work? |

If these answers do not justify pre-beta work, defer it. Do not add tests,
refactors, cleanup campaigns, build systems or research merely because they are
possible. A changed support promise or a concrete serious defect can reopen the
relevant gate; an unrelated commit cannot. Keep optional jobs off the critical
path. Review total cost per accepted result, not test count or model tier alone.

## Deferred, without implied commitment

- Automatic guest startup after host reboot.
- Full managed shutdown/checkpoint/reconciliation and managed-specific custody
  and restoration qualification.
- Additional recovery automation and advanced guest lifecycle features.
- Optional deeper endurance/fault testing when real use provides a reason.

These are parked source/designs, not an automatic post-beta work queue. Admit a
future release feature using the same benefit, evidence, cost and stop rules.
There is no plan to support another Orange Pi model/series or generic A733 board.
No GUI, replacement management stack or orchestration platform is implied.

## Durable efficiency

Keep one short current status, stable AGENTS rules, executable validation and
packaging commands, relevant-dependency receipts and small runtime usage reports.
Successful scripts should need one compact result. Preserve completed reviews
and evidence; fresh sessions should resume from facts rather than replaying the
whole investigation. Recalibrate effort on real tasks only when a failure or
model upgrade warrants it; do not duplicate every assignment as an experiment.

# Project plan

The active plan is the [manual SPI/NVMe beta](beta-scope.md), with a finite
[current status](implementation-status.md) and [future work admission policy](roadmap.md).

1. Freeze the manual profile and complete software validation for affected code.
2. On the operator's hardware resumption, preserve the paused state, perform the
   required fresh boot, and construct/deploy the manual beta candidate once.
3. Reuse matching evidence, close the bounded boot/lifecycle gaps, and package
   the exact supported artifacts with known issues.
4. Publish only assets eligible for the chosen distribution; record actual gates.

Keep the implementation and process changes together in the overhaul PR.
Working management components stay; unfinished automatic management remains
parked. No new boards, replacement management platform, unrequested features,
long soak or general upstream qualification enters the beta by implication.

[Original research/build plan](archive/pre-beta-20261114/hypervisor-plan.md).

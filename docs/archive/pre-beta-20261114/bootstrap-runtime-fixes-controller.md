> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Registered runtime-corrected bootstrap controller

`build/launch-host-bootstrap-runtime-fixes.py` binds the historical bootstrap
launcher to the accepted corrected-chain state in
[evidence](../../evidence/production-host-state-runtime-fixes-20261108.json), committed
at `486d66a`. It retains the historical CLI and all historical predicates,
construction steps, input authentication, resource limits and final checks.
Only counted AST string constants change in the SHA-pinned historical body.
The original and repeat launchers remain unchanged.

The registration selects state UUID `360dfb39-f49f-487e-b307-0bb887c854c5`, phase
`/var/lib/a733-clean-replay-20261106/host-state-timesync-runtime-fixes-02`, output
`work/project/out/host-state-timesync-runtime-fixes-02`, source
`6dbf6d97568f2c6eeef3d0a326552358fe4b3d9a`, and receipt SHA-256
`a25aa352b42bc49c13218d61587ea68636e83d21b77a34af52c6fd68db12ae88`.
Evidence byte length, SHA-256, canonical JSON SHA-256 and Git blob identity are
pinned in the controller. The state adapter is separately SHA-pinned; both it
and its historical state template remain open with ancestry and identity checks
through the existing root execution final checks. The real state family retains
its complete nested parent root proofs, including expected tree, stability and
member bindings. Successful plans add `runtime_fixes_controller`, distinct from
the repeat controller record.

Invoke with `python3 -I -B build/launch-host-bootstrap-runtime-fixes.py --help`
for the unchanged required arguments. Use the exact phase receipt and digest
above. Planning and construction require the executing controller and its
closure to be committed. This document records registration and host checks;
it does not record a completed bootstrap build or authorize device writes.

Focused validation on November 8:

- `python3 -B -m unittest discover -s tests -p test_bootstrap_runtime_fixes_controller.py`
  checks actual committed evidence/policy, counted AST changes, input and source
  drift, complete root proof shape, receipt/readback predicates, held template
  replacement, final-check ordering and CLI isolation.
- The local accepted producer receipt, state manifest and independent readback
  under `out/state-runtime-fixes-20261108/` also pass the unchanged
  `authenticate_state` using the real verifier's held files and JSON reader,
  with the policy computed by the real source family. Raw result:
  `tmp/bootstrap-runtime-fixes-actual-authentication.log`.

The target remains the A733 with actual 1,977,614,336-byte SD and nominal
250 GB NVMe, minimal host, and zero routine SD writes. A real KVM guest remains
a hardware acceptance requirement. This component is unbooted and is not for
the current installed `00fd…` state. The next physical action remains the
[reviewed VGIC diagnostic SD](../../next-host-boot-20261108.md), matched to that
existing installation; no NVMe reinstall or SPI write is part of this work.

The [actual committed-source construction](../../production-host-bootstrap-runtime-fixes.md)
now passes the complete privileged suite, producer inspection and independent
artifact review. Its source, consumed tag and remaining integration limits are
recorded separately.

# Known issues and support limits

| Item | Effect and disposition |
| --- | --- |
| Automatic management unfinished | Disabled/deferred; manual guests remain usable through the documented workflow |
| Managed terminal-socket qualification | The source repair is included; full automatic managed lifecycle qualification remains deferred with that feature |
| Manual disk hashes | After guest writes, clean shutdown, checkpoint and unit regeneration are required before a new start; a checkpoint is not a backup |
| Historical unexplained SSH interruption | Retain the observation. A recurrence or concrete normal-path cause deserves focused diagnosis; indefinite investigation without a symptom is not a gate |
| Hardware scope | Orange Pi 4 Pro only; original-unit evidence does not duplicate unique identities or prove materially changed hardware |
| Firmware/toolchain provenance | Existing pins and notices remain; public redistribution eligibility of some vendor inputs is unresolved. Package only the actual eligible asset set |
| Existing recovery image | Contains the original unit's SSH host private key; keep private and do not clone it onto another unit |
| Recovery evidence limits | Existing handled-interruption tests do not claim every power-loss or locked-kernel failure was reproduced |

The authoritative live state is [current status](implementation-status.md).
Technical evidence is preserved in [the previous acceptance record](archive/pre-beta-20261114/release-acceptance.md)
and [pause receipt](evidence/goal-pause-and-restoration-20261113.json).
Minor unsupported edges go here; ordinary-operation failures, data loss,
wrong-device writes and serious reachable privilege defects remain blockers.

> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# External managed boot qualification

`scripts/qualify-managed-boot.py` is an external evidence helper for candidate B,
source revision `f3496f5`. Supply it through the separately reviewed RAM/SSH
workflow. It is not installed in the candidate's maintenance closure and does
not require rebuilding the candidate. Running it without options prints a plan;
it imports no installed code, opens no transport and requests no synchronization.

Execution requires both `--qualify` and `--expected-boot-id UUID`. For example,
a separately authenticated operator session may execute the RAM copy with:

```sh
python3 -I -B /run/qualify-managed-boot.py --qualify --expected-boot-id "$observed_boot_id"
```

The expected UUID must come from the intended current boot. The helper fixes the
guest to `autokvm`, the service to `a733-managed-autokvm.service` and the module
to `/usr/lib/a733-tools/scripts/manage-guests.py`; there is no module-path or
guest override. `firstkvm` remains manual.

Before importing installed code, the helper authenticates candidate B's exact
26,273-byte `/usr/lib/orangepi-hypervisor/payload.json`, SHA-256
`396be626a7919b9c800dd6a00cc5f8c025fa26c2d5c124bdde469e3b8c347ab8`.
This pin was extracted read-only from the builder's independently verified
candidate B image. The outer build manifest is
`out/managed-permit-root-trial-20261113-01/manifest.json`; the payload
marker is generated separately inside each slot image. The helper verifies the
marker's complete maintenance and management runtime file descriptors before
import, including ownership, protected directory placement, sizes, hashes and
recorded modes. The management module `manage-guests.py` additionally has the source pin
`8a6211b45d6d4b0231b100c71c2bb72a6f4eb475e04de5652104111a7d43520e`.
Qualification requires the isolated interpreter and bytecode-disabled flags
`-I -B`. Before imports, the helper requires a read-only root and checks the
exact 85-file maintenance namespace: all parent directories must be protected,
on the root device and read-only; extra files or directories, symlinks,
`__pycache__` and legacy adjacent bytecode refuse. It compiles the management module
from captured authenticated source bytes. This excludes existing timestamp-valid
bytecode from dependency loading. Existing installed guards then enforce the
native root and runtime identity.

Qualification borrows the installed module family's existing shared admission
lease and existing guest lock. It requires a canonical, completed, current-boot,
automatic `managed-power-key-v1` ready cohort. It calls the existing checkpoint
authenticator without preacquiring a registry or disk lock, retaining its lock
ordering and physical disk/manifest/registry checks. Missing completion refuses;
qualification cannot enroll, reconcile, repair, initialize, mount or dispatch.

**Qualification synchronizes NVMe objects.** This is explicit in the receipt and
may issue NVMe writes. It does not write SPI or SD. Before and after authentication,
PID1 must report the exact managed service inactive, successful, exited with
status zero, no main PID and no job. The existing full native task/FD census must
find no KVM consumers; unexpected scan errors refuse. The JSON receipt embeds
bounded protected head, run, intent, outcome, fixture, console, completion and
transition bytes with hashes. After authentication releases its locks, the helper
acquires the registry lock to retain the protected registry bytes and requires
its digest to match authentication and its guest entry to bind the ready head.
It conservatively refuses concurrent registry changes and does not authenticate
or synchronize disk objects while holding that registry lock. Preserve stdout off-device, including failed
receipts, stderr and exit status. Success is emitted only after cleanup succeeds.

Every receipt says `hardware_acceptance: false` and `power_authorized: false`.
This helper is neither a boot collector nor power permission: a census does not
exclude future unrelated launches, and a detached receipt does not authorize a
later reboot. Collector integration still needs its own retained exclusion,
current-boot binding, UART/boot evidence and independent physical review under
[the production readiness plan](../../spi-production-readiness-plan.md). Keep the
A733, nominal 250 GB NVMe, known 2 GB recovery SD and zero routine SD write
requirements. No endurance rerun or new hardware success is established here.

The focused fake API tests exercise refusal boundaries and resource cleanup;
they do not establish installed runtime, physical boot, KVM or storage acceptance:

```sh
python3 -m unittest discover -s tests -p test_qualify_managed_boot.py
```

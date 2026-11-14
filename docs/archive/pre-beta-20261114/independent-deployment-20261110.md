> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Independent physical deployment reproduction

The final normalized installation procedure was reproduced on the recorded A733
from a fresh operator context and clean RAM installer boot. Full installation,
same-board identity preservation, protected-SD boot, preserved guest restoration,
actual KVM, clean shutdown, checkpoint and restart pass. Subsequent [warm/cold boot qualification and immutable-SD evidence](../../release-acceptance.md)
complete release acceptance for the recorded fallback configuration.
[Installation evidence](../../evidence/normalized-host-install-20261110.json) and
[restored-guest evidence](../../evidence/normalized-guest-restore-20261110.json) pin raw
receipts. This does not claim an independently provisioned Ubuntu builder.

The operator used Sol medium without inheriting the coordinator's conversation.
An independent Sol high reviewer examined each concrete storage plan before its
apply. The coordinator retained architecture/integration ownership and separately
observed both live KVM runs. No child delegated. The complete original operator
report is retained privately at
`out/release-deployment-20261110-01/independent-deployment-report.md`, 14046 bytes,
SHA-256 `20bca5728cfa44bfb91c08abc809292e157eb992d1c2e4b17271f5324071111c`.

## Procedure reproduced

The starting guides were [deployment](../../deploy-protected-20261109.md),
[current images](../../current-images.md), [SD maintenance](../../known-bootstrap-sd-writer.md),
[guest activation](../../guest-activation.md), [restoration](../../guest-restoration.md) and
[checkpointing](../../guest-checkpoints.md). Actual command arguments, source runners,
plans, TTY prompts, exits, serial output and corrections are retained off-device.

| Step | Actual result |
| --- | --- |
| Enter private RAM recovery | Exact SU02G card received only the known 256 MiB recovery prefix; successful receipt retained before reboot; RAM boot `5e051870-626c-4da2-bb93-05fc29178614` authenticated at `.115` |
| Stage and install | Exact 545581074-byte encoded distribution and manifest authenticated; process/namespace/holder/swap census passed; one decoded 256060514304-byte write completed in 1507.81 seconds, exit 0; no preliminary full decode or readback |
| Preserve identity | Original machine-id and two SSH identity files restored, files/directories fsynced and STATE unmounted; original host fingerprint retained |
| Protected host boot | Exact protected 256 MiB SD prefix restored; normalized A boot `d9e94d54-79bb-49c3-ad72-2672a9c71c6a` at `.116`; root p2 read-only, 40 tools authenticated, original configuration/keys, DHCP/NTP and resolver policy pass |
| Restore guest | Immutable counter-5 archive authenticated and streamed directly to private NVMe STATE; exact p5 and locked UID/GID 60001 activated; new guest published durably with four original history records |
| Execute and restart | Actual QEMU PIDs 2553 and 3018 used KVM VM/vCPU/VGIC descriptors, UID/GID 60001, zero capabilities and NoNewPrivileges; CPU/disk/HTTP tests advanced 5 → 6 → checkpoint → restart → 7 |
| Finish safely | Each guest requested powerdown, with a prestarted referenced observer retaining raw exit code/status 1/0; final checkpoint 2 is durable, all six history records verified, updated RAM unit inactive/PID 0 |

The final manifest is
`768a0138781926b2fa7765fcf87f5fa444edfda4a004dc0a171d7daed59759fb`;
stopped disk SHA-256 is
`bea84eb95db444a3ea923db1ea58cabda4cc0f7392140bf45b4b2c64d1bca3fb`.
Checkpoint numbering restarts for the restored chain; disk counter 7 is not
checkpoint number 7. Original history remains intact.

## Discrepancies retained and resolved

- A native serial status query initially addressed the user manager; the system
  manager confirmed the running relay. Neither capture was restarted.
- A new SSH shell produced a nonsecret RAM plan as 0644. The metadata guard
  refused it; only its mode changed to 0600 under the private directory. Source
  files were already protected, and no installer retry occurred.
- The old host initially reported a failed `/var` unmount, then its final
  shutdown unmounted STATE and all filesystems. No reboot was repeated.
- The historical RAM banner conflicts with the deliberately bounded compressed
  staging method; the plan and guide now describe its memory guards explicitly.
  Three RAM CMA allocation failures remain unexplained in retained serial data.
  A late RAM dmesg request failed after reboot began; it was not retried.
- An obsolete RAM SSH transport remained open after the new host authenticated.
  Only that verified local client was terminated. Its exit 255 remains recorded.
- Wrapper assumptions about manifest-key case, aligned `findmnt` output and
  the history directory were corrected from authenticated schema/source.
  Refusals preceded their proposed changes; restoration was not repeated.
- `mount` printed its two-line fstab hint before a successful JSON receipt.
  The wrapper initially refused parsing; exact original output and bounded
  extraction remain recorded. The mount itself was not repeated.
- An unexecuted draft confirmed HTTP automatically. The actual flows separate
  launch/capture from confirmation; the coordinator independently inspected
  each live PID before admitting one confirmation request.

No endurance test was repeated. No SPI write, SD-removed boot, measured release
boot count or final whole-card comparison is claimed by this report. Original
host/guest/forensic preservation remains off-device; later functional guest
changes are disposable test data.

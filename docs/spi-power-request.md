# External SPI power request helper

`scripts/request-spi-power.py` is an external RAM helper for the unchanged
F349 trial B runtime. It is outside the installed runtime closure and needs
no root rebuild. Its default invocation emits a plan without reading installed
sources or starting a transport. Execution is a separate, explicit operation.

Stage the helper and the unchanged qualifier as root:root single-link 0400 files
in the root:root 0700 `/run/a733-spi-collector` directory on native `/run` tmpfs.
That directory must contain exactly `request-spi-power.py` and
`qualify-managed-boot.py`, including no bytecode cache. The qualifier must be
15424 bytes with SHA-256
`c2d09a43fdbc6894270e473209cb586b32d13946b31caeda0806c5a0c0381c0b`.
The helper compiles those captured, authenticated qualifier bytes and reuses its
installed-source authenticator; it never loads a qualifier bytecode cache.

The execution interface is:

```text
/usr/bin/python3 -I -B /run/a733-spi-collector/request-spi-power.py \
  --execute --action reboot \
  --expected-boot-id CURRENT-CANONICAL-UUID \
  --request-id NEW-CANONICAL-UUID \
  --expected-head-sha256 RETAINED-QUALIFIER-HEAD-SHA256 \
  --expected-registry-sha256 RETAINED-QUALIFIER-REGISTRY-SHA256
```

`poweroff` is the other action. Supply actual lowercase SHA-256 values and
canonical nonzero UUIDs. There are no path, guest, source-pin or storage overrides.
The native collector must durably retain its own request intent before opening
the SSH transaction, because remote output and acknowledgements can be lost.
Retain each attempt's evidence separately. This document does not itself
constitute an instruction to operate the board.

Execution requires the original root process and main thread, current native
boot and normal running/degraded PID1, fixed protected storage policy and full
NVMe/STATE/p5 checks, selected and running B with pinned marker and selector,
observed SD namespace absence, terminal successful inactive managed service, and
no visible KVM consumers. The fixed policy binds NVMe serial
`134807190FC700149277`, model `SPCC M.2 PCIe SSD`, capacity 256060514304 bytes,
STATE UUID `360dfb39-f49f-487e-b307-0bb887c854c5`, and guest UUID
`298b281a-d10e-44eb-bdb9-285e27eaafad`. eMMC reporting type `MMC` is allowed;
known `mmcblk1`, other whole MMC devices with SD/unknown type, and scan/read
errors refuse execution.

The helper opens existing canonical admission, requires an open record, closes
it for shutdown, and takes immediate canonical WRITE EX without a shared-lease
upgrade. With EX retained, the existing installed checkpoint implementation
reauthenticates the actual completed automatic `autokvm` fixture disk and its
current-boot ready head, managed power-key profile, completion records and
registry. Caller-supplied hashes must agree. No stop, force, repair, guest launch
or publication by checkpoint authentication is allowed.

It exclusively creates `/run/a733-spi-power/request.json` inside a new root-only
0700 directory, as a bounded root-only single-link 0400 file, and fsyncs file and
parent. The intent binds the request, boot, action, source/storage identities,
closed admission record and retained authenticated head/run/intent/outcome/
completion/registry evidence. Any existing directory, intent or partial
publication blocks another same-boot attempt. It never deletes intent or reopens
admission automatically.

All guest/controller/disk resources and EX must acknowledge cleanup before the
power command. Final checks retain only policy and admission descriptors, verify
unchanged closure/intent/sources and repeat live boundary observations. The
helper flushes a JSON precommand record, marks the attempt locally, then invokes
exactly one `/usr/bin/systemctl reboot` or `/usr/bin/systemctl poweroff` with a
fixed environment, no shell/force, and a ten-second timeout. It emits a final
JSON receipt if execution remains observable. Timeout, command error, cleanup
uncertainty or lost SSH acknowledgement never permits automatic retry. Preserve
the closed gate and evidence for explicit recovery and observations. An actual
new boot resets RAM state.

These receipts set `hardware_acceptance=false`. Sysfs block absence does not
prove physical SD removal. A systemctl acknowledgement proves neither power
removal nor a new boot. Qualification still needs independently reviewed UART,
SPI/NVMe boot identity and real KVM evidence; cold cycles additionally need the
operator's physical power-removal attestation. Host tests exercise refusal and
ordering using explicit fake installed APIs; they do not validate board behavior.

[Software review and off-device custody](evidence/spi-power-request-validation-20261112.json)
record the exact source pins, fixture substitutions and pending collector integration.

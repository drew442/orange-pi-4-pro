# Qualify metadata-aware SPI recovery

Status: reviewed blank restoration, SD recovery and SPI reinstallation pass
physical checks. The board is cleanly powered off for SD removal; the next
SD-removed SPI/NVMe boot and actual KVM demonstration remain pending.
[Physical evidence](evidence/spi-reinstall-and-poweroff-20261113.json).
[Software evidence](evidence/spi-metadata-validation-20261112.json).
This is phase 3 of the [production plan](spi-production-readiness-plan.md).
Known-board storage authorization persists; no additional erase confirmation is
needed for these identified targets. A changed target requires yes/no approval.

## Operator steps

Keep the UART bridge powered throughout. Retain the known SU02G 2 GB card,
CID `035344535530324780716f216e00a8f5`, with its protected recovery bootstrap.
Do not write another image to it for this test. No heatsink removal is needed.

1. After the coordinator confirms clean A733 poweroff, remove USB-C power,
   insert that recovery SD, then restore power. Leave the card installed.
2. The coordinator verifies the SD boot route, prepares a fresh reviewed SPI
   restore plan and restores the original blank SPI. The coordinator then
   reboots through SD and verifies management and NVMe state.
3. The coordinator reinstalls the registered SPI firmware with the revised tool,
   verifies readback/close/off-device receipts, and requests clean poweroff.
4. Only after that confirmation, remove USB-C power and SD, wait at least ten
   seconds, then restore power. Keep the card for recovery.
5. The coordinator verifies the SPI/NVMe boot, firmware components and actual
   KVM guest execution. Retain discrepancies rather than counting a failed case.

These are genuine physical card/power transitions. Remote reboots or observations
cannot substitute for the operator's confirmation. Qualifying final-configuration
boots may count toward the SPI series; recovery SD boots do not.

## Coordinator procedure and evidence

Before any power transition, confirm the exact current boot ID, no QEMU/KVM
consumers, available RAM, functioning UART capture/relay and off-device backups.
Retain a UART cursor and admission receipt. An uncertain acknowledgement permits
only read-only observation; never replay a reboot/poweroff automatically.

On the SD recovery boot verify UART storage selection, the SU02G CID, expected
NVMe serial `134807190FC700149277`, root/STATE pairing, key-only management and
block-level SD protection. Read the complete current SPI and retain it off-device.
The legacy recovery bootstrap does not set SD block read-only flags. After
checking the exact card identity, zero write counters and absence of mounts,
holders, swap or open block consumers, explicitly set both the whole card and
its existing partition read-only. Verify both with BLKROGET and sysfs; do not
assume the whole-card flag protects an existing partition. Repeat this step
after each reboot, because these flags do not persist across boots. Setting
these flags does not write card contents.
The SPI remains XM25QU128C, JEDEC `204118`, `/dev/mtd0` character 90:0,
16,777,216 bytes, Linux erase size 65,536 bytes.

Recreate private staging under `/run` after every boot. Check tmpfs capacity first:
allow at least 128 MiB free for sources/images plus readback. Keep no swap and
preserve existing RAM mounts. Stage the exact reviewed programmer (0400), helpers,
original/factory images and recovery proof (0600), with private 0700 directories.
Verify all staged sizes/hashes against current source pins. The registered proof
is `docs/evidence/spi-sd-recovery-policy-20261111.json`; its pin is compiled into
the programmer. The capture controller authenticates the programmer's source.
[Programmer contract](known-spi-programmer.md).

Use `program-known-spi.py plan` with the freshly observed `--expected-boot-id`,
held `--original`, `--candidate` and `--helpers` paths. Add `--direction restore`
for original blank restoration; the default restore ID is `original`. Independently
rebuild the complete canonical plan, checking full current/desired hashes, target,
all preservation regions and units. Write only through
`capture-known-spi-events.py` with that reviewed plan/digest and registered
recovery proof. Retain/fsync the terminal receipt before another power transition.
Do not substitute old November 11 source pins or an earlier boot's plan.

After blank restoration verify the complete all-FF original hash
`dffab0dd410657cb30c7b2fd7f2586a4792e8472e58882b3532581f8111a646d`.
Boot again through SD, revalidate identities and stage inputs anew. Build/review
an installation plan from that fresh snapshot, then use the same captured write
path without restore options. Installation uses the registered factory code and
preserves validated live parameters. Record the complete intended/readback hash
before boot-time vendor updates. No speculative firmware patch is included.

After SD removal and power restoration, verify UART SPI storage selection and
NVMe file loading, distinct boot ID, SD absence, expected read-only root/STATE,
SSH identity, NTP, eight CPUs/EL2 and actual nonroot KVM CPU/disk/network,
guest-owned clean poweroff and durable checkpoint/restart. Validate code hashes
independently of bounded vendor parameter updates. Preserve private raw logs and
binary backups outside Git and verify a synced second copy on the Ubuntu builder.

A failed/uncertain SPI attempt stops the sequence. Preserve partial receipts and
inspect fresh bytes before a new plan. Malformed metadata requires a separate
reviewed recovery-preimage registration, not a relaxed decoder. Keep the known
SD recovery route available and report the exact limitation. Host model tests
are not physical recovery evidence.

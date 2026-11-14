> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Physical KVM networking, shutdown and restart — 2026-11-08

The A733 now passes actual KVM guest CPU, NVMe-backed disk, DHCP/HTTP,
guest-requested poweroff, durable checkpoint and restart-persistence tests on
the [VGIC diagnostic boot](../../vgic-live-boot-20261108.md). Counter progression was
**1 → 2 → 3**. This completes the diagnostic guest core tests; final corrected
host deployment and release acceptance remain open.

Both observed QEMU processes ran at UID/GID **60001**, with empty capabilities,
`NoNewPrivileges`, one vCPU, 256 MiB guest RAM and a 512 MiB unit memory cap.
They used `virt,gic-version=host -accel kvm -cpu host`, the empty NIC ROM option,
and actual KVM VM/vCPU descriptors. No TCG fallback or root QEMU was used.
Both guest boots obtained `10.0.2.15` and served the exact expected counter via
`127.0.0.1:18080/cgi-bin/probe`. Confirmation used the matching counter before
letting each guest power itself off. The forward was loopback-only; requests to
the host LAN address failed as intended, while management SSH remained active.

| Run | QEMU PID | Disk counter | Result |
| --- | ---: | --- | --- |
| Resumed guest | 2157 | previous 1, new 2 | CPU/disk, DHCP, HTTP and guest poweroff pass; checkpoint 1 published |
| Restarted guest | 2665 | previous 2, new 3 | Same checks pass; exact exit fields retained; checkpoint 2 published |

The prior [counter-1 run](../../first-kvm-results-20261108.md) was stopped by host
SIGTERM and remains explicitly unclean. Its disk and enrollment survived this
host reboot unchanged. A reviewed RAM resume config bound those recorded disk
bytes without changing enrollment or inventing a prior clean checkpoint.
Only after counter 2's actual guest poweroff was observed did the normal
checkpoint tool advance enrollment; the second checkpoint followed counter 3's
clean exit. Both histories and published manifests are retained off-device.

For counter 2, the guest logged network PASS, clean-shutdown request and kernel
`Power down`; systemd logged successful deactivation. The later unit query had
lost its execution history to garbage collection: all-zero timestamps and an
empty invocation ID mean its reset code/status **0/0 are not raw exit evidence**.
That observation gap remains recorded. For counter 3, the [reviewed observer](../../guest-exit-observer.md)
held a D-Bus unit reference through completion and captured PID 2665,
**ExecMainCode=1, ExecMainStatus=0, Result=success**, positive ordered execution
timestamps, inactive/dead and no remaining main/control PID. The waiting start
command returned 0. No host stop was issued for either successful run.

The [evidence index](../../evidence/kvm-vgic-results-20261108.json) pins complete
process/fd captures, both guest consoles, HTTP probes and confirmations,
checkpoint plans/receipts/history, source closures and independent review.
All 39 installed-tool hashes remain unchanged. Only reviewed RAM account and
launcher corrections were used. The current guest is stopped, with counter 3
and enrollment SHA-256
`1317731d7025a3bd609cc92e1bbd24d7a19172822bae77d5bbb469b127bc6291`.
The inactive RAM unit was freshly rendered for that checkpoint; no fourth run
was started. Do not reuse the historical pristine first-guest enrollment steps
or the counter-1 resume helper against this advanced state.

This run uses the original `00fd…` 220-package host and diagnostic SD, not the
unbooted corrected 221-package candidate. Integrate the proven VGIC DT and
matching boot firmware, then validate packaged fixes/time, SPI recovery/boot or
immutable SD fallback, zero routine SD writes, boot/endurance tests,
interrupted update/rollback, missing-NVMe/dirty-journal recovery, off-device guest
restore and independently reproduced deployment. No SPI programming or NVMe
reinstallation was performed in this session.

> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Corrected host installation, 2026-11-08

The approved physical NVMe installation, same-board identity restoration and
matched SD bootstrap write completed from the private SSH RAM installer.
[Write receipts](../../evidence/corrected-host-install-20261108.json) bind the exact
known targets, authenticated sources and independently reviewed helpers.

The installer expanded and wrote 256,060,514,304 bytes once, reporting progress
and flushing writes. Readback was not requested. It then restored the existing
SSH host key and machine identity into the new STATE filesystem and unmounted
it. The original stream hash describes the image before that personalization.
The 268,435,456-byte matched bootstrap was written to the actual
1,977,614,336-byte SD card, then flushed. No SPI write occurred.

The [corrected host boot](../../evidence/corrected-host-boot-20261108.json) now passes:
verified the
pinned SSH host key, read-only NVMe root, STATE UUID `360dfb39-f49f-487e-b307-0bb887c854c5`,
complete installed 40-file maintenance tree and 221-package manifest. Automatic
time synchronization passes. The authenticated counter-3 backup was restored
onto GUESTS UUID `298b281a-d10e-44eb-bdb9-285e27eaafad` and [actual
KVM execution, clean guest poweroff, checkpoint and restart pass](../../evidence/corrected-host-guest-restore-20261108.json), advancing the disk counter 3 → 4 → 5.

A full-card baseline read was recorded solely for release acceptance of
zero routine SD writes. This is separate from installation; normal installation
readback remains optional. Compare the baseline after boot/workload/shutdown,
record Linux block-write counters and audit firmware writes. Successful storage
writes do not establish boot, guest or release acceptance.

The post-install, pre-boot full-card baseline is SHA-256
`436fb8258d0ce9fed094babdedbeb39aa339dca57b2a3b7642775425971acc08`
for all 1,977,614,336 bytes. This read took 164.53 seconds with progress.

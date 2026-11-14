# Backup, restore, update and rollback

Use the complete authenticated maintenance tree belonging to the installed root.
Keep current manifest pins and receipts with the installation inventory. Old
reference records describe completed work; [current status](implementation-status.md)
contains the remaining gates.

## Guest backup and restore

1. Shut down inside the guest, confirm QEMU has exited and run the
   [stopped-disk checkpoint](guest-checkpoints.md). Save its new trusted manifest pin.
2. Follow [off-device backup](guest-backups.md): the existing sender streams to a
   separate machine, and the receiver verifies the archive against the successful
   source receipt. Keep that archive and both receipts off-device. A checkpoint
   alone is not a backup.
3. Use [restoration](guest-restoration.md) to a fresh destination, with matching
   account identity and the actual destination filesystem UUID. The tool refuses
   existing directories and never silently replaces a running guest.
4. Render the restored guest unit with its returned manifest pin and start through
   [manual operations](manual-guest-guide.md). Existing applicable restoration
   evidence satisfies the beta recovery gate; every unrelated code change does
   not require another full transfer and restore.

## Host update and rollback

Keep the compatible previous root, STATE/identity backup, BOOT inputs, recovery SD
and SPI backups. Stop/checkpoint manual guests before maintenance. Use the existing
[update tools](host-updates.md) to prepare and write the inactive root, then the
[selector/recovery tools](host-recovery.md) to choose the next root. A root update
does not program SPI or rewrite guests. A root partition payload is not a whole
NVMe installation image.

Inspect the concrete plan's device, partition, capacity and manifest identity;
reuse standing authorization for that exact known target. Honor shared admission
and consumer checks. Changing the profile means building a matching new root,
not editing mutable STATE or injecting an environment flag.

After reboot verify the selected root and manual profile, then start wanted guests.
If boot/management fails, use UART/recovery SD and select the retained compatible
root. Automatic rollback and automatic guest lifecycle recovery are not beta
promises. Do not delete lifecycle markers or use stale STATE to conceal an
incomplete managed operation; preserve it and use supported recovery.

## Physical acceptance already retained

[Host update/rollback](archive/pre-beta-20261114/physical-host-update-rollback-20261110.md),
[paired host restoration](archive/pre-beta-20261114/paired-host-restore-20261110.md),
[guest restoration](evidence/normalized-guest-restore-20261110.json) and
[SPI restoration](evidence/spi-blank-restoration-and-sd-recovery-20261113.json)
retain their actual scope. Handled-interruption results do not claim arbitrary
power-loss survival. Revalidate an affected path after a relevant change, with a
specific stop condition; avoid another broad physical fault matrix.

# Current SD firmware write-policy audit

The independent [source/configuration/provenance audit](evidence/sd-firmware-write-policy-20261109.json)
is complete for the protected SD firmware. **Immutable-SD hardware acceptance
remains open.** The audit changed no image and contacted no hardware. Do not
interrupt the active endurance workload to gather later evidence.

The retained physical first 256 MiB matches protected image `ab5e8c…ad207`.
Construction evidence preserves firmware prefix `da4a8d…2b556`, which binds SD
boot0 `9213f1…75345` and the **EL2** package `b20c3d…ab3b6`. Authenticated frozen
replay binds unpacked U-Boot `f552de…b1c7c`, U-Boot commit
`b791be842935b27268ae3d00e943a9075495f30a`, config `253f9c…02bb9` and source
archive `63e1c7…ab7c3`. The evidence contains complete hashes, sizes and source
members. Original pre-EL2 diagnostic binaries alone are not the current anchor.

The selected distro/host script reads SD boot files into RAM, changes RAM
bootargs/FDT and boots or loops. Its `setenv` and FDT commands do not themselves
save data to SD. Reviewed ordinary write paths are disabled:

- `ENV_IS_NOWHERE=y` and disabled persistent backends/`CMD_SAVEENV` select
  [environment initialization without persistent load/save hooks](https://github.com/orangepi-xunlong/u-boot-orangepi/blob/b791be842935b27268ae3d00e943a9075495f30a/env/nowhere.c#L21).
- Disabled bootcount makes [its increment a no-op](https://github.com/orangepi-xunlong/u-boot-orangepi/blob/b791be842935b27268ae3d00e943a9075495f30a/include/bootcount.h#L85).
- `BOOTCMD_SKIP_MISC=y` excludes misc consumption/clearing; disabled
  `SUNXI_SWITCH_SYSTEM` excludes the environment-saving switch object.
- OTA boot0 turning, automatic update, FDT saving and boot0-copy recovery are off.

Enabled symbols alone can be misleading. In this pinned source,
[`sunxi_bootparam_down()` returns immediately](https://github.com/orangepi-xunlong/u-boot-orangepi/blob/b791be842935b27268ae3d00e943a9075495f30a/sprite/sunxi_boot_param.c#L97),
the [normal GPT-update call is commented out](https://github.com/orangepi-xunlong/u-boot-orangepi/blob/b791be842935b27268ae3d00e943a9075495f30a/common/board_r.c#L604),
and `update_user_data()` also returns immediately. These observations describe
selected source behavior, not every operation the firmware can perform.

MMC/ext4 write commands, vendor flash/burn and secure-storage facilities remain
compiled. Generic flash initialization reaches backup-boot0 handling, whose MMC
implementation skips writes for its normal secure-mode condition but can mirror
boot0 otherwise. Recorded `workmode=0, storage type=1` supports the ordinary SD
boot observation; **work mode is distinct from secure mode**. Conditional keybox
re-encryption/storage writes also exist. No `keybox_list` assignment was found in
the pinned text inputs; this does not establish every runtime/opaque condition.

Configuration cannot prove opaque boot0, monitor/TF-A or SCP behavior. Missing
serial messages do not prove no writes. Linux counters start after firmware,
and identical whole-card hashes cannot detect same-content writes. Retain these
limits rather than describing the firmware as incapable of writes.

After retaining the endurance outcome, complete the original planned evidence:

1. Preserve the complete-card SHA-256 after workload/shutdown **before changing
   the SD image**, and compare it with the authenticated baseline for exact CID
   `035344535530324780716f216e00a8f5`, capacity 1977614336 bytes.
2. Choose the final boot route and retain final matched cold/warm boot logs,
   exact firmware/SSD identities and Linux write/discard/flush counters.
3. Compare the whole card after that boot/shutdown series and reconcile the
   serial storage/update/recovery behavior with this bounded source audit.

Intentional maintenance writes require a new matching baseline for the final
route. Avoid full-card hashing on every boot; it belongs at the defined series
boundaries. Preferred SPI boot still requires independent recovery and actual
SD-removed boot; an SD trial is not a substitute for either.

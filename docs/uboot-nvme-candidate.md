# Separate NVMe bootloader component candidate

This recipe builds components for the preferred SPI-to-NVMe path. It creates
**no combined SPI image or programmer**, changes no installed boot files and
establishes no hardware acceptance. Preserve the working SD images. Programming
still requires original-flash backup, demonstrated recovery, NVMe file reads,
independent artifact inspection and exact-target yes/no authorization.

The pinned [sources and layout](spi-boot-investigation.md) remain unchanged.
`build/uboot-nvme-candidate.py` authenticates the registered SD build's configuration
and changes exactly its `CONFIG_BOOTCOMMAND` line to:

```text
setenv boot_targets nvme; run distro_bootcmd
```

It retains both reviewed patches, EL2 service `0x8000ff40`, the environment in
RAM only and the disabled saveenv/misc/system-switch/bootcount/OTA settings.
It uses fresh vendor-generated challenge bytes, not the SD artifact's retained
challenge or replay generator patch. The generated C is retained for later
explicit registration and reproduction of this candidate. See [the challenge
boundary](uboot-reproducibility.md).

The recipe verifies pinned sources, GCC 7.4.1, all 26 firmware/tool inputs and
complete builder inventories before/after construction. It uses a private
source/compiler/pack tree, two compilation jobs and the original packing steps.
Outputs include U-Boot, its ELF/map/configuration/challenge, bootm source/object/
disassembly, boot package and the unchanged SPI boot0 as separate files.
Monitor, SCP and SPI boot0 hashes must remain unchanged. No diagnostic binary
match is expected from the changed target and fresh challenge.

## Frozen builder execution

From a clean committed checkout on `build.drewnet.online`:

```sh
sudo python3 -I -B build/launch-uboot-nvme-candidate.py \
  --project "$PWD" --tag first-01 --plan
sudo python3 -I -B build/launch-uboot-nvme-candidate.py \
  --project "$PWD" --tag first-01
```

The plan is read-only. Actual execution uses the existing frozen Ubuntu 22.04
builder, readonly imported source/provenance binds, private networking and the
existing capability restrictions. It authenticates Git sources and a source
bundle, checks the namespace probe and preserves logs/receipts on failure.
Use a fresh tag; never overlay an earlier attempt.

The historical two-replay launcher's 10 GB guard stays unchanged. This separate
single-candidate recipe calculates a construction estimate from pinned Git file
sizes, authenticated compiler archive headers and explicit build/pack/log
allowances. It reserves **more than 4 GB after construction**, matching the
unchanged final input validation. Runtime abort floors remain 3 GB disk and
1 GB available RAM. A failed budget check must not trigger a lower guard.
Builder resources are independent of Orange Pi performance; no hardware
benchmark is needed.

## Separate host script

`configs/spi-nvme-host-boot.cmd` is compiled into a separate, uninstalled legacy
script. It retains compatible bootargs, payload/address variables, DTB resize
and the patched `bootm` handoff. Its own reads require exactly `nvme 0:1` and
`/boot/`, with the partition explicit in every load. Failed load/FDT/handoff
returns failure without looping. Manual SD recovery remains `run bootcmd_mmc0`.

The pinned hush parser can report success for `exit 1`; the script instead ends
with `false`. Source references are [ordinary false status](https://github.com/orangepi-xunlong/u-boot-orangepi/blob/b791be842935b27268ae3d00e943a9075495f30a/cmd/test.c#L193)
and [hush exit handling](https://github.com/orangepi-xunlong/u-boot-orangepi/blob/b791be842935b27268ae3d00e943a9075495f30a/common/cli_hush.c#L3217).
Bash control-flow fixtures check these intended branches; they do not execute
U-Boot or Linux.

The firmware retains its broader distro scan: other bootable partitions,
`/` and `/boot/`, extlinux, script alternatives and EFI. The script's guards
restrict its own payload reads only. Before deployment, independently verify the
reviewed boot-partition file list and GPT flags; do not claim the whole firmware
has a closed partition/file policy. The current installed `/boot/boot.scr` is
the earlier SD-oriented script and must not be presented as this candidate.

The outer launcher independently checks the generated script's legacy header,
architecture/type, timestamp, CRCs, size table and exact source bytes. Host
fixtures and source review precede an actual component build; build results are
recorded separately. Neither component compilation nor script packaging proves
SPI boot, recovery, KVM guest execution or zero routine SD writes.

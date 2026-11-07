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

## RAM workspace option

The [first actual attempt](evidence/uboot-nvme-first-attempt-20261107.json)
stopped before full U-Boot compilation: its new validator required a disabled OTA
comment absent from the authenticated configuration. The corrected validator
accepts the absent unknown symbol and rejects every value assignment; complete
configuration equality still permits only the BOOTCOMMAND change. The failed
attempt's source, compiler, logs and receipts remain intact.

Use `--ram-work` with a fresh tag to construct this small component in builder
RAM. It requires at least 4 GiB MemAvailable initially, retains the 1 GB runtime
memory floor, and uses a root-private tmpfs with an 8 GiB maximum size. The pinned
construction estimate is about 1.67 GB; the maximum is not an allocation.
Readonly input filesystems retain their >4 GB guard. Construction reservation
applies to the writable output filesystem, including the unchanged >4 GB final
reserve. Disk mode retains the same shared-filesystem admission rule.

```sh
sudo python3 -I -B build/launch-uboot-nvme-candidate.py \
  --project "$PWD" --tag ram-02 --ram-work --plan
sudo python3 -I -B build/launch-uboot-nvme-candidate.py \
  --project "$PWD" --tag ram-02 --ram-work
```

Before unmounting, the launcher archives the entire workspace as `work.tar.gz`,
compares it against the live tree, fsyncs it and records its size/hash. Review
copies are retained under `component-retained/`; they are fsynced as well. The
immutable recovery record and final receipt have distinct names. If retention
fails, `mounted_work_path` and `retention_error` identify the still-mounted
workspace to recover before removing its mount. A source/compiler path inside
an unmounted RAM workspace must be restored from this archive before reuse.
Physical SPI and NVMe are unaffected by this build-host option.

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

## Actual candidate build

[The RAM build from `01372ec`](evidence/uboot-nvme-candidate-20261107.json)
passes all compilation, packaging and retention checks. U-Boot is **1,192,546
bytes**, its boot package **1,392,640 bytes**, and the separate unchanged SPI
boot0 **237,568 bytes**. The independent review verifies the exact configuration
derivative, fresh challenge, unchanged generator, linked ARM instructions calling
SMC with `0x8000ff40`, executable bytes, packed firmware payloads and host script.
All 72 artifact-manifest file hashes pass independent native readback.

The complete RAM workspace is retained on the builder and codex as a
**412,929,105-byte** archive with SHA-256
`436e35cd9b6e16ccef0a991f88b8afb4a57c21bcd3e374c0946cd6c77635b49a`.
Detailed copies, paths, hashes and restoration mapping are in the evidence. The
original empty `work/` directory is unmounted; private source/compiler paths
listed by the original component manifest require archive restoration before
reuse. The review files remain directly available under `component-retained/`.

Minimum host disk free and available RAM were **4.05 GB** and **6.08 GB**.
Live postchecks show no mapped build processes or phase mounts; package inventory
is unchanged. RAM mode still needs disk for the archive and review copies: this
run retained approximately 706 MB. Current space is insufficient to repeat that
retention while preserving the final >4 GB reserve. Do not rerun blindly or
lower the guards; the separate production capacity proposal remains pending.

This first candidate's generated challenge is now an authenticated retained
input. Exact candidate reproduction still needs a separately registered replay
using that same input. No flash image, SPI programming, NVMe file-read test,
physical boot or KVM guest test is established by this software build.

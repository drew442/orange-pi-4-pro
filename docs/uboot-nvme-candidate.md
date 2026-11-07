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

The firmware retains its broader distro scan: bootable partitions, `/` and
`/boot/`, extlinux and both script alternatives. The
[linked-firmware clarification](evidence/nvme-firmware-search-policy-20261107.json)
confirms that **EFI loading is disabled in this exact candidate**; its conditional
EFI code is absent from the linked scan. It also confirms the vendor's unnumbered
`bootcmd_nvme` and partition-1 fallback: five Linux-type GPT entries with zero
attributes produce an empty bootable list, remove `devplist`, then select partition 1.

The script's guards restrict its own payload reads only. Before deployment,
independently verify the complete boot-partition file list and actual GPT types/
flags; do not claim the whole firmware has a closed partition/file policy.
The current installed `/boot/boot.scr` is the earlier SD-oriented script and must
not be presented as this candidate. The new opt-in `spi-nvme-v1` profile in
`build/nvme-image.py` integrates this separate script into a future NVMe BOOT
filesystem while leaving its matching SD bootstrap unchanged. The
[full NVMe construction](production-nvme-composition.md) now passes complete
software inspection. The [firmware transport SD](nvme-firmware-test-sd.md) now
also passes frozen construction and inspection, allowing a later U-Boot test
before SPI programming. Physical boot remains unproven.

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
run retained approximately 706 MB. At that completion, space was insufficient
to repeat that retention while preserving the final >4 GB reserve. Subsequent
authorized growth and constructions are recorded in the current
[host assessment](build-host-assessment.md). Recheck the selected phase budget
before rerunning; do not lower guards.

This first candidate's generated challenge is now an authenticated retained
input. The separately registered replay below reproduces it exactly. No flash
image, SPI programming, NVMe file-read test, physical boot or KVM guest test is
established by these software builds.

## Registered exact replay

`configs/uboot-nvme-candidate-targets.lock.json` registers the retained
`nvme-candidate-20261107-ram02` artifact. It binds its original manifest,
immutable evidence, producer inputs, configuration and canonical challenge.
The loader authenticates the actual retained inputs and committed registry;
there is no arbitrary challenge override. Fresh builds retain random generation.

The explicit replay path patches only its private Makefile with the conditional
vendor-generator adapter and supplies the same registered challenge. It retains
the generator patch, input C and truthful replay provenance. Success requires
exact unpacked U-Boot, boot package, configuration, challenge and SPI boot0
hashes. ELF and object debug metadata are outside that reproduction claim.

On the prepared builder with the original retained target available:

```sh
sudo python3 -I -B build/launch-uboot-nvme-candidate.py \
  --project "$PWD" --tag registered-01 --ram-work \
  --replay-target nvme-candidate-20261107-ram02 --plan
sudo python3 -I -B build/launch-uboot-nvme-candidate.py \
  --project "$PWD" --tag registered-01 --ram-work \
  --replay-target nvme-candidate-20261107-ram02
```

Planning reads only committed sources. Execution authenticates the original
72-file artifact set and full workspace archive, then stages five small,
readonly inputs through the existing source bind. Only a successful registered
RAM replay can retain identical files by reference. Every entry in its new
artifact/log manifest must resolve to a verified local copy or the same-path
original file with the expected hash. All logs, producer inputs, replay patch,
challenge and changed files remain local. The retained reference map binds both
manifests and the original full workspace archive.

Original references are rehashed; their durability relies on the first run's
already-fsynced retention. New local files and recovery records are fsynced
before unmount. The unchanged >4 GB reserve and runtime floors still apply;
copy budgeting must pass before retention begins. Any payload, proof or retention
failure leaves RAM mounted, with no fallback discard. Default fresh RAM builds
continue to retain the complete workspace archive.

The new private source/compiler tree is reconstructable from pinned sources,
compiler, registered challenge, producer revision and retained patch. Reference
retention does not claim an exact snapshot of every new cache-file timestamp.
Actual replay results are recorded separately; source review and fixtures alone
do not establish matching rebuilt artifacts or hardware acceptance.


## Executed registered replay

The [actual replay record](evidence/uboot-nvme-replay-20261107.json) records source
`3f2c39a27bfafe6e19507570a371d005c3e02dad` and phase
`/var/lib/a733-clean-replay-20261106/uboot-nvme-component-registered-01`.
It completes with two compilation jobs and unchanged package inventory. All five
required artifact hashes match the first candidate. The separate host script
also retains both original hashes and verified CRCs.

Independent Sol high artifact review passes with no findings, including the
complete original archive, new bundle HEAD, generator patch and producer copies.
ELF, bootm object/source/disassembly and link map also match byte for byte.

All **77** manifest entries pass native readback: **30** retained local files and
**47** verified references. New producer inputs, replay patch, provenance and
logs remain local. Resolve native references using
`out/spi-candidate-ram-02-20261107/component-retained`; new local records are in
`out/spi-candidate-registered-01-20261107/component-retained`. The original
`out/spi-candidate-ram-02-20261107/work.tar.gz` remains the shared full workspace
archive; keep both sets and the reference map together when transferring them.
No second complete workspace archive is created. The original archive now
passes the full temporary-path restoration test below. Private source/compiler
reconstruction and building at the original absolute paths remain untested.

The minimum disk free is **4,037,980,160 bytes**, and available RAM stays above
**6,090,018,816 bytes**. The final receipt and a separate live observation show
an empty, unmounted work directory and no mapped build processes. The >4 GB
retention reserve remains unchanged. This is component reproduction evidence;
SPI recovery, image assembly, programming and physical boot remain separate gates.


## Complete archive restoration test

[Restoration evidence](evidence/uboot-nvme-restoration-20261107.json) records an
actual fresh extraction of the original full workspace on the Ubuntu 22.04
builder. The separately reviewed, one-off controller authenticates the archive
and its observed file identity before creating a fresh private destination:
`/var/lib/a733-uboot-nvme-restore-20261107-01/tree`.

The archive contains **27,105** entries: 2,315 directories, 24,683 regular files
(**1,173,312,508 bytes**) and 107 symlinks. A 4 GiB tmpfs mounted with
`nodev,nosuid,noexec` held the restored tree. GNU tar extracted numeric owners,
modes, links and timestamps, compared the complete archive against the result,
and detected no differences. The restored path list matched every archive name,
and all **72** component-manifest hashes passed. Independent source review and
post-run archive/member/manifest verification pass. GNU tar checks restorable
metadata, including nanosecond mtimes; archived ctime is historical information,
not a timestamp that extraction can restore.

The controller command that completed was:

```sh
sudo python3 -I -B /var/lib/a733-uboot-restore-controller-20261107/restore.py
```

Its exact reviewed source, nine fixture tests and durable journals are retained
under `out/spi-workspace-restore-20261107` on codex, with a root-private controller
copy and journals on the builder. This one-off command refuses its existing
destination; it is an execution record, not a reusable operator repair command.
The archive and deployed images remain unchanged. A separate live postcheck
confirms matching archive identity/hash, no phase mounts or mapped processes,
and an empty temporary directory after unmount.

Minimum available RAM was **6,074,494,976 bytes**, and minimum disk free was
**4,009,005,056 bytes**. Existing swap configuration was preserved; tmpfs was
allowed to use that swap. No compilation or board operation ran. The original
absolute build directory remains empty and unmounted. This proves complete
archive recovery at a temporary location; it does not prove a rebuild from that
location, ROM recovery, SPI boot or KVM guest execution. The initial candidate
evidence remains immutable because the replay registry pins it; the newer
restoration record updates this specific verification result.

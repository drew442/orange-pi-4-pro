# Investigate ROM recovery before SPI programming

The host-side **version-only FEL probe** now builds in the frozen Ubuntu
builder and passes source, ELF and package inspection. It has not contacted the
board. [Evidence](evidence/fel-version-probe-20261107.json) records exactly what
was checked. This prepares identification; it does not establish USB ROM entry,
a RAM recovery loader, flash restoration or recovery after a bad SPI bootloader.

The [corrected host](evidence/corrected-host-boot-20261108.json), SSH, automatic time
and restored KVM guest now pass. The [earlier endurance run](endurance-run-20261108.md) lost SSH management.
Independent UART capture/control now works.
The [November 9 run](evidence/eight-hour-endurance-result-20261109.json) passes
the 8-hour workload gate. Defer USB recovery experiments and reboots until
required backups are complete. Keep both heatsinks attached.

## What the sources establish

The retained vendor manual v1.4 lists BOOT/RESET/PWR ON buttons. Its Android
instructions describe ADB through the blue dual-role USB port, after changing
Android's USB mode. Its section 3.36 installs Linux to NVMe and SPI. Those
sections do **not** give an independent ROM recovery button/power sequence or a
verified PC-side restoration procedure. ADB mode and ROM mode cannot be treated
as the same observation. The exact manual text hash and locators are retained
in the evidence; the source manual is linked from the
[board investigation](spi-boot-investigation.md).

The primary [A733 FEL proposal](https://github.com/linux-sunxi/sunxi-tools/pull/223)
remains open when checked on November 7. It identifies SoC ID `0x1903` and reports
work on Radxa Cubie A7A. Its proposed SRAM/loader support is separate from the
pinned upstream master used here. We do not substitute another board's loader,
DRAM setup or TF-A for the compatible Orange Pi components.

The author's [boot notes at a pinned revision](https://github.com/dlan17/a733/blob/0feb1a77f9b1914b421672048e853ff0de85fbbc/boot-fel.md)
describe entering FEL through serial input **after boot0 initializes DRAM**.
That depends on working boot0 and does not prove an entry path independent of
corrupted SPI. Forced ROM entry and a compatible RAM diagnostic still need their
own physical demonstration.

## Version-only protocol boundary

The transport source is
[sunxi-tools commit `34dfa20f`](https://github.com/linux-sunxi/sunxi-tools/tree/34dfa20f8284273085666436e4cac09af19646c8).
The small [frontend](../build/fel-version.c) uses `feldev_open` for an explicitly
selected USB bus/device, restricted to VID:PID **`1f3a:efe8`**, then checks the
returned `AWUSBFEX` signature and decoded A733 ID `0x1903`. It prints that response
and closes. Its 30-second alarm bounds stalled transport.

This uses FEL request **`0x001`, address 0, length 0**. USB framing includes OUT
packets; these are protocol requests, not target-memory uploads. The stock
`fel.c` frontend is excluded because its normal startup can run secure-boot
workarounds. Device listing/SID helpers are also excluded. The linker discards
unreachable code; independent `nm` and disassembly checks find no callable
memory-read/write, target execution, SID or flash command path in this binary.
It offers no command forwarding, `spl`, `uboot`, memory address or flash option.

The pinned master has no A733 SRAM entry. Its host-side generic lookup may print
an unknown-SoC warning; the frontend independently requires the exact ID, and
never uses the generic SRAM addresses. This is sufficient for the limited
identification request, subject to real hardware testing.

Upstream [FEL error-framing work](https://github.com/linux-sunxi/sunxi-tools/pull/259)
explains that error responses can cause timeouts/overflows in existing receive
code. The pinned transport also does not validate the eight-byte FEL status.
We have not applied a speculative protocol patch. A valid version response is
identification evidence only; an error or timeout must be returned for diagnosis,
not bypassed with arbitrary memory or execution commands.

## Frozen build and retention

Producer source `4903e67f1da90f6b2d598658ca67b950a8523538` ran:

```sh
sudo python3 -I -B \
  /var/lib/a733-fel-version-project-20261107-02/project/build/launch-fel-version.py \
  --project /var/lib/a733-fel-version-project-20261107-02/project \
  --upstream-bundle /home/drew/a733-fel-upstream-20261107.bundle \
  --tag compile-02
```

The same command with `--plan` passed first. Used tags refuse reuse. The launcher
binds all 610 project source records and a fixed complete upstream Git bundle,
checks the upstream commit/tree/archive, and reuses private UID/network,
read-only frozen builder, dropped capabilities, probe and cleanup checks.
The recipe authenticates 14 selected dependency versions from the frozen lock,
uses GCC 11 and fixed flags, and records compiler diagnostics. It does not install
software or run a valid USB address during packaging. `--help` and invalid-argument
checks return before USB opens.

Builder phase:
`/var/lib/a733-clean-replay-20261106/fel-version-compile-02`.
Native retained package:
`out/fel-version-20261107/work/project/out/fel-version-compile-02`.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `fel-version` | 35,280 | `ddd419d6356609b9a79b5a6d582215e0ee6a19b579a893ccfabdfe2d33c9a4f6` |
| `manifest.json` | 19,726 | `5a754827fdd53938f23d23bc3db79247667b58689ddeac0d8cf4c469ac0df263` |
| `sunxi-tools-source.tar` | 542,720 | `ed18650ce80d4c44b53f59fb55f9608253a35ae41f75ccc0863462c4b56eee77` |

Keep the entire package, including original source archive, frontend, recipe,
notices, license and dependency lock. The surrounding retention directory also
contains both authenticated Git bundles under `inputs/` for offline source
replays. The ELF is **x86-64 Linux**, for the USB host
PC; it is not an ARM host package or a boot image. Direct runtime dependencies
are `libusb-1.0.so.0` and `libc.so.6`; transitive/runtime host compatibility remains
separate from the pinned build environment. All 97 output-file hashes pass.

The first compilation attempt failed. Its original compiler stderr was not
retained, so no exact diagnostic is claimed. Source inspection found the missing
upstream `include/` search path. The launcher also incorrectly treated its own
sibling receipt creation as an input-parent change. Corrected code retains
compiler errors and uses the existing parent-identity guard while preserving
full input-leaf signatures/hashes. The failed phase remains retained; its host
inventory is unchanged and no mapped processes or mounts remain. Its input
continuity check failed as described above. The fresh corrected phase succeeds.

Initial free-space admission was 4,424,894,155 bytes. Runtime minima were
21,557,407,744 free bytes and 7,342,542,848 available RAM; final builder free was
21,557,407,744 bytes. All 842 host packages are unchanged, with no remaining
mapped process, phase mount or actual device node. The separate 22 GiB initial
guard for full NVMe composition remains unchanged. Privileged Ubuntu discovery
passes **1,614 tests without skips or failures**; relevant build logs and
independent review are linked in the evidence. No physical USB acceptance is
inferred from these results.

## Later identification and recovery evidence

Once the actual Orange Pi ROM-entry procedure and physical USB connection are
established, capture serial and host USB enumeration together. On the x86-64 host:

```sh
lsusb -d 1f3a:efe8
```

Select the actual bus/device from that listing; the probe requires ordinary
decimal integers without leading zeroes. For example, **only if the listing
shows bus 003, device 024**:

```sh
sudo /home/drew/orange-pi-4-pro/out/fel-version-20261107/work/project/out/fel-version-compile-02/fel-version 3 24
```

Return the exact button/power/media sequence, complete serial log, `lsusb` output,
probe output and exit status. Multiple devices must be identified individually;
the probe never selects the first matching device automatically. Do not use the
stock tool's listing/SID or RAM/flash commands as substitute checks.

Before SPI programming can be proposed, demonstrate entry independent of the
installed SPI bootloader, boot a compatible RAM recovery environment without
persistent writes, retain and verify the complete original flash off-device,
and inspect the actual backup-derived candidate/erase-unit plan. The operator has [authorized the identified SPI target](evidence/storage-write-authorization-20261108.json);
that authorization persists, while recovery and independent programmer review
remain prerequisites.
Version identification alone does not satisfy this gate. Actual restore testing,
SD-removed SPI boot and all [hardware acceptance](hardware-validation.md), including
real KVM guest execution, remain required.

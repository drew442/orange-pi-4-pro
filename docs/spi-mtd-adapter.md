# Linux MTD backend preparation

`scripts/spi-mtd-adapter.py` supplies Linux syscall callbacks for the
[SPI transaction engine](spi-unit-transaction.md). It is an API-only backend:
there is no command-line interface, device opening, source staging or complete
production programmer. Importing it performs no device or libc operation.
Independent host review passes; physical SPI programming remains unperformed.
The qualifying endurance workload is stopped; required backups and independent physical SPI recovery remain prerequisites.

## Descriptor and operation contract

`MtdAdapter(fd, *, guard, identity_check, emit)` borrows an already-open,
exclusively owned descriptor. The caller must keep that descriptor and all
aliases alive, unshared, in normal MTD file mode and free from concurrent flag
changes. Descriptor checks cannot detect close/reopen of the same inode or
defend against a hostile privileged caller.

The backend directly checks character device **90:0**, `O_RDWR` without
`O_APPEND`/`O_PATH`, stable descriptor identity/flags and complete NOR geometry:
type 3, 16,777,216 bytes, 65,536-byte erase units, byte writes, no OOB and the
writeable flag. It obtains fresh `MEMGETINFO` before each mutation. These checks
do not independently identify the board or SPI chip; the trusted identity
callback must additionally bind the expected A733 sysfs topology and
**XM25QU128C / JEDEC 204118**.

The callbacks passed to the engine are `read`, `erase`, `write`, `guard` and
`emit`. Reads require an exact nonempty range within the full 16 MiB device.
Erases permit only one aligned full unit among **0–25**. Writes require immutable
nonempty bytes, at most 4 KiB, within those units, without crossing a 4 KiB
boundary. The backend permits valid short positive writes; the engine advances
by that returned count. Zero, invalid counts and short reads fail closed.
Only the engine supplies authenticated desired bytes, preserves partial-unit
contents, enforces plan order and verifies the complete final image.

Every data operation rechecks the descriptor and trusted guard/identity callbacks.
Any operation or callback failure permanently invalidates the adapter. No further
call on that instance can resume the transaction. The caller must preserve the
engine's conservative partial receipt and obtain a fresh plan before a separately
reviewed recovery attempt.

## Kernel ABI and completion limits

The backend supports Linux 64-bit AArch64 and x86-64. It uses
`MEMGETINFO` `0x80204d01` with the 32-byte `mtd_info_user` layout and
`MEMERASE64` `0x40104d14` with two unsigned 64-bit fields. Mutation wrappers call
libc `ioctl` and `pwrite` once, including on `EINTR`; they do not allow Python's
automatic syscall restart to repeat an uncertain mutation. A raising operation
may already have altered flash. Normal `pwrite` is used rather than NAND/OOB
`MEMWRITE`.

The pinned vendor
[UAPI](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/include/uapi/mtd/mtd-abi.h)
and [mtdchar implementation](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/drivers/mtd/mtdchar.c)
establish these layouts, erase dispatch, write-return behavior and RW-close
synchronization. The driver provides no `.fsync` operation. The backend neither
calls `fsync` nor closes the borrowed descriptor. After full engine verification,
the caller invalidates the adapter, closes its own descriptor and records that
outcome; close errors leave the result uncertain. Readback and close alone do
not prove power-loss durability, ROM recovery or a successful physical boot.

## Remaining production integration

The [trusted caller and native capture](known-spi-programmer.md) now pass
independent code review and privileged Ubuntu tests. Their production recovery
pin remains unregistered, so write mode refuses before device opening. Before
constructing this backend for a real write, the caller must establish the exact board/descriptor and initial namespace,
exclusive use and quiescence, held authenticated original/candidate sources,
registered independently demonstrated ROM recovery, preserved original backup,
and durable off-board event capture. It must honor the operator's existing known
SPI authority and request a single yes/no only for unapproved targets or scope.
`guard` and `identity_check(fd)` must raise on any failed prerequisite; `emit`
must return normally only after durable off-board capture. Normal callback return
is trusted, not independent evidence that those prerequisites were satisfied.

Neither this backend nor a simulated successful engine transaction satisfies
those gates. The [ROM recovery investigation](rom-recovery-investigation.md),
registered physical recovery evidence, actual MTD operation/restoration and
SD-removed NVMe boot remain open. The A733, actual 2 GB SD, nominal 250 GB NVMe, minimal host and zero
routine SD-write requirements remain unchanged. Real KVM acceptance stays separate.

Host validation and independent probes are recorded in
[backend evidence](evidence/spi-mtd-adapter-20261109.json). No ARM runtime or actual
MTD erase/program result is claimed.

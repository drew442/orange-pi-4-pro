# SPI erase-unit transaction engine preparation

`scripts/spi-unit-transaction.py` is an API-only engine for a future independently
reviewed adapter. It imports only Python hashing/JSON support. It has no CLI,
file opening, device opening, physical erase ioctl or hardware programming route.
The active 24-hour real KVM workload must remain undisturbed. These host tests
and callback receipts always report `hardware_acceptance: false`.

The immutable source pins are the actual 16,777,216-byte
[original backup and candidate](spi-original-backup-20261108.md):

| Image | SHA-256 |
| --- | --- |
| Original | `dffab0dd410657cb30c7b2fd7f2586a4792e8472e58882b3532581f8111a646d` |
| Candidate | `83edcd7f448e8353163666016a6bae3e9b70d041a5e4d8454f48931839205e90` |

There is no production pin override interface. Tests patch module constants only
inside the test process to exercise a NOR bytearray model with nonblank preserved
bytes. Engine source authentication checks image bytes; it supplies no proof of
file provenance, a bound physical descriptor, target identity or recovery.

## Review and callback contract

`build_plan(original, candidate, current, direction, expected_current_sha256)`
accepts exact immutable `bytes`, authenticates both pinned sources and checks the
current image against its explicit caller-supplied whole-image hash. `direction`
is `install` or `restore`. An interrupted current image is allowed only with an
exact fresh hash pin and unchanged bytes outside `[0,237568)` and
`[262144,1654784)`. It produces a JSON-compatible plan containing old/new unit
hashes, differing byte counts and a SHA-256 of canonical JSON excluding the digest
field itself. Unit records remain in index order 0–25. The reviewed plan also
binds `execution_order`: install processes package units 4–25, then boot0 body
units 1–3, then header unit 0 last; restore processes units 0–25 in ascending
order. Execution follows that explicit order, including checks and skip events
for unchanged units. This order limits initial blank-SPI exposure while the
package is unfinished. It supplies no atomicity or ROM recovery guarantee.
Planning performs no actions.

Only complete 65,536-byte units 0–25 can be erased and reconstructed. The gap,
partial-unit tail and all units 26–255 must remain byte-for-byte unchanged.
`execute(plan, original, candidate, *, read, erase, write, guard, emit)` freezes
the reviewed plan, authenticates immutable source bytes and obtains a fresh full
current image. Rebuilding the plan must reproduce the reviewed plan exactly
before any mutation. The caller cannot silently replace its current-image pin.

The future adapter supplies five explicit callbacks:

- `read(offset, size)` returns exact immutable bytes or raises.
- `erase(offset, 65536)` completes one full unit or raises; a raising erase may
  already have altered the entire unit.
- `write(offset, data)` returns an integer accepted byte count from 1 through
  `len(data)`, or raises. Writes are at most 4 KiB and never cross a 4 KiB boundary.
  Short writes advance only by the returned count.
- `guard()` returns normally only if the adapter's required safety conditions
  still hold; otherwise it raises. It runs before each unit and write callback,
  and around final verification. Physical adapter guards should use cheap bound
  identity/quiescence checks, without rescanning the entire flash each time.
- `emit(event)` returns normally only after durable off-board event capture;
  otherwise it raises. Every event carries `hardware_acceptance: false`.

Before erasing a changed unit, the engine checks the guard and current unit hash,
then emits a conservative erase-attempt intent. An event failure prevents that
erase. It verifies erased bytes are all `0xff`, writes the complete desired unit
including preservation bytes, verifies every unit byte, checks the guard and
captures completion. Units already matching the desired bytes are skipped after
current-unit revalidation. Final verification compares all 16 MiB directly with
the desired image. Complete flash reads occur for initial and final verification;
unit processing reads only that unit.

Any callback failure, invalid result, mismatch or interruption stops execution
without another erase/write or an automatic recovery attempt. `TransactionError`
carries a partial receipt with the failing stage, active unit, attempted and
completed unit lists, bytes and `success: false`. An attempted erase is recorded
before calling erase, so its exception never implies untouched flash. Completed
units require both readback and captured completion. `mutation_possible` remains
true after any erase attempt, including a successful transaction.
`write_attempted_bytes` sums offered request lengths, including failed requests;
it may exceed unique programmed bytes under short writes. `write_confirmed_bytes`
sums only valid returned counts, so a raising write may have programmed additional
unknown bytes. These counts are conservative callback observations, not physical
write counters. A final event failure also prevents a success receipt.

## Physical adapter remains a separate gate

A [Linux MTD backend](spi-mtd-adapter.md) now implements bounded syscall callbacks
on a caller-owned descriptor and passes independent host review. It has no CLI
or device-opening path. The complete trusted programmer and its recovery,
quiescence, source and off-board durability integration remain unimplemented.

The future trusted programmer must independently enforce pinned A733/XM25QU128C target
identity and geometry, initial namespace and quiescence, authenticated staged
sources, registered ROM recovery proof, durable off-board capture and the required
single yes/no authorization workflow while honoring existing operator authority.
The engine cannot authenticate a supplied hash as an operator-reviewed observation
or establish that callbacks address the intended target. No routine SD writes,
additional host services or changes to the minimal host are introduced.

The vendor ABI is pinned to revision
`2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f`:
[`include/uapi/mtd/mtd-abi.h`](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/include/uapi/mtd/mtd-abi.h)
and [`drivers/mtd/mtdchar.c`](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/drivers/mtd/mtdchar.c).
A future adapter's `MEMERASE64` uses unsigned 64-bit start/length and ioctl
`0x40104d14`. Do not use NAND/OOB-oriented `MEMWRITE` for this NOR transaction.
The pinned mtdchar file operations provide no guaranteed `fsync` operation;
callback completion must use reviewed MTD write/close/readback semantics rather
than assuming `fsync(fd)` supplies persistence. This engine implements none of
those physical interfaces.

Run `python3 -m unittest discover -s tests -p 'test_spi_unit_transaction.py'` for
host NOR-model checks. They cover exact full-unit preservation, skip behavior,
short writes, interruptions, fresh restore, stale plans/pins, preservation refusal,
callback failure ordering and final full-image verification. They do not establish
actual MTD acceptance. The complete physical programmer, ROM recovery demonstration,
real SPI restoration and SD-removed NVMe boot remain unprepared or unperformed.
Physical KVM and hardware release acceptance remain with the coordinator, retaining
the A733, 2 GB SD and nominal 250 GB NVMe requirements.

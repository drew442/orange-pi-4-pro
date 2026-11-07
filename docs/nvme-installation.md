# Full-disk NVMe writer

`scripts/install-nvme.py` implements read-only preparation and an interactive
full-disk write. The writer is included in the current
[installer preparation image](installer-preparation.md). Native loop tests,
ARM software probes and independent review passed for the earlier implementation.
The [November 7 physical installation](evidence/installed-host-first-boot-20261107.json)
completed its write/readback and booted installed root A; management needs the
[machine-id repair](installed-host-repair-20261107.md). No reinstall is needed.

Current source simplifies confirmation and installation work as described below.
Published November 6 installer images retain the older prompts/readback behavior;
a refreshed image is required to use the new behavior.

Full-disk installation overwrites every partition, including guest storage.
Routine inactive-slot updates, rollback and backup/restore are separate work.
The writer never mounts filesystems, reboots, programs SPI or writes SD.
SPI-to-NVMe remains preferred after compatibility and recovery are demonstrated;
the immutable SD host bootstrap remains the fallback.

## Image eligibility and preparation

The existing published NVMe disk files contain fixture credentials/network
settings and cannot be installed with this CLI. `build/nvme-image.py` defaults to
`deployment_intent.for_installation=false`. Its `--for-installation` option
requires real administrator/network state and rejects known fixture keys/MACs,
documentation, loopback and multicast client CIDRs. Captured state configuration,
public-key fingerprint, machine ID, permissions and empty host-key directory
receive positive readback checks. This declares candidate intent; it proves
neither key ownership nor hardware success and authorizes no physical write.
Create a fresh per-installation state rather than editing a fixture manifest.

The [recorded operator inputs](evidence/operator-inputs-20261105.json) now supply
the real MAC, RSA3072 and Ed25519 keys, exact NVMe model/serial/capacity and
512/512 sector geometry. Preserve its compatible kernel/DTB/bootstrap and state UUID together.
The [provisioning guide](nvme-provisioning.md) describes the image layout and
builder inputs. The [personalized candidate](evidence/personalized-nvme-20261106.json) now passes
software inspection; use its [exact deployment procedure](deploy-personalized-20261106.md).

The writer accepts an already acquired regular `nvme.img` or `nvme.img.gz` and
its trusted build manifest. Gzip input requires both compressed and expanded
size/SHA-256 records in that manifest. The [bundle recipe and acquisition tool](nvme-distribution.md) provide verified
compressed delivery. Historical bundles remain fixtures; the personalized bundle now passes complete
verification and native LAN delivery. Board acquisition is recorded in the November 7 installation log. Keep payloads on identified
external storage or suitable RAM storage after measuring capacity; the 2 GB SD
is an immutable bootstrap, not payload staging. `/run` and `/tmp` in the
preparation image are each limited to 64 MiB.

Obtain the manifest's SHA-256 from the trusted builder/release record, through
an authenticated channel. Computing a digest of an unverified download alone
would not establish its provenance. Source, manifest and saved plan must not
reside on the target or any mapped descendant. Local disk filesystems and
explicit tmpfs/ramfs sources are supported; unknown source topology fails
closed. A declared digest is an integrity check, not a release signature.

## CLI and review plan

These help commands passed on the builder and in the packaged ARM runtime:

```sh
python3 -I -B scripts/install-nvme.py --help
/usr/bin/python3 -I -B /usr/lib/a733-installer/install-nvme.py --help
```

Use the first from a repository checkout and the second inside the preparation
image. Arguments are deliberately mandatory:

| Argument | Meaning |
| --- | --- |
| `prepare` / `apply` | Read-only review plan / explicit whole-device installation |
| `--image` | Verified regular `nvme.img` or `nvme.img.gz` |
| `--manifest` | Trusted image build manifest |
| `--manifest-sha256` | Independently obtained exact manifest digest |
| `--target` | Identified whole NVMe namespace; prefer its stable by-id path |
| `--expected-model` | Exact displayed model |
| `--expected-serial` | Exact displayed serial |
| `--expected-bytes` | Exact measured byte capacity, not nominal “250 GB” |
| `--plan` | Saved reviewed JSON plan; required by `apply` |
| `--verify-readback` | Optional full target readback after writing; default off |
| `--existing-data-policy` | `verified-backup` (default) or explicitly authorized `discard-authorized`; use the same policy for prepare/apply |

The exact physical target is now recorded and its data may be discarded:
`SPCC M.2 PCIe SSD`, serial `134807190FC700149277`, 256,060,514,304 bytes.
This authorization does not apply to SPI or another NVMe. Concrete `apply`
commands still require the completed authenticated installation candidate and
fresh reviewed identity/idle plan. Do not use the historical fixture payloads.

`prepare` authenticates the manifest and encoded image before describing the
target. It does not decompress the image. It requires a whole NVMe with matching
model, serial, capacity, logical/physical sector geometry and fixed layout.
Mounted/swap/mapped/read-only or ambiguous targets are refused. Source backing
is resolved through partitions and mapper slaves to reject target overlap.

The JSON plan binds source descriptor metadata and digests, target identity,
layout, deployment intent and first/last MiB hashes to a canonical plan digest.
Those samples cover target metadata, not all old disk contents. A plan is an
observation, not a lock, backup or authorization. Save it in RAM or verified
off-target storage. Encoded authentication reports byte progress; it does not scan the expanded disk.

## Authorization and write protocol

Before erasure, identify the exact device and obtain the user's explicit
approval. Existing authorization persists for the matching recorded drive. The
CLI creates no backup and cannot independently verify one.

The data policy remains bound into the plan: use `discard-authorized` when the
operator permits discarding existing data, otherwise `verified-backup` confirms
an independently verified off-device backup. The tool prints the exact path,
model, serial, capacity and sector sizes, then asks **Erase and install? [y/N]**.
Only `y` or `yes` proceeds; no/empty/EOF declines. There are no typed hashes or
confirmation phrases. stdin/stderr must be interactive terminals.

`apply` matches the current target/source metadata to the saved plan, obtains
that confirmation, then authenticates encoded bytes before the first write.
The default path decompresses only while writing and hashes that same stream;
expanded size/hash and gzip CRC must pass before success. Malformed authenticated
input can therefore leave a partial target: retain the failure report and use
recovery rather than booting it.

It checks the held descriptor's device number, size, sector sizes and writable
status, and takes a nonblocking advisory lock. Identity and metadata are checked
again while the exclusive descriptor is held. Quiesce other privileged raw-device
users; advisory locks do not constrain programs that ignore them. Run from the
preparation environment, which starts no automounter.

Every expanded byte is written, including zeros in sparse image holes. Input is
hashed during writing. The writer synchronizes, invalidates the block
cache and requests a kernel partition-table refresh. Full target readback is
skipped by default. Add `--verify-readback` to `apply` for troubleshooting; this
reads/hashes the entire target and reports progress. Success requires selected steps and source
cleanup to complete. It performs no automatic boot or activation.

Errors, Ctrl-C and best-effort SIGTERM handling retain conservative
`target_modified`, attempted/written/verified byte counts and failure status.
A write attempt sets modification state before the syscall, even if it fails.
SIGKILL, power loss or a lost console may prevent any final report. If a write
phase began without a complete success record, treat the device as possibly
partial and keep booting recovery media. Do not infer success from a byte count
or a process exit alone.

For an interrupted installation, retain the original verified backup and source
manifest, inspect the device from recovery, generate a fresh review plan, and
obtain renewed exact-target confirmation before a complete rewrite or restore.
The loop test demonstrates explicit full rewrite after interruption; physical
power-cut recovery and backup restoration remain release gates. Existing guest
disks must be backed up before any full-disk operation.

## Software evidence and limits

The opt-in builder test accepts no device arguments:

```sh
sudo python3 tests/verify_nvme_writer_loop.py
```

It creates fresh private 128 MiB files on fixed storage, attaches only those as
loops, checks their sysfs backing identities, waits for builder udev probes,
and acquires the same exclusive/advisory access. Production descriptor ioctl
results are checked against independent sysfs geometry. A second exclusive
claim must fail. Complete copying replaces nonzero old bytes and zero ranges,
checks full loop/backing-file hashes and kernel MBR readback. Further cases
exercise interruption followed by explicit rewrite, and intentional readback
corruption that must fail before partition refresh. Owned loops are checked as
detached and private files removed.

[Native loop evidence](evidence/nvme-writer-loop-20261104.json),
[state readback](evidence/nvme-writer-state-20261104.json) and the
[writer-enabled ARM probes](evidence/writer-arm-20261104.json) record results.
The ARM probe checks isolated CLI startup, fixture refusal before target access,
and a regular-file byte-engine run with block flush/refresh callbacks stubbed.
Native loop tests exercise actual Linux block callbacks. The earlier implementation completed the physical installation above. The new
yes/no/default-no-readback behavior has software checks and independent review;
it has not yet run on the board. Neither test satisfies board, firmware,
SD-write, installation, rollback or actual KVM acceptance.

The block operations follow [Linux UAPI definitions](https://raw.githubusercontent.com/torvalds/linux/v6.6/include/uapi/linux/fs.h)
and [Linux exclusive block-device open semantics](https://man7.org/linux/man-pages/man2/open.2.html).
Physical driver behavior, power-loss durability and the plan's complete
[hardware acceptance](hardware-validation.md) still require measured evidence.

Default successful reports have `readback_requested=false`, `bytes_verified=0`
and no `readback_sha256`; `source_sha256` records the stream written. Opt-in
readback reports actual verified bytes/hash. Neither result substitutes for the
separate release hardware validation procedure.

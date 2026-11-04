# Full-disk NVMe writer

`scripts/install-nvme.py` implements read-only preparation and an interactive
full-disk write. The writer is included in the current
[installer preparation image](installer-preparation.md). Native loop tests,
ARM software probes and independent review passed. No physical NVMe installation
has been performed or authorized; the deployment procedure remains unvalidated
on the A733.

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

Before producing a real candidate, obtain the board's actual wired MAC, the
operator's Ed25519 public key, exact SSD model/serial/byte capacity and sector
sizes. Preserve its compatible kernel/DTB/bootstrap and state UUID together.
The [provisioning guide](nvme-provisioning.md) describes the image layout and
builder inputs. No deployable candidate using these real inputs exists yet.

The writer accepts an already acquired regular `nvme.img` or `nvme.img.gz` and
its trusted build manifest. Gzip input requires both compressed and expanded
size/SHA-256 records in that manifest. The [bundle recipe and acquisition tool](nvme-distribution.md) provide verified
compressed delivery. Existing published bundles remain fixtures; a real
per-installation candidate and board network acquisition remain pending. Keep payloads on identified
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

No device-specific `apply` command is published here: the actual target and
user authorization have not been supplied. Exact deployment commands must be
prepared and validated for that identified hardware. Use the read-only planner
and [diagnostic inventory guide](diagnostic-image.md) first.

`prepare` authenticates the entire held source and manifest, including expanded
gzip bytes, before describing the target. It requires a whole NVMe with matching
model, serial, capacity, logical/physical sector geometry and fixed layout.
Mounted/swap/mapped/read-only or ambiguous targets are refused. Source backing
is resolved through partitions and mapper slaves to reject target overlap.

The JSON plan binds source descriptor metadata and digests, target identity,
layout, deployment intent and first/last MiB hashes to a canonical plan digest.
Those samples cover target metadata, not all old disk contents. A plan is an
observation, not a lock, backup or authorization. Save it in RAM or verified
off-target storage. Whole-image authentication can take several minutes.

## Authorization and write protocol

Before erasure, identify the exact device and obtain the user's explicit
approval. Complete and independently verify off-device backups first. The CLI
creates no backup and does not validate a backup archive; its first confirmation
records the operator's assertion.

`apply` repeats source/target preparation and requires an exact match with the
saved plan. It requires root and interactive stdin/stderr terminals. At its
first prompt enter exactly `BACKUP VERIFIED`. At the second enter
`ERASE SERIAL BYTES PLAN_SHA256`, replacing the last three values with the
reviewed plan's exact serial, byte count and digest. There is no `--yes`, force,
loop-device or environment-variable authorization bypass.

After confirmation it rechecks target identity, busy indicators and sampled
metadata, then opens the canonical whole device with exclusive block access.
It checks the held descriptor's device number, size, sector sizes and writable
status, and takes a nonblocking advisory lock. Identity and metadata are checked
again while the exclusive descriptor is held. Quiesce other privileged raw-device
users; advisory locks do not constrain programs that ignore them. Run from the
preparation environment, which starts no automounter.

Every expanded byte is written, including zeros in sparse image holes. Input is
hashed again during writing. The writer synchronizes, invalidates the block
cache, hashes the complete target through the held descriptor and only then
requests a kernel partition-table refresh. Success requires all steps and source
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
Native loop tests exercise actual Linux block callbacks. Production whole-NVMe
selection/authorization has mocked regressions and source review; it has not
been executed against physical NVMe. Neither test satisfies board, firmware,
SD-write, installation, rollback or actual KVM acceptance.

The block operations follow [Linux UAPI definitions](https://raw.githubusercontent.com/torvalds/linux/v6.6/include/uapi/linux/fs.h)
and [Linux exclusive block-device open semantics](https://man7.org/linux/man-pages/man2/open.2.html).
Physical driver behavior, power-loss durability and the plan's complete
[hardware acceptance](hardware-validation.md) still require measured evidence.

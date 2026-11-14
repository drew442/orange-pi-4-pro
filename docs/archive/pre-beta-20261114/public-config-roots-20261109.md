> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Resolver-readable host root components

Two fresh A/B root constructions now contain the reviewed public-directory
permission fix. They are **components awaiting distribution integration and
physical verification**, not new installation images. The installed
host and historical full images retain the [resolver finding](../../evidence/management-runtime-exposure-20261109.json).

[Artifact evidence and exact pins](../../evidence/public-config-roots-20261109.json)
bind source `4f40843365566ae4ada9b7377b75bba3f40c3360`, all 845 committed files,
the existing pinned 221-package time-enabled userspace, kernel and modules.
No packages were installed. Keep the current external protected BOOT and VGIC
DTB; the root payload's historical baseline DTB provenance is unchanged.

## Construction and inspection

The first construction used the existing source-generic controller:

```sh
sudo python3 -I -B \
  /var/lib/a733-public-config-roots-20261109-01/project/build/launch-host-roots.py \
  --project /var/lib/a733-public-config-roots-20261109-01/project \
  --tag timesync-public-dirs-01 \
  --userspace /var/lib/a733-clean-replay-20261106/userspace-timesync-account-dates-01/work/export-timesync-result/rootfs.tar.gz \
  --diagnostic /var/lib/a733-bootstrap-inputs-20261107
```

The repeat used `timesync-public-dirs-02`. Both tags are consumed; a later
construction must use a fresh tag. Plans and terminal unit limits are retained.
The first job had a two-CPU quota, the repeat one CPU, each with a 3 GiB memory
ceiling. Resource guards were unchanged; exact admission and sampled minima
are in the evidence.

Each phase is under
`/var/lib/a733-clean-replay-20261106/host-roots-timesync-public-dirs-NN`, with
payloads under `work/project/out/host-payload-timesync-public-dirs-NN`.
Construction 01 is selected for subsequent integration:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Root A | 4,294,967,296 | `299b1f2f74e11c71c89b9ee249dfd43bdc741ac3e90addc763a9626bbbb5aed6` |
| Root A gzip | 143,471,704 | `ddbc237a5c447eeacfcd25588f2542c778656d113eb55144c685616edd2caf97` |
| Root B | 4,294,967,296 | `48b9876f72f28d4a10f7458dd81a96b95da00d13ae00783b39f7c30067fa94f7` |
| Root B gzip | 143,471,589 | `74679187340549ddcb7f50fc88aced8d5c14d6d316c76b458c0e813ddbbe1203` |

The root-payload manifest is 32,613 bytes, SHA-256
`1f82b9e85c5fbcbac65af695b699ee0048fb397faeba2e3eeb652eeb584adb6c`.
It is distinct from a whole-drive installation manifest. These 4 GiB files are
NVMe root components; the existing 256 MiB SD bootstrap still fits the actual
1,977,614,336-byte card.

Independent Sol/high reviews authenticate all 86 payload artifacts and complete
gzip expansions for both constructions. Direct ext4 extraction verifies 46
files per slot, including the complete 40-file maintenance closure. Compared
with the previous roots, exactly four file contents change per slot: the runtime
helper, its maintenance copy, the maintenance package descriptor and root
payload marker. Package-state identity, profiles, namespace and other non-mtime
metadata remain compatible.

The complete producer-captured expected trees contain 16,368 paths per slot.
The repeat has matching contents and non-mtime metadata, with 465 different
mtimes per slot. Actual filesystem creation/check/write times, directory hash
seeds and checksums also differ. Raw filesystem byte reproducibility remains
unproven; these are two constructions on one qualified builder.

The separate full filesystem reader is bound to each producer receipt and
checks every expected path, regular content, ownership, modes, links, device
metadata and raw attributes. It excludes atime, ctime and crtime and preserves
source mtimes to whole seconds. All four constructed filesystems now pass.
The first concurrent attempt refused an input
ancestor-directory change; its failure is retained. Subsequent checks serialize
ancestor-directory creation and retain the original strict guard. These are
regular-file inspections on the builder, not physical media readback during
installation.

## Integration still required

Prepare new matched A/B maintenance candidates using the new root-payload
manifest. Do not relabel the [earlier candidates](../../matched-host-updates-20261109.md).
The default A selector in the existing full distribution authenticates root
marker `e1cc…`, whereas these roots require A marker `286494…` or B marker
`521e86…` (complete pins in the evidence). A coherent full image must update
STATE's selector together with both roots, preserving STATE UUID
`360dfb39-f49f-487e-b307-0bb887c854c5` and the current compatible protected BOOT.
Replacing only the root partition payloads would leave an invalid boot pair.

After retaining endurance evidence and installing corrected roots, verify the
resolver drop-in is readable as `systemd-resolve`, global LLMNR/mDNS are disabled,
TCP/UDP 5355 wildcard listeners are absent, and DHCP/DNS/NTP plus pinned SSH
continue working. Retain exact SD zero-write counters. Source and artifact
reviews do not establish that physical result or release acceptance.

# Production-size personalized time-enabled state

The [construction and readback evidence](evidence/production-host-state-20261107.json)
records an actual **8 GiB** journaled state component, built in the frozen Ubuntu
22.04 namespace and checked by complete read-only filesystem readback. It uses
the real operator configuration and both administration keys. This is one
construction; the [matching immutable bootstrap](production-host-bootstrap.md)
now also passes construction and software inspection. Full NVMe composition,
repeat-build validation and physical deployment remain open. Published deployment images are unchanged.
Use the [existing-install repair](installed-host-repair-20261107.md) for the board.
This component is not an SD or whole-drive installation image.

## Inputs and construction

Producer revision `e68f03531e3fab56deb5c883ccb7a5dca13baff8` authenticates all
**583** tracked source records and the existing [production root receipt](production-host-roots.md).
The selected root A marker is independently bound to that root's previously
verified expected tree. The explicit `timesync-20261106` archive contains 221
configured packages; the metadata backend is `fakeroot-raw-xattrs-v1`.
The read-only capsule contains authenticated userspace, management configuration,
both public keys, a Git bundle and generated entry points. Source, capsule,
operator inputs and original root inputs are rechecked before success publication.

The executed command on `drew@build.drewnet.online` was:

```sh
sudo python3 -I -B \
  /var/lib/a733-host-state-project-20261107-02/project/build/launch-host-state.py \
  --project /var/lib/a733-host-state-project-20261107-02/project \
  --tag timesync-02 \
  --userspace /var/lib/a733-clean-replay-20261106/userspace-timesync-account-dates-01/work/export-timesync-result/rootfs.tar.gz \
  --config /home/drew/orange-pi-4-pro-build/operator-inputs-20261105/management.json \
  --admin-keys /home/drew/orange-pi-4-pro-build/operator-inputs-20261105/admin.pub \
  --roots-receipt /var/lib/a733-clean-replay-20261106/host-roots-timesync-01/receipt.json \
  --roots-receipt-sha256 dfcebe53d55dfcef53fcfee4bb88bd0db6a751ac3257163ceed742a6fb8904c4 \
  --state-uuid fc8bbef6-9385-4ddb-90ba-e41eaccc035b
```

The same command with `--plan` passed first. Plans inspect committed source and
policy without creating a phase or accessing physical build inputs. The used tag
now refuses reuse. A repeat needs a fresh tag, unique installation identity,
committed source and sufficient capacity; never overlay an attempt. An earlier
preflight attempt failed before phase creation because it looked for slot UUIDs
in the root receipt rather than its pinned payload manifest. The corrected
contract retains manifest UUID validation and has a regression for the actual
receipt shape. That failure is retained in the evidence.

The launcher uses the existing frozen namespace, private UID/network, capability
drops and firmware mask. Original root payloads are bound read-only. Root selection
uses a temporary regular-file snapshot, removed before state creation. Fakeroot
spans the entire state recipe; no real device node or physical storage is used.
The state remains 8,589,934,592 bytes with its journal enabled and writable
filesystem policy. Its sparse allocation is 83,042,304 bytes; the compressed
component is 10,009,310 bytes. Exact hashes are in the evidence.

## Identity and complete readback

Fresh state UUID `fc8bbef6-9385-4ddb-90ba-e41eaccc035b` differs from the installed
board's UUID. A fresh nonzero machine-id is root-owned `0644`, with private
`0700` identity directory. Both canonical administration keys and management
configuration remain `0600`; SSH host-key storage is empty. The clock directory
is empty, `0755`, owned by 996:996. Never clone this per-installation identity to
another host. Public-key-only SSH policy and actual access still need board tests.

The independently reviewed verifier authenticates its SHA-pinned sibling reader,
producer receipt, all 38 manifest artifacts, capsule inputs and stable expected
tree. It reads through held regular-file descriptors with read-only `debugfs`;
it does not mount, repair or write images. The executed command was:

```sh
sudo python3 -I -B /var/lib/a733-state-readback-controller-20261107/verify-host-state.py \
  --receipt /var/lib/a733-clean-replay-20261106/host-state-timesync-02/receipt.json \
  --receipt-sha256 3abeb7ba014eae05acebfd7af3b75dab7c4c58999b96d8c752163bb96a2ccd33 \
  --output /var/lib/a733-host-state-readback-20261107-01
```

That run completed: **1,172 paths**, 1,115 unique regular inodes totaling
7,337,572 bytes, three symlinks, no device records and no raw attributes. Only an
empty, root-owned `0700` `lost+found` is additional. Every file byte, link,
ownership and permission matches. The expected source/staging root is `0700`;
the pinned creator initializes the actual filesystem root as root-owned `0755`.
The verifier enforces that narrow documented transformation. Child mtime is
compared at whole-second resolution, and root mtime comes from the actual
superblock. Atime, ctime and crtime are outside the comparison. Complete image
readback does not independently prove that producer staging implements the
desired policy; source review covers that separate question.

Native records, expected trees and all 37 non-raw state files are retained in
`out/host-state-timesync-02-20261107`; full readback logs are in
`out/host-state-readback-20261107-01`. The raw image remains on the builder.
No physical SSH, NTP, guest execution, rollback or endurance result is established.

## Validation and next integration

Ubuntu unittest discovery passes 1,513 cases with 112 skips and no failures;
five shell suites and four diagnostic assembly cases also pass. Independent
source review precedes the run; separate artifact review passes source/file pins,
compressed expansion and the readback evidence. These builder checks do not
change the operator's optional-readback installation procedure.

The retained state work tree allocates about 104 MB. Initial admission requires
19,095,663,082 bytes; the maximum of sequential selection/state peaks is used,
plus authenticated capsule and Git staging. Final composition still requires
the unchanged **22 GiB** free-space guard. Recheck real allocation and use verified
retention/staging before composition; the approved LV expansion does not imply
that every later phase fits simultaneously. The matching immutable bootstrap now passes; next compose the full NVMe
candidate and repeat from clean inputs. Physical
acceptance continues separately with the existing installed-host repair.

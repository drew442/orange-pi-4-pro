# State matched to the corrected roots

A fresh **8 GiB** personalized state component now passes construction and full
read-only filesystem verification against the [corrected roots](production-host-roots-runtime-fixes.md).
Independent artifact review also passes. This is a component for a future matched
generation, not an SD or whole-drive installation image. Keep the current NVMe
installation and continue the [VGIC SD trial](next-host-boot-20261108.md).

The accepted state UUID is `360dfb39-f49f-487e-b307-0bb887c854c5`. Its newly
generated machine-id differs from the previous states; SSH host-key storage
remains empty for first-boot generation. Both authorized administration keys,
hostname `a733-host`, MAC `10:f4:ae:90:5e:57`, DHCP and unrestricted IPv4 source
policy remain unchanged. Never clone this identity to another installation.
Clock storage is empty, owned by 996:996 with mode 0755; machine-id is mode 0644.
Administration-key and configuration files remain mode 0600.

## Construction and full readback

The [registered controllers](state-runtime-fixes-controller.md) retain historical
predicates and statements, adding only their declared parent bindings, UUID
exclusions and strict proof-schema checks. Producer source is
`6dbf6d97568f2c6eeef3d0a326552358fe4b3d9a`, authenticating all 684 tracked files.
A committed CLI plan and all **1,724 privileged tests** passed before this build:

```sh
sudo python3 -I -B \
  /var/lib/a733-state-runtime-fixes-project-20261108-02/project/build/launch-host-state-runtime-fixes.py \
  --project /var/lib/a733-state-runtime-fixes-project-20261108-02/project \
  --tag timesync-runtime-fixes-02 \
  --userspace /var/lib/a733-clean-replay-20261106/userspace-timesync-account-dates-01/work/export-timesync-result/rootfs.tar.gz \
  --config /home/drew/orange-pi-4-pro-build/operator-inputs-20261105/management.json \
  --admin-keys /home/drew/orange-pi-4-pro-build/operator-inputs-20261105/admin.pub \
  --roots-receipt /var/lib/a733-clean-replay-20261106/host-roots-timesync-runtime-fixes-01/receipt.json \
  --roots-receipt-sha256 12a0bb104f29935f75be49e936d120e410a335ee032a3dd486e0f3cf112b6ba2 \
  --state-uuid 360dfb39-f49f-487e-b307-0bb887c854c5
```

The consumed phase is
`/var/lib/a733-clean-replay-20261106/host-state-timesync-runtime-fixes-02`,
with payload under `work/project/out/host-state-timesync-runtime-fixes-02`.
Another construction needs a fresh tag and identity; retain consumed phases.

```sh
sudo python3 -I -B \
  /var/lib/a733-state-runtime-fixes-project-20261108-02/project/build/verify-host-state-runtime-fixes.py \
  --receipt /var/lib/a733-clean-replay-20261106/host-state-timesync-runtime-fixes-02/receipt.json \
  --receipt-sha256 a25aa352b42bc49c13218d61587ea68636e83d21b77a34af52c6fd68db12ae88 \
  --output /var/lib/a733-host-state-runtime-fixes-readback-20261108-02
```

The full reader authenticates all 38 payload artifacts, then checks all 1,172
paths, 1,115 unique regular inodes containing 7,337,572 bytes, three symlinks,
ownership, modes and attributes. It also checks the actual parent root proof,
selector, canonical public keys and source inputs. No image was mounted or
repaired. State is writable with its journal enabled. The creator's root-mode
and whole-second mtime policy remain explicit; atime, ctime and crtime are
outside filesystem comparison.

Compared with the previous accepted state, the namespace and metadata match.
Only the state marker, selector and machine-id contents differ, with 15
changed modification times (12 additional mtime-only differences). Independent inspection verifies both complete streams,
all source/payload inputs and the critical paths directly from ext4.

The raw image is 8,589,934,592 bytes; gzip is 10,009,284 bytes. Exact pins and
receipts are recorded in the [evidence index](evidence/production-host-state-runtime-fixes-20261108.json).
Personalized identity and filesystem creation metadata intentionally differ
between builds; no byte-identical personalized-state claim is made.

## Retained first attempt

The first construction at source `6665d0f`, tag `timesync-runtime-fixes-01`,
succeeded, but its reader failed before completing filesystem verification.
The coordinator's registered root record flattened `expected_trees` pins where
the historical state reader required complete expected/stability/member records.
That attempt and UUID `a55f9ea1-d255-4556-b578-9aa9e9d72726` remain retained and
are not accepted for installation.

A separate [state-binding record](evidence/production-host-roots-runtime-fixes-state-binding-20261108.json)
preserves the original root evidence and all actual artifact identities while
restoring the authenticated producer proof shape. Both controllers now check
that schema after authentication and before construction/readback. Tests use
the actual committed parent data; independent review also exercised the actual
retained parent proof and selector. The [failure record](evidence/state-runtime-fixes-readback-failure-20261108.json)
remains separate from the successful second attempt. No old predicate was
removed or failure relabeled as a pass.

## Resources, retention and remaining work

Initial admission required 19,107,968,964 free bytes. Sampled minima were
18,677,731,328 free bytes and 7,261,958,144 available RAM bytes during execution;
runtime floors remain three billion disk bytes and one billion available RAM
bytes. The 842-package host inventory is unchanged, with no mapped processes or
phase mounts left. Construction ended with 19,201,351,680 free builder bytes;
full NVMe composition still requires capacity recovery under its unchanged
22 GiB admission guard.

Native records, all non-raw payload files including gzip and complete reader
logs are in `out/state-runtime-fixes-20261108`, with successful files under
`producer-02`, `payload-02` and `readback-02`. The raw image remains on the builder.
The `-01` paths retain the failed reader attempt and must not be confused with
the accepted component.

The [matched bootstrap](production-host-bootstrap-runtime-fixes.md) now passes
construction and independent inspection. Matched NVMe integration remains
required. Historical registrations
cannot be relabeled. Current published images, installed state and SPI are
unchanged. Physical boot, automatic time, the machine-id unit mask, complete KVM
guest checks, rollback/recovery and independent deployment remain open.

# Registered state controller for corrected roots

`build/launch-host-state-runtime-fixes.py` and
`build/verify-host-state-runtime-fixes.py` register one new parent: the
[corrected production roots](production-host-roots-runtime-fixes.md), produced
at `f40c151fb8957db41ede80fbd758880a5d901efe`. These adapters prepare a fresh
8 GiB STATE component. They do not establish construction, installation
readiness or hardware acceptance. Historical controllers and repeat adapters
retain their original registrations.

Each adapter holds and SHA-authenticates its historical template and physical
ancestors through publication. Exactly five launcher and four reader AST string
constants change; missing or repeated anchors refuse execution. Source closure,
root receipt, manifest, root A image, independent parent proof, operator inputs,
221-package archive and clock ABI checks remain active. The additional
`runtime_fixes_controller` record binds the historical template and exact new
parent evidence. The committed evidence at `80e9ba6` is 17,141 bytes, SHA-256
`12c45544ac379a0ca11312f833f2e49333b0cf94dc55156a280a18044ba8003d`,
canonical JSON SHA-256
`2c04a75d5239224b80a721442e88ea872ccd63d18aaae841539d007697302971`,
Git blob `63ee6c9c17dbca0f434feaef010c7a0fa630f070`.

Both adapters require a canonical, nonzero fresh state UUID. They reject both
root-slot UUIDs and all three older installations/candidates:
`00fd936c-49fb-4fea-87c8-928b9c1599e0`,
`fc8bbef6-9385-4ddb-90ba-e41eaccc035b`, and
`feaaee34-d782-4c18-bd04-1029540ab26a`. The recipe generates a fresh machine-id,
leaves SSH host-key storage empty, preserves both authorized key pins and keeps
empty clock storage owned by 996:996. Do not clone the resulting identity.

## Committed planning and construction

Commit the independently reviewed adapters before staging a clean checkout at
`/var/lib/a733-state-runtime-fixes-project-20261108/project`. The source launcher
requires exact committed bytes. Run the read-only CLI plan first:

```sh
STATE_UUID=$(python3 -I -B -c 'import uuid; print(uuid.uuid4())')
sudo python3 -I -B \
  /var/lib/a733-state-runtime-fixes-project-20261108/project/build/launch-host-state-runtime-fixes.py \
  --project /var/lib/a733-state-runtime-fixes-project-20261108/project \
  --tag timesync-runtime-fixes-01 \
  --userspace /var/lib/a733-clean-replay-20261106/userspace-timesync-account-dates-01/work/export-timesync-result/rootfs.tar.gz \
  --config /home/drew/orange-pi-4-pro-build/operator-inputs-20261105/management.json \
  --admin-keys /home/drew/orange-pi-4-pro-build/operator-inputs-20261105/admin.pub \
  --roots-receipt /var/lib/a733-clean-replay-20261106/host-roots-timesync-runtime-fixes-01/receipt.json \
  --roots-receipt-sha256 12a0bb104f29935f75be49e936d120e410a335ee032a3dd486e0f3cf112b6ba2 \
  --state-uuid "$STATE_UUID" \
  --plan
```

Keep the selected UUID with the plan. After checking actual builder admission,
repeat that exact command with the same UUID and without `--plan`. Planning
performs no input or capacity inspection and does not prove admission.
Initial RAM remains 2 GiB; free-disk admission remains the exact computed maximum
of the 5 GiB selector peak and state recipe peak, plus capsule and Git staging.
It is about 19.1 GB, with the exact figure dependent on committed Git closure.
Runtime floors remain 3,000,000,000 free disk bytes and 1,000,000,000 available
RAM bytes. The previously measured 19,751,702,528 builder free bytes precede
new project/capsule staging; native space is also limited. Remeasure both and
account for transfers before execution. Do not lower guards or grow storage.

The new phase is
`/var/lib/a733-clean-replay-20261106/host-state-timesync-runtime-fixes-01`;
payload output is `work/project/out/host-state-timesync-runtime-fixes-01`.
An existing phase refuses reuse. Retain failures and use a new tag and identity
for a later attempt; do not overlay retained evidence.

## Independent readback

After a successful construction, independently record the exact SHA-256 of its
new `receipt.json` as `STATE_RECEIPT_SHA256` and run:

```sh
sudo python3 -I -B \
  /var/lib/a733-state-runtime-fixes-project-20261108/project/build/verify-host-state-runtime-fixes.py \
  --receipt /var/lib/a733-clean-replay-20261106/host-state-timesync-runtime-fixes-01/receipt.json \
  --receipt-sha256 "$STATE_RECEIPT_SHA256" \
  --output /var/lib/a733-host-state-runtime-fixes-readback-20261108-01
```

Use a new output directory. The unchanged full reader authenticates the complete
artifact closure, independent parent root proof, selector, metadata, contents,
links and attributes without mounting or repairing images. It verifies writable
journal policy, new identity, empty host-key/clock storage and both operator keys.
Preserve construction, readback and independent review evidence separately.

Matched bootstrap and full NVMe bindings require this new state's evidence;
older registrations cannot be relabeled. No board write, delivered-image change
or installation is part of these commands. Physical boot, automatic time,
masked-unit behavior, full KVM guest acceptance, recovery and zero routine SD
writes remain open gates; continue the separate reviewed VGIC SD trial on the
existing installation.

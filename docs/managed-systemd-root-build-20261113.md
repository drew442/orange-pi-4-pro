# Reproduce the repaired managed root construction

Use frozen source `4334a2229c9b816c4c5826cd612da1c851362146` on the declared
Ubuntu 22.04 builder `drew@build.drewnet.online`. This procedure uses retained
inputs and a fresh source checkout/phase. It does not provision a fresh outer OS
or establish physical deployment/KVM acceptance. Two coordinator constructions and an independent fresh-checkout reproduction
match all 133 manifest-listed artifacts and complete gzip expansions. Independent
filesystem/semantic and historical-reader checks pass.
[Build evidence](evidence/managed-systemd-root-builds-20261113.json),
[independent reproduction](evidence/independent-managed-systemd-root-reproduction-20261113.json),
[artifact review](evidence/managed-systemd-root-artifact-review-20261113.json).

The source bundle on codex is
`out/managed-systemd-root-candidate-20261113-01/source.bundle`, 4760790 bytes,
SHA256 `6c84e5ecc98c5f375c3f3a5b82cfa7cf0938625177837e12efbad26acef93d7d`.
The same file is retained on the builder under
`/var/lib/a733-managed-systemd-root-candidate-20261113-01/source.bundle`.
Source tree is `9777b7057acfe88d3c42e915ad3e0be071becb42`, 1144 tracked files.
Authenticate the bundle before cloning and confirm the whole clean checkout.

Exact outer inventory is the committed 849-package
`configs/builder-host-packages-20261113.lock.tsv`, SHA256
`b34b70a879cbbf75f818708275c57ef0aef3ae635b9ae71a8005e2b29a8490b8`.
The frozen replay builder still uses its historical 842-package lock. Preserve
both historical locks/template and all input archives.
[Package provenance](evidence/outer-builder-849-20261113.json),
[validated source](evidence/managed-systemd-outer849-validation-20261113.json).

## Fresh checkout and declared inputs

Run on the builder in a root shell. Keep the independent workspace separate from
both producers; refuse an existing path. Use the authenticated bundle above.

```bash
set -euo pipefail
umask 077
A733_REPRO=/var/lib/a733-managed-systemd-root-reproduction-20261113-01
A733_BUNDLE=/var/lib/a733-managed-systemd-root-candidate-20261113-01/source.bundle
test ! -e "$A733_REPRO"
mkdir -m 0700 "$A733_REPRO"
git clone --no-checkout "$A733_BUNDLE" "$A733_REPRO/project"
git -C "$A733_REPRO/project" checkout --detach 4334a2229c9b816c4c5826cd612da1c851362146
```

The userspace input is
`/var/lib/a733-clean-replay-20261106/userspace-timesync-account-dates-01/work/export-timesync-result/rootfs.tar.gz`,
138093095 bytes, SHA256 `0ead62c982344bfa3a796027e2f07ce0d8402e7033122707e01a9a3f8860f248`.
The diagnostic capsule remains `/var/lib/a733-bootstrap-inputs-20261107`; its
individual pins are enforced by the committed launcher policy. Root provenance's
diagnostic DTB is not a replacement for the working NVMe BOOT/VGIC DTB.

## Plan, then construct regular files

The initial plan checks syntax/source contracts. Actual execution additionally
checks the complete inputs, current package inventories, frozen builder, absence
of mapped processes/mounts and the resource budget. Constructions must run
sequentially. Do not lower those guards or silently select a newer package.

```bash
A733_PROJECT="$A733_REPRO/project"
A733_USERSPACE=/var/lib/a733-clean-replay-20261106/userspace-timesync-account-dates-01/work/export-timesync-result/rootfs.tar.gz
A733_DIAGNOSTIC=/var/lib/a733-bootstrap-inputs-20261107
A733_TAG=managed-systemd-20261113-repro-01
args=(--project "$A733_PROJECT" --tag "$A733_TAG" \
      --userspace "$A733_USERSPACE" --diagnostic "$A733_DIAGNOSTIC")
python3 -I -B "$A733_PROJECT/build/launch-deterministic-host-roots.py" \
  "${args[@]}" --plan > "$A733_REPRO/plan.json"
systemd-run --unit=a733-managed-systemd-root-reproduction-20261113-01.service \
  --service-type=exec --property=RemainAfterExit=yes \
  --property=CPUQuota=200% --property=MemoryMax=3G \
  --property=RuntimeMaxSec=2h --property=UMask=0077 \
  --property="StandardOutput=append:$A733_REPRO/build.stdout" \
  --property="StandardError=append:$A733_REPRO/build.stderr" \
  --setenv=MAKEFLAGS=-j2 --setenv=CMAKE_BUILD_PARALLEL_LEVEL=2 \
  /usr/bin/python3 -I -B "$A733_PROJECT/build/launch-deterministic-host-roots.py" \
  "${args[@]}"
```

Sample unit status, disk free space and available memory during construction.
The phase is
`/var/lib/a733-clean-replay-20261106/host-roots-managed-systemd-20261113-repro-01`;
outputs are under `work/project/out/host-payload-managed-systemd-20261113-repro-01`.
Keep the phase and all failed receipts. Completion requires MainPID0,
SubState=exited, Result=success, ExecMainStatus0 and a successful complete receipt
with unchanged inventory and no mapped processes/mounts. Producer receipts
explicitly report `full_filesystem_readback=false` and
`independent_image_readback=false`; their artifact hashes do not supply that
separate verification. Require independent filesystem readback before accepting
the images.

Independently compare all corresponding raw/gzip outputs and manifest with both
coordinator builds, including complete gzip expansion. Record discrepancies,
source/command pins, resources and receipts off-device. Only then claim the
build-procedure portion reproduced. Artifact review and fresh physical deployment,
actual managed KVM, rollback/recovery and final boot qualification remain gates.

> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Build and verify the F349 managed trial

Frozen source is `f3496f5ff1b3629d5333d26725b9f5494ace9d25`, tree
`aec007a4cff593e6e1feaa5d0acf459df6445c8c`, 1156 tracked files. Its combined
3717-test Ubuntu run passes. One construction and independent complete
filesystem/readers pass; actual managed KVM and an independently matching
construction remain required. [Evidence](../../evidence/managed-permit-root-trial-20261113.json).

Use Ubuntu 22.04 `drew@build.drewnet.online`, exact 849-package outer inventory and the
retained frozen 842-package inner builder. Keep the declared signed package archives.
[Builder](../../build-host-assessment.md), [dependency provenance](../../evidence/outer-builder-849-20261113.json).

The source bundle is retained on codex at
`out/managed-permit-root-trial-20261113-01/source.bundle` and on the builder at
`/var/lib/a733-managed-permit-root-trial-20261113-01/source.bundle`:
4825943 bytes, SHA256 `fa2b88d6c34bb383fe7cd3ee2a2d83db4608bcb448039b50d01eae6288deca05`.
Authenticate it before cloning. The userspace input remains
`/var/lib/a733-clean-replay-20261106/userspace-timesync-account-dates-01/work/export-timesync-result/rootfs.tar.gz`,
138093095 bytes, SHA256 `0ead62c982344bfa3a796027e2f07ce0d8402e7033122707e01a9a3f8860f248`.
Diagnostic inputs remain `/var/lib/a733-bootstrap-inputs-20261107`; the launcher
checks each pin. Its diagnostic DTB provenance does not replace working NVMe BOOT.

## Independent fresh construction

Run in a root shell on the builder after the coordinator releases reproduction.
Use a new path/tag and refuse existing output. Recompute disk/memory admission;
the trial required 19079050489 free bytes. Never lower the launcher guard.

```bash
set -euo pipefail
umask 077
A733_REPRO=/var/lib/a733-managed-permit-independent-reproduction-20261113-01
A733_BUNDLE=/var/lib/a733-managed-permit-root-trial-20261113-01/source.bundle
test ! -e "$A733_REPRO"
mkdir -m 0700 "$A733_REPRO"
git clone --no-checkout "$A733_BUNDLE" "$A733_REPRO/project"
git -C "$A733_REPRO/project" checkout --detach f3496f5ff1b3629d5333d26725b9f5494ace9d25
A733_PROJECT="$A733_REPRO/project"
A733_TAG=managed-permit-repro-20261113-01
A733_USERSPACE=/var/lib/a733-clean-replay-20261106/userspace-timesync-account-dates-01/work/export-timesync-result/rootfs.tar.gz
args=(--project "$A733_PROJECT" --tag "$A733_TAG" \
      --userspace "$A733_USERSPACE" --diagnostic /var/lib/a733-bootstrap-inputs-20261107)
python3 -I -B "$A733_PROJECT/build/launch-deterministic-host-roots.py" \
  "${args[@]}" --plan > "$A733_REPRO/plan.json"
systemd-run --unit=a733-managed-permit-independent-reproduction-20261113-01.service \
  --service-type=exec --property=RemainAfterExit=yes \
  --property=CPUQuota=200% --property=MemoryMax=3G \
  --property=RuntimeMaxSec=2h --property=UMask=0077 \
  --property="StandardOutput=append:$A733_REPRO/build.stdout" \
  --property="StandardError=append:$A733_REPRO/build.stderr" \
  --setenv=MAKEFLAGS=-j2 --setenv=CMAKE_BUILD_PARALLEL_LEVEL=2 \
  /usr/bin/python3 -I -B "$A733_PROJECT/build/launch-deterministic-host-roots.py" \
  "${args[@]}"
```

Sample resources and unit status during construction. Require terminal
MainPID0/SubStateexited/Resultsuccess/ExecMainStatus0, complete successful receipt,
unchanged inventories and no mapped processes/phase mounts. Phase output is
`/var/lib/a733-clean-replay-20261106/host-roots-managed-permit-repro-20261113-01/work/project/out/host-payload-managed-permit-repro-20261113-01`.
Compare all 133 manifest-listed artifact and manifest with the trial output,
including complete root gzip expansions. Retain discrepancies and failed receipts.
The trial plus this independent construction establish matching builds only after
actual comparison passes; the guide does not claim that future result.

## Independent artifact checks

The trial verifier separately checked all 133 artifact hashes, both gzip streams,
both complete filesystems and semantic package/namespace/timestamp/superblock
contracts. Historical maintenance readers accepted both prepared roots; current
readers accepted retained normalized A. [Proof](../../evidence/managed-permit-root-trial-20261113.json).
Producer receipts correctly retain `full_filesystem_readback=false` and
`reproducibility=not established`; separate checks supply their own evidence.

The verifier retains three prepared roots and only removes its explicitly
temporary, inode-bound legacy expansion after successful preparation/readers.
Original producer raw/compressed files and backups remain. Do not create a
redundant large builder archive just to transfer files; stream the selected
compressed root and small authenticated metadata with progress and fsync.

These 4 GiB roots are for NVMe. Keep the 2 GB recovery card, working normalized A,
compatible BOOT/kernel/DTB and original/calibrated SPI backups. The next
[deployment](../../implementation-status.md#next-actions-and-acceptance) requires live
exact-target review, authenticated actual boot and real KVM execution. Further
backup/restore, maintenance, final boot series and physical reproduction gates
remain separate.

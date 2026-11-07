# Completed kernel replay retention

The completed frozen-builder kernel replay is retained as a complete archive on
codex and the builder. Its expanded builder tree was removed after independent
review and full verification. Historical kernel evidence remains unchanged;
commands naming the original tree now require restoration.

[Evidence](evidence/kernel-run-retention-20261107.json) records identities,
archive pins, exact reviewed controllers, disposable tests and durable journals.
The archive is **1,612,368,790 bytes**, SHA-256
`dca31cae45cad28a5022917a31da0f4c4834f7f51ef069873275ca87cb429b7c`.

| Role | Host and absolute path |
| --- | --- |
| Original, now absent | build: `/var/lib/a733-clean-replay-20261106/kernel-run-01` |
| Retained primary | codex: `/var/lib/a733-kernel-run-retention-20261107/primary/kernel-run-01.tar.gz` |
| Retained mirror | build: `/var/lib/a733-kernel-run-retention-20261107/mirror/kernel-run-01.tar.gz` |

Both retained files are root-owned mode `0600` in private `0700` directories.
The temporary second codex archive was removed only after full builder mirror
verification. The two hosts are not proven independent physical failure domains.

All 121,554 names and restorable metadata passed independent comparison; GNU tar
compared the archive against the live tree before and after private quarantine.
Checks included full gzip integrity, file hashes, numeric ownership, modes,
nanosecond mtimes, symlinks and attributes. The reviewed mutation guards inspect
every process thread's references and mount namespace, plus loop-device backing
files. The initial preparation helper checked process leaders only; corrected
thread-aware checks passed before either actual retirement. Original sources,
frozen builder and current deployment artifacts were excluded.

To restore, stop all users of the original location and require that exact
`kernel-run-01` destination to be absent. Authenticate either retained file's
complete size/hash. Reserve at least **5,350,162,432 bytes plus 2 GiB working
headroom**, plus any transfer staging allocation. As root, extract into the exact
original parent using GNU tar with `--keep-old-files --same-owner --numeric-owner
--sparse --acls --xattrs --xattrs-include='*'`. The archive already contains its
`kernel-run-01/` top-level directory. Refuse symlink ancestors and existing
outputs; do not overlay a newer build. Compare the restored tree with GNU tar and
the complete retained metadata facts, then recheck the receipt/log anchors in
the evidence before using it. **Full actual restoration has not been exercised.**

Original retirement left 9,228,218,368 builder bytes free. A subsequent userspace
build and retained archive mirror left 6,837,583,872 bytes. Closing descriptors
after removing the temporary native copy left 2,747,215,872 codex bytes free.
These are measurements, not current capacity guarantees. Recheck before each
build; production capacity guards and the two-job compilation limit remain.
No LV resize, Orange Pi erase or SPI write occurred.

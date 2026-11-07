# Replay with fixed locked-account dates

The separate replay driver adds the reviewed [account-date policy](userspace-account-date-replay.md)
to fresh baseline construction. Historical producers, profile locks and evidence
remain unchanged. The new adapter must reproduce their exact compressed archive,
raw tar and BOM bytes before publishing. This is build infrastructure; it adds
no installed account-maintenance command or physical acceptance evidence.

The driver authenticates the historical pipeline component and every selected
source against committed Git blobs. It reuses the original foreign/configuration
phases, API handshake, frozen-builder preflight, resource floors and cleanup
checks. Only export staging and verification use the new replay contract.
The export adapter authenticates the immutable source manifest and original core,
runs its original receipt/inventory/tree preflight and cleanup, then normalizes
only the two closed locked-account date sets before final scanning and export.
Any normalization or publication failure invalidates the disposable clone.

The additional `account_date_replay` receipt records the adapter/helper/contract
and source manifest, authenticated input receipt, changed basenames and before/
after file digests. Core exporter attribution remains accurate. The outer receipt
identifies this new replay driver rather than claiming historical launcher
execution. All historical archive pins remain mandatory.

From a clean committed checkout on the prepared builder:

```sh
sudo python3 -I -B build/launch-account-dated-userspace.py \
  --project "$PWD" --tag account-dates-01 --plan
sudo python3 -I -B build/launch-account-dated-userspace.py \
  --project "$PWD" --tag account-dates-01
```

The plan is read-only. Actual construction needs a fresh tag, more than 4 GB free
initially and the existing per-phase/runtime/memory guards. It records its inputs,
phase logs and receipts under
`/var/lib/a733-clean-replay-20261106/userspace-pipeline-account-dates-01/`.
Never reuse the earlier failed repeat receipt or overlay its tree.

The adapter also composes with the unchanged time-profile exporter and retains
its original identity/service/derivation validations. The separate outer consumer described below authenticates the new baseline
replay source manifest before fresh time-profile construction. The historical time launcher deliberately
rejects this changed source closure; do not relax it or edit old receipts.

Eight driver tests and ten adapter tests pass, including real mapped-owner
receipt metadata. Independent Sol/high review passes after correcting a receipt
reader that incorrectly required readonly mode. [Actual fresh baseline execution](evidence/account-date-baseline-execution-20261107.json)
now passes from `b133454`: all 220 packages, unchanged archive/raw/BOM pins and
independent complete 15,876-entry raw PAX/BOM readback. Host inventory is unchanged
and no mapped processes or mounts remain; minimum disk/RAM were 6.02 GB/7.25 GB.
[Actual time-profile repetition](evidence/account-date-time-execution-20261107.json)
also passes. Production construction remains pending.
The baseline proof establishes no deployment success. Full roots/state/NVMe construction,
physical SSH/KVM and independent deployment remain required.

## Repeat the time profile

The separate `build/launch-account-dated-timesync.py` consumer now retains the
historical time launcher's full base, clone, namespace and receipt checks. It
also requires the successful dated baseline's marked provenance, exact source
closure, phase runners and archive/BOM verification. Export uses the reviewed
adapter's `time` policy; original time-adapter attribution remains accurate.
Eleven focused regressions and independent Sol/high review pass.
[Actual execution](evidence/account-date-time-execution-20261107.json) from
`a7e51be` now repeats the unchanged time archive/raw/BOM pins. Independent raw
PAX/BOM inspection verifies all 15,909 entries, 221 packages, fixed 996:996
identity and time-service files/links. Both staged source snapshots match all
27 recorded Git blobs; postchecks show unchanged host inventory and no mapped
processes/mounts. Minimum disk/RAM were 5.25 GB/7.24 GB. This proves no installed
NTP exchange or physical boot.

After the baseline succeeds and the hash-pinned additional package described in
[time inputs](host-time.md) is available on the builder:

```sh
sudo python3 -I -B build/launch-account-dated-timesync.py \
  --project "$PWD" --base-tag account-dates-01 --tag account-dates-01 \
  --extra-deb /tmp/a733-timesync-input.deb --plan
sudo python3 -I -B build/launch-account-dated-timesync.py \
  --project "$PWD" --base-tag account-dates-01 --tag account-dates-01 \
  --extra-deb /tmp/a733-timesync-input.deb
```

The plan performs no builder staging. Actual execution retains the existing
5 GiB initial floor and full clone/configuration budget; it must refuse inadequate
capacity before staging. Keep historical successful and failed receipts intact.

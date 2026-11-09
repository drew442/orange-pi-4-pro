# Physical endurance result, 2026-11-09

The **eight-hour workload gate passes using the existing longer run**. The
operator shortened all planned tests above eight hours to eight hours and
explicitly accepted existing qualifying data without a rerun. The protected
STATE host ran an actual four-vCPU nonroot KVM guest with disk cycles, physical
network transfers and four additional low-priority host CPU workers.
[Result and raw-record pins](evidence/eight-hour-endurance-result-20261109.json).
Overall release acceptance remains incomplete.

The run began **2026-11-09 02:10:42 UTC**, host boot
`dd8d6f67-d297-4f3b-bc9a-8a41c9742051`, guest run
`59af604a-cf2e-4c8f-9fd5-e26b8283d14c`. Its immutable guest was originally
configured for 86,400 seconds. Preserve that historical metadata: the coordinator
stopped the collector and guest after the operator changed the requirement.
This is an operator stop, with no claim of guest-generated `ENDURANCE_PASS` or
clean guest filesystem shutdown. Separate [actual clean guest shutdown,
checkpoint and restart](kvm-vgic-results-20261108.md) evidence remains valid.
No new eight-hour workload is required.

## Retained physical observations

- **2,423 samples**, guest elapsed 10 through **72,669 seconds**, covering
  72,659 seconds (more than 20 hours), final cycle 2,422. Maximum collector gap
  is 30.542 seconds. The endpoint-at-zero record remains separate; no full
  telemetry is claimed for the initial ten seconds.
- Every sample passes the original authenticated collector validator and
  progress watchdog: actual KVM/VGIC/four-vCPU descriptors, nonroot restrictions,
  four live guest workers, disk and one-MiB network payload hashes, physical
  Ethernet counters, thermal trips, memory and exact SD identity/write counters.
  No observed NVMe kernel errors or guest failure markers occur.
- Observed SoC temperatures span **15.810–35.526 °C**, below recorded critical
  trips. Minimum available memory is **11,902,619,648 bytes**. These are recorded
  sensor readings, not measurements of negotiated adapter power.
- Original guest PID 2153, CPU parent 2169 and exit observer 2098 were observed
  without restarts. The observer retained guest runtime 72,711.745 seconds.
  Host CPU runtime was 72,709.542 seconds; status 2 follows its intentional
  interruption handler. QEMU exited zero after host SIGTERM. These exit values
  do not establish guest-initiated poweroff.
- All 1,194 management-watch guards pass, maximum interval 61.11 seconds. This
  periodic evidence does not claim continuous observation of every host worker.
- Post-run SMART remains at zero critical warnings/media errors, 153 error-log
  entries and 77 unsafe shutdowns. The existing latest invalid-field status
  8194 is unchanged; this is not an empty-error-log claim.
- The complete **1,977,614,336-byte SD** still hashes to
  `401df09e666c22db8e8f9adbeecc0f385a910b1f41995e7a1d0bc68be3b64be5`,
  matching its [baseline](evidence/protected-sd-baseline-20261109.json). Every
  recorded Linux write/discard/flush field is zero. Final shutdown/reboot and
  firmware reconciliation remain part of immutable-SD acceptance; a matching
  hash alone cannot detect same-content firmware writes.

The earlier failed baseline capture was a devtmpfs atime identity-check refusal,
then corrected and repeated successfully. Goodix/sound probe findings are
retained with the headless vendor boot evidence. Neither is silently removed
from the logs. The [previous failed workload](endurance-run-20261108.md) remains
separate and unexplained; this result does not reclassify it.

## Preservation and next work

Collector, guest, CPU workers and management watch are stopped. The exit
observer finished normally. The old November 10 02:16 timer was stopped and disabled; both
fixed preserver phases were explicitly run immediately and retained under
`out/endurance-outcome-20261109-01` and `out/endurance-outcome-20261109-final`.
Their original `scheduled-final` phase label does not change the recorded actual
capture time. The collector transient unit was already garbage-collected, so
its conservative preserver skipped SMART; the coordinator independently
confirmed the stop and retained a separate read-only SMART capture.

The bridge capture and native relay remain running. Their retained complete
1,262,306-byte console prefixes are identical. Raw telemetry, both outcome
captures, stop receipts, observer/watch files, full UART prefixes and health/SD
observations are private under `out/`; no guest disk backup is implied.

Before any reinstall, preserve current guests, original identity and paired
ROOT-A/ROOT-B/STATE using the reviewed [backup procedure](host-state-backup.md).
The stopped endurance disk is a forensic artifact: do not manufacture a clean
checkpoint or repair it before preservation. Then complete changed-slot
interruption/rollback, the fresh normalized installation, recovery and restored
host/KVM checks, the final boot route, ten cold/ten warm boots and independently
reproduced deployment. Future release guest, collector and host load default to
**28,800 seconds**; observation grace is for termination capture, not a longer
workload test. Original sources, archived evidence and generated artifacts keep
their historical hashes.

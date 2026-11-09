# Physical endurance run, 2026-11-09

The protected STATE SD/NVMe boot pair now physically boots with read-only root,
automatic time synchronization and the authenticated 40-file management tool
set. Its [maintenance receipts](evidence/protected-state-maintenance-20261109.json)
record the exact writes, backup and UART reset. SPI remains unprogrammed.

A fresh release-mode KVM workload started **2026-11-09 02:10:42 UTC**, host boot
`dd8d6f67-d297-4f3b-bc9a-8a41c9742051`, guest run
`59af604a-cf2e-4c8f-9fd5-e26b8283d14c`. It requires **86400 guest-monotonic
seconds**; completion is expected no earlier than approximately **November 10,
02:11 UTC**. This is an ongoing test, not a release pass.
[Run metadata](evidence/endurance-release-run-20261109.json) records the initial
samples, services, actual host CPU workers and storage identities.

| Location | Service or evidence |
| --- | --- |
| A733 | `a733-guest-endurance.service`: four KVM vCPUs, UID/GID 60002, 512 MiB limit |
| A733 | `a733-endurance-host-cpu-release-20261109.service`: four nice-19 UID/GID 65534 workers |
| A733 | `a733-endurance-release-exit-observer-20261109.service`: retained D-Bus unit reference |
| A733 | `a733-management-watch-endurance-20261109.service`: bounded console guard/address snapshots |
| codex | `a733-offboard-endurance-collector-20261109.service` |
| codex | `out/endurance-physical-release-20261109-01/samples.jsonl` |
| codex | `a733-uart-relay-20261109-01.service`; `out/serial-recovery-20261109/serial-live.log` |

The collector transfers and authenticates a 1 MiB guest payload over physical
Ethernet every 30 seconds. It checks actual KVM/VGIC/four-vCPU descriptors,
nonroot restrictions, disk cycles, thermal trips, memory, network counters,
NVMe kernel errors and exact SD identity/write counters. The endpoint was observed
at guest elapsed zero; the first full collector sample is elapsed **10 seconds**.
No full telemetry is claimed for those initial ten seconds. The collector is an
independent native systemd service, not a shell background process.

The previous failed release guest was retained by a reviewed nonreplacing rename
to `/run/a733-guests/.endurance-failed-retained-20261108`, preserving inode 6673.
The original smoke directory remains; the fresh guest directory has inode 6678.
Their deterministic enrollment manifests can share a hash despite different
mutable disks. The failed run is not classified as clean or successful, and
its original management failure remains unexplained.

Before this run, NVMe SMART reported zero critical warning and media errors,
77 unsafe shutdowns, and 153 error-log entries. Its latest entry is the existing
invalid-field status 8194. These are baselines, not a clean error-log claim;
review any subsequent change against the captured command and kernel evidence.
The UART reset was performed only after syncing/flushing writes with NVMe idle
in the RAM installer; it is not counted as a release boot cycle.

After guest poweroff, preserve complete console and observer output under
`/run/corrected-kvm`, require matching `ENDURANCE_PASS` with elapsed at least
86400 and no `ENDURANCE_FAIL`, and correlate the run UUID with every sample.
Require independently observed normal zero guest exit and cleanup, plus the
host CPU service's full duration and normal zero exit. Collector exit 2 requests
external completion evidence; it never grants acceptance. Recheck NVMe health,
management continuity and SD counters. The bridge's `/var/log` is volatile, so
check the off-board UART relay before relying on its continuity.

Ten cold and ten warm boots, SPI recovery/SD-removed boot or complete immutable
SD fallback proof, interrupted update/recovery, dirty-journal/missing-NVMe tests,
a coherent full distribution image and independent deployment reproduction remain
separate acceptance gates. Do not reboot either board during this workload.

## Automatic evidence preservation

The [reviewed preserver](evidence/endurance-outcome-preservation-20261109.json)
is armed on collector exit and with a persistent native timer for
**2026-11-10 02:16 UTC**. Its protected standalone copy is independent of later
repository edits. It stores separate fresh snapshots in
`out/endurance-outcome-20261109-01` and `out/endurance-outcome-20261109-final`,
including raw exit fields, RAM observer/watch/load files, bounded journals and
health observations when the collector is confirmed stopped. SSH errors, missing
files, boot mismatches and possible truncation are retained explicitly. These
are unreviewed captures; no automatic pass or recovery is attempted.

A [live read-only preflight and independent review](evidence/endurance-journal-selector-repair-20261109.json)
found and corrected the journal boot selector before final capture: this target
requires the compact 32-character boot ID. The protected copy was atomically
replaced at November 9 12:55:58 UTC, without restarting services or changing the
workload. All three journals now return records from the expected boot.

The [whole-SD baseline](evidence/protected-sd-baseline-20261109.json) is now
`401df09e666c22db8e8f9adbeecc0f385a910b1f41995e7a1d0bc68be3b64be5`
for 1,977,614,336 bytes. Its first 256 MiB exactly match the written protected
image, and Linux write counters remain zero. This baseline was collected after
the workload started; later whole-media comparisons and firmware audit are still
required. It is not an installation step or a throughput benchmark.

## Twelve-hour observation

The [retained prefix audit](evidence/endurance-prefix-12h-20261109.json) covers
1,454 complete samples through guest elapsed 43,600 seconds, cycle 1,454.
Audited telemetry checks have no failures: exact SD identity/unmounted state and
zero write/discard/flush counters, no observed NVMe kernel errors, KVM execution
and progress, one-MiB network payloads and selected Ethernet link. Maximum
collector sample gap is 30.339 seconds. Observed SoC thermal zones range
18.786–35.526 °C; minimum available memory is 11,904,503,808 bytes.

A direct coordinator observation also confirms the original guest, host CPU and
exit-observer PIDs remain active with zero restarts and the original boot ID.
The prefix alone does not establish continuous host-worker/observer history.
Final exits, SMART health and the full 24-hour endpoint remain pending. This is
a partial observation, not endurance or release acceptance.

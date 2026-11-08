# Physical endurance run, 2026-11-08

The corrected host passed [restoration and KVM restart](evidence/corrected-host-guest-restore-20261108.json)
and the [60-second physical smoke test](evidence/endurance-smoke-physical-20261108.json).
The release-mode workload started at **2026-11-08 11:06:24 UTC** and requires
86400 guest-monotonic seconds. This attempt **failed management reachability**:
the collector lost SSH at elapsed 300.005 seconds, after its last valid guest
sample at elapsed 357 seconds. The board still answers ping at its recorded MAC
and address, but port 22 refuses connections. Guest/load/observer state after
that loss is unknown; no remote stop or reboot was performed. Serial console
output is required before diagnosing or recovering. This is not a release pass.

[Run metadata](evidence/endurance-release-run-20261108.json) records exact boot,
guest, enrollment and process identities. The original smoke directory was
retained by an independently reviewed, nonreplacing rename on p5; fresh release
assets were enrolled separately. The first valid collector sample is guest
elapsed **86 seconds**: an initial shell-background collector failed to persist,
so it was replaced with an independent supervised system service. No telemetry
is claimed for that initial interval; final acceptance must assess coverage.

| Location | Service or evidence |
| --- | --- |
| Orange Pi | `a733-guest-endurance.service`, four KVM vCPUs, UID/GID 60002 |
| Orange Pi | `a733-endurance-host-cpu-release.service`, four nice-19 workers, UID/GID 65534 |
| Orange Pi | `a733-endurance-release-exit-observer.service`, retained D-Bus reference |
| codex | `a733-offboard-endurance-collector-20261108.service` |
| codex | `out/endurance-physical-release-20261108-01/samples.jsonl` |
| Orange Pi | `/run/corrected-kvm/release-exit-observer.jsonl` and `.stderr` |

The collector runs independently of this chat and transfers/verifies one actual
1 MiB guest payload over SSH every 30 seconds. It records exact SD identity and
Linux counters, real KVM/VGIC/four-vCPU descriptors, process restrictions, thermal
trips, memory, CPU and physical network/storage counters, and kernel NVMe errors.
NVMe SMART/error logs are retained separately before and after the run. Existing
error-log entry count 151 describes an unsupported command field; zero media
errors and zero critical warning form the baseline. Any new error needs review.

After the guest powers itself off, retain the complete console, require release
`ENDURANCE_PASS` with elapsed at least 86400 and no `ENDURANCE_FAIL`, and correlate
the run UUID with all samples. Require the independent observer's normal zero
exit, ordered timestamps and successful cleanup, and the host CPU service's
full duration/normal exit. Collector exit 2 means pending external evidence,
never acceptance. Recheck NVMe health and SD counters. The original full-card
baseline remains available for the later boot/workload/shutdown comparison;
normal installation readback is still optional.

A completed endurance run alone does not close repeated cold/warm boots,
SPI/recovery or fallback SD proof, interrupted update/rollback, missing NVMe,
dirty-journal recovery, and independent clean deployment reproduction.

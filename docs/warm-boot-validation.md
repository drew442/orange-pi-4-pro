# Prepared warm boot collection

The collector is prepared and **unexecuted**. The running November 9 A733
24-hour KVM workload must remain undisturbed. No warm or cold boot acceptance
is claimed by this preparation or its mocked tests.

`scripts/collect-known-warm-boots.py` defaults to a local plan and makes no SSH
connection. Execution requires both `--execute` and a canonical nonzero
`--expected-start-boot-id`. After the workload has stopped and native automatic
outcome preservation has retained its snapshot, the prepared invocation is:

```sh
python3 scripts/collect-known-warm-boots.py --execute \
  --expected-start-boot-id ACTUAL-CURRENT-CANONICAL-BOOT-UUID \
  --tag AFTER-ENDURANCE-UNIQUE-TAG
```

For release counts, first select and install the final image and boot route,
then record the final firmware/SSD combination. Preliminary reboot trials on a
different route do not replace the final ten warm and ten cold boots. This
collector currently requires the known SD card; it is prepared for the immutable
SD route. A selected SPI route with SD removed needs corresponding collector
adaptation and physical validation before its ten-boot series.

This command has **not been run**. Replace the UUID with a fresh observation;
the collector refuses any mismatch. The existing storage/reboot authorization
applies; there is no new typed confirmation. Execution always targets
`root@192.168.1.116`, using `/home/drew/.ssh/id_ed25519`, strict known-host
checking with `HostKeyAlias=192.168.1.116`, batch mode and identities-only SSH.

The collector requires a complete native preservation output under
`out/endurance-outcome-20261109-01` or `out/endurance-outcome-20261109-final`.
It verifies the known run UUID `59af604a-cf2e-4c8f-9fd5-e26b8283d14c`, original
boot `dd8d6f67-d297-4f3b-bc9a-8a41c9742051`, retained remote snapshot and stopped
off-board collector. Nested capture errors, failed commands, timeouts, malformed
base64 and truncated command/journal captures refuse admission. Unit output must
retain guest/CPU/observer exit fields and show these workloads stopped. Guest,
kernel and manager journals must contain records from the known boot; the guest
journal must retain an exit/stop outcome. A nonempty dmesg capture is mandatory.

All six preserver `FILES` are mandatory: observer JSONL and stderr,
management-watch JSONL and stderr, and host CPU stdout and stderr. Empty stderr
and CPU output files are permitted when captured completely. Observer JSONL
must contain readiness and either a final record or a retained nonempty failure
stderr. Thus a failed endurance outcome can be retained and admitted; failed
evidence capture cannot. Missing observer stderr is refused. Management-watch
JSONL may be a bounded tail with a consistent offset/length; an initial partial
JSON line is skipped and subsequent complete records are required. Other small
files must be complete.

UART evidence requires either the native readable, nonempty full off-board
archive `out/serial-recovery-20261109/serial-live.log` or a valid nonempty
`uart-tail.json` capture. A UART tail may have a nonzero offset if its recorded
size, offset, length and truncation flag agree. A missing or unusable tail is
allowed only when the full archive remains readable. This verifies retention,
not console continuity or workload success. Preservation is evidence retention,
not a workload pass.

Before every clean `systemctl reboot`, the actual remote source rechecks the
current boot ID, kernel `6.6.98-a733-diag`, protected installed management module
and its `runtime_guard()`/`current_address()`, read-only root, and fixed STATE
UUID `360dfb39-f49f-487e-b307-0bb887c854c5`. It checks SPCC NVMe serial
`134807190FC700149277`, 256060514304 bytes, and SU02G SD CID
`035344535530324780716f216e00a8f5`, 1977614336 bytes. It requires initial PID
and user namespace inodes from the pinned vendor kernel (`0xEFFFFFFC` and
`0xEFFFFFFD`), matching PID 1 namespace objects and namespace types. NSpid
alone is insufficient.

All visible processes and tasks are inspected for SD mounts and KVM VM/vCPU
descriptors. Running guest, endurance CPU and exit-observer units refuse a
reboot; an active-exited unit with MainPID 0 is permitted. SD write, discard
and flush counters must all remain zero. Online CPUs must be `0-7`; kernel
logs must show all CPUs starting at EL2 and successful VHE initialization.
NTP must be synchronized. `/dev/kvm` API version 12 and IRQCHIP capability 1
are preliminary checks only; the collector never launches a guest or claims
guest execution.

A fresh private `out/warm-boots-TAG/events.jsonl` retains raw transport snapshots,
requested cycles and validated observed cycles. Every appended record is
fsynced before the next action. SSH has bounded response size and wall time.
Each reboot receives a 300-second readiness deadline, with progress messages;
only distinct, fully validated boot IDs count. A previously seen boot ID,
partial response, lost identity, timeout or admission failure cannot produce a
ten-boot completion. Failure retains partial evidence and stops further reboot
requests. Failed execution is not automatically resumed: review its recorded
requests before starting any later series.

A completed series requests exactly ten clean warm reboots. Forced reboot,
SysRq reset and power cycling are never issued. Every record keeps
`hardware_acceptance=false`; the coordinator must review the complete series
and independently reconcile it with physical acceptance. Full SD byte comparison
is a separate later step; the collector avoids whole-card or root hashing on
each boot. Ten cold boots require manual power-cycle evidence and remain
**unperformed**. SPI boot, dirty-journal recovery and release acceptance also
remain separate gates.

Local validation: `python3 -m unittest discover -s tests -p
 'test_known_warm_boots.py'` exercises mocked transport and the actual remote
source with fake kernel/systemd interfaces. These tests establish software
behavior only.

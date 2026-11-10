# Final cold boot collection

Ten genuine cold boots remain unperformed. The final normalized A image and
protected SD route are installed. The eight-hour workload gate already passes
from retained data; no endurance rerun is needed.

The collector must run in your interactive terminal on **codex**, while you can
reach the A733 USB-C power cable. Keep the serial bridge powered separately.
Leave the SD, NVMe, Ethernet and UART connections in place. No image flashing,
SPI programming or storage erasure is part of this procedure.

The collector is ready for operator use: independent review, 18 mocked cases and
the privileged Ubuntu **2304-test** suite pass, with five shell suites and five
diagnostic cases. [Validation](evidence/cold-boot-collector-validation-20261110.json).
No physical cold boot follows from these software checks.

When you are ready to perform the physical steps, run from the repository:

```sh
python3 scripts/collect-known-cold-boots.py
python3 scripts/collect-known-cold-boots.py --execute \
  --expected-start-boot-id 274af3d8-156c-4de8-a1ed-688c89b5aaa4 \
  --tag normalized-a-protected-sd-20261110-01
```

The first command only prints the plan and makes no network connection. The
second requires a local interactive terminal, a fresh tag and the exact current
host boot UUID. If the board has rebooted since this record, obtain a fresh UUID
and coordinator readiness check; a mismatch refuses before poweroff.

For each of ten cycles, the collector checks the release, exact storage targets,
idle host, synchronized clock and zero SD write counters. It requests one clean
poweroff and waits for a fresh UART `reboot: Power down` message. **Wait for its
physical power prompt.** Disconnect the A733 USB-C power cable, leave it removed
for at least ten seconds, reconnect it, then answer `yes`. Answer `no` to stop.
The attestation means you personally performed that cycle; a reboot or reset
does not qualify. Do not pipe answers into the collector.

It then waits up to five minutes for a distinct boot that passes the same
readiness guards. It prints progress and repeats. The overall series, including
operator waits, has an eight-hour limit. A failed or uncertain step stops further
requests. It never repeats an uncertain poweroff or automatically resumes a
partial series. If it stops, leave the evidence intact and send the last screen
messages before starting another attempt.

Evidence is retained privately under
`out/cold-boots-normalized-a-protected-sd-20261110-01/events.jsonl`.
Return that path and the terminal's final result; the coordinator will also
review the continuous off-board UART archive and perform one full-card SD
comparison at the series boundary. There is no whole-card read on each boot.
Keep the board on and leave the serial capture running after completion.

The script reuses the hash-pinned [warm collector](warm-boot-validation.md)
release and device guards. An SSH connection closed during the poweroff request
has an unknown command exit, even when its pre-poweroff guards pass. Fresh UART
powerdown, your physical attestation and a distinct ready boot are all required
before a cycle counts. A changed boot UUID alone never proves a cold boot.
Mocked tests establish collector behavior; physical acceptance requires review
of the actual ten cycles and unchanged SD evidence.

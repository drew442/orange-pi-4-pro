# Final cold boot collection

Ten operator-performed cold boots of final normalized A with the protected SD
route pass independent review and the complete SD boundary comparison.
[Physical result](evidence/final-cold-boots-20261111.json).
The procedure below is retained for future explicitly requested qualification. The eight-hour workload gate already passes
from retained data; no endurance rerun is needed.

The collector must run in your interactive terminal on **codex**, while you can
reach the A733 USB-C power cable. Keep the serial bridge powered separately.
Leave the SD, NVMe, Ethernet and UART connections in place. No image flashing,
SPI programming or storage erasure is part of this procedure.

The collector is ready for operator use: independent review, 18 mocked cases and
the privileged Ubuntu **2304-test** suite pass, with five shell suites and five
diagnostic cases. [Validation](evidence/cold-boot-collector-validation-20261110.json).
No physical cold boot follows from these software checks.

When you are ready to perform the physical steps, run on codex in an
interactive terminal. The installed host is at `192.168.1.116`.

```sh
cd /home/drew/orange-pi-4-pro
boot_id=$(ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes \
  root@192.168.1.116 'cat /proc/sys/kernel/random/boot_id') &&
tag="normalized-a-protected-sd-$(date -u +%Y%m%dT%H%M%S%NZ)" &&
python3 scripts/collect-known-cold-boots.py --execute \
  --expected-start-boot-id "$boot_id" --tag "$tag"
```

The first SSH command reads the current boot UUID. The collector checks that
same UUID and the full release and device guards before requesting poweroff.
A restart between those checks causes a refusal. The timestamp gives each
attempt its own evidence directory. Keep failed attempts for review.

The first operator attempt reached the host over SSH but used the previous
boot UUID. The saved remote error was `boot ID differs`, reported by the
collector as `SSH probe failed/timeout/oversized`. It stopped before requesting
poweroff. Read the current UUID as shown above when starting a new attempt.

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
`out/cold-boots-<tag>/events.jsonl`.
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

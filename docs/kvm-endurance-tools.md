# Read-only KVM endurance evidence tools

These tools support the Orange Pi 4 Pro A733's physical KVM endurance gate.
They have software regression coverage only; their existence does not establish
physical execution, 24-hour endurance, thermal stability or release acceptance.
The coordinator must retain separate console, exit observer, duration, host-load,
SMART and storage-write evidence. The 2 GB SD must have zero routine writes;
all installed runtime state belongs on the nominal 250 GB NVMe.

`scripts/collect-kvm-endurance.py` runs on the off-board workstation with Python
3 and OpenSSH. Example (substitute the actual canonical installed-host boot UUID
and existing private-key path):

```sh
python3 scripts/collect-kvm-endurance.py \
  --host 192.168.1.116 --key /private/path/board-key \
  --boot-id 11111111-1111-4111-8111-111111111111 \
  --mode smoke --unit a733-guest-endurance.service \
  --output out/endurance-smoke-fresh
```

`--mode release` selects 86400 seconds; smoke selects 60. `--interval` is fixed at
30 seconds, and `--guest-name` only accepts `endurance`. The output directory must
be fresh, its parent must exist and have at least 256 MiB available. The collector
creates a private directory and appends/fsyncs `samples.jsonl`, at most 2883 sample
records plus a final record for release. Each metadata response is limited to
64 KiB. The 1 MiB guest payload is retained only in bounded RAM, hashed on the
workstation and discarded. There are no payload files. Responses are framed as
`ENDURANCE1 PAYLOAD_LENGTH JSON_LENGTH\n`, exact payload bytes, then exact JSON
bytes. Payload length must be zero for an inactive endpoint or exactly 1048576.
Each SSH subprocess has a 30-second deadline and a bounded stdout reader.

The collector uses the existing trusted host key under
`HostKeyAlias=192.168.1.116`, strict host-key checking, batch mode and the supplied
identity with `IdentitiesOnly=yes`. Additional identity files from local SSH
configuration remain possible; the trusted host-key check is mandatory. It does not enroll keys or print authentication stderr.
The remote command is fixed isolated Python source supplied on stdin; its only
variable command argument is a validated service name encoded as JSON. It reads
host proc/sysfs, systemd service properties and kernel messages. It never changes
a service, VM, mount, device or boot state. Guest HTTP requests use the fixed
host loopback port 18081 with proxies and redirects disabled. Each running sample
transfers the actual zero-filled 1 MiB guest payload across the physical SSH/LAN
path to the workstation (about 2.8 GiB across 24 hours).

Running samples require unchanged host/guest boot UUIDs, matching profile/mode,
nondecreasing guest counters, four live guest CPU workers, known disk/payload
hashes, the actual QEMU process running as UID/GID 60002 with all capability sets
zero and `NoNewPrivs: 1`, KVM-only arguments, four vCPU descriptors, a VM descriptor
and the observed vendor `anon_inode:kvm-arm-vgic-v3` descriptor. The descriptor
label comes from the prior physical counter-3 observation in
`out/vgic-live-boot-20261108/kvm-process-counter3.txt`; it does not claim the new
endurance guest has run. QEMU must use `-smp 4`, `-m 256`/`256M`,
`-cpu host`, `-accel kvm`, and `-machine virt,gic-version=host` (the same machine
string with `accel=kvm` is also accepted). The unit's collected `MemoryMax` must
be exactly 536870912 bytes (512 MiB). TCG is rejected.

A progress watchdog requires both guest elapsed time and cycle count to advance
within 120 seconds measured by the collector's monotonic clock. Short stalls
through 120 seconds accommodate the 30-second disk cycle and probe jitter; a
longer stall fails, even when one of those two counters continues advancing.
The disk counter identifies the run and can remain constant. This watchdog is
liveness evidence and does not establish full workload duration acceptance.

The collector records host CPU/memory, thermal zone values and critical trips,
CPU frequency, counters/speed/duplex for MAC `10:f4:ae:90:5e:57`, block statistics
and NVMe-related kernel error lines. Existing NVMe errors explicitly set
`nvme_kernel_error_assessment: coordinator_review_required`; an empty baseline
sets `no_errors_observed`. Coordinator assessment remains required before
acceptance when preexisting errors are present. Every thermal zone needs a readable critical
trip and must remain below it. Missing or changed error evidence fails closed;
kernel ring-buffer turnover can therefore require operator investigation.
SD mount or any nonzero write/discard/flush counters fails. Block field positions
follow Linux [block statistics documentation](https://www.kernel.org/doc/html/latest/block/stat.html).
These Linux observations do not prove absence of firmware writes or replace full
SD hashes and shutdown evidence. SMART is explicitly `not_proved`: no NVMe ioctl
is implemented. No package-count metadata is guessed or synthesized.

Every record has `hardware_acceptance: false`. Exit 1 means failed evidence or a
source/transport failure. A bounded decoded sample is retained alongside a
validation failure when available; transport failures do not reuse an earlier
sample. Exit 2 means pending external completion, including an
inactive guest unit, or reaching the time/sample bound. A pending result is not a
pass, even after 24 hours. An active unit with missing HTTP endpoints fails.
`ENDURANCE_PASS`, independent clean QEMU exit and the entire elapsed workload
window require separate coordinator review. A 60-second smoke run cannot satisfy
the release gate.

`scripts/endurance-cpu-load.py --mode smoke|release` is a separate explicit load.
It refuses root before forking, runs exactly four workers at nice 19, each hashing
one reusable 1 MiB RAM buffer, and opens no workload files, disks or network
connections. It uses fork/signals rather than file-backed shared-memory events.
The service launcher must supply an unprivileged user/group, no new privileges
and appropriate CPU/memory limits; this helper neither creates nor starts that
service. It returns 0 after its selected duration, 2 on interruption and 1 on a
worker failure. SIGTERM/SIGINT stop all children; unresponsive children are killed
within the bounded cleanup period. Its exit status alone is not endurance proof.

Run software tests with:

```sh
python3 -m unittest discover -s tests -p test_kvm_endurance_tools.py
```

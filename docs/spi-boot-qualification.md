# Native SPI boot evidence collection

`scripts/collect-spi-boots.py` collects a finite series of SPI/NVMe boot evidence
for the A733, with the known 2 GB recovery SD physically removed and the nominal
250 GB NVMe installed. It does not grant hardware or release acceptance. The
coordinator reviews the actual guest receipts, identities, UART and flash custody
against the [production readiness plan](spi-production-readiness-plan.md).
The earlier protected-SD boot counts and collectors remain separate evidence.

With no mode flag, the collector prints a plan without opening files, contacting
the board, creating evidence or requesting power:

```sh
python3 scripts/collect-spi-boots.py
```

After reviewing the ledger, choose only the remaining shortfall (1–10), the
explicit current canonical boot UUID and a new tag. For example, these placeholders
must be replaced with reviewed values before execution:

```sh
python3 scripts/collect-spi-boots.py --execute --kind warm --count 1 \
  --expected-start-boot-id CURRENT-BOOT-UUID --tag UNIQUE-REVIEWED-TAG \
  --host 192.168.1.118
```

Cold collection uses `--kind cold` from a real local interactive terminal. Each
cycle waits for native UART `reboot: Power down`, then asks the operator to
personally disconnect USB-C **POWER** for at least ten seconds and reconnect it.
Only a `yes` answer received after at least ten seconds permits continuation.
The attestation is an operator statement; software does not measure cable state.
Do not remove heatsinks or substitute an SSH session for physical power handling.

A series is capped at eight hours. A requested warm transition has 300 seconds
for its new boot; cold powerdown has 60 seconds and its new-boot window is 300
seconds after physical attestation. All windows remain within the series cap.
Progress appears at least every thirty seconds during polling or operator waits.
The optional `--host` chooses a canonical unicast IPv4 endpoint (default
`192.168.1.116`); use `--host 192.168.1.118` for the reviewed current DHCP address.
DNS names, noncanonical addresses, loopback, multicast and limited-broadcast endpoints
refuse. The plan and all collector evidence retain the selected endpoint. Only
the SSH remote address changes: the existing identity key, strict host-key
checking and pinned `HostKeyAlias=192.168.1.116` remain fixed. A different address
must authenticate the same board. The collector uses the native UART relay
archive. Transport output, UART and cumulative evidence storage are
bounded. It requires a 256 MiB free-space floor plus a 192 MiB capture reserve.
This is boot qualification, without a new endurance workload.

The initial ready and qualified boot is an uncounted baseline. Read-only readiness
authenticates the installed B runtime and protected storage policy, SD absence,
normal PID1, successful terminal automatic guest unit, NTP, CPUs 0–7, the pinned
6.6.98 kernel and EL2/VHE initialization. Starting PID1, busy units or unsynchronized NTP permit
read-only polling; authenticated source, root/storage or failed-unit mismatches
stop the series. Readiness does not call admission or launch APIs, so a gate
already closed for shutdown remains observable. Readiness alone is not proof of
an actual accelerated guest.

Once readiness succeeds, the collector checks the global current-boot intent
namespace before staging or synchronization. It reads SPI code-component hashes
once per ready qualification, then places the two frozen external helpers in the
fixed private native tmpfs directory `/run/a733-spi-collector`. Existing identical
files are permitted; unsafe, different or extra entries refuse. Qualification
runs explicitly once per boot, synchronizes NVMe through the existing helper and
retains its complete thirteen-record automatic guest cohort. Its head and
registry digests bind the next power request.

| Captured helper | Bytes | SHA256 |
| --- | ---: | --- |
| `qualify-managed-boot.py` | 15424 | `e2254bd80778c250f979a3e74ebe120c845d865e57d5b7d1fb02711c176c02bc` |
| `request-spi-power.py` | 17795 | `670a99065cba20b4ab10783698d80aa9217455be359a9dcc9ed480c709efb43a` |

Before the sole power-helper SSH transaction for an old boot UUID, the collector
exclusively publishes and fsyncs both the global
`out/spi-power-intents/OLD-BOOT-UUID.json` and per-run request event. Global intents
span tags. A separate retained local exclusive execution lock serializes executing
series across tags before any remote staging, synchronization or power request.
Read-only observations do not acquire that exclusive lock. Never delete, rewrite or retry them; a partial/unsafe intent namespace
also refuses. A new tag cannot synchronize or issue another request on an old
boot with an existing intent. Any timeout, SSH close or unknown acknowledgement
allows only read-only observation until a distinct authenticated boot and its
ordered UART boot interval resolve the outcome. A later boot does not convert an
unknown command acknowledgement into a known one. Failure leaves all artifacts
and intents intact, without reopening admission.

Warm UART must show normal kernel restart, followed by one new native boot0
banner and the ordered SPI/NVMe route markers. Cold requires powerdown before
attestation and then the same new boot interval. Stale pre-request bytes, UART
rotation/truncation, panic, repeated firmware boots, repeated boot UUIDs and
markers from separate intervals cannot qualify. A transition is proposed only
after a distinct ready UUID, UART, fresh SPI components and the complete explicit
new-boot qualification succeed. UART is drained to EOF within its byte limit before
and after qualification, and the complete interval is rechecked before credit.

Per-boot SPI observations hold read-only `/dev/mtd0` character device `90:0`,
check named and held identity and sysfs geometry (16 MiB, erase size 65536), and
verify the known boot0 `[0,237568)` and package `[262144,1654784)` hashes. They
retain the current 2048 calibration bytes `[260096,262144)` separately. They do
**not** verify preservation of the gap or trailing flash. Before/after full-flash
snapshots, original/calibrated backups and exact ledger identity review remain
required under the [calibration policy](spi-calibration-policy.md) and existing
[physical SPI procedure](spi-sd-boot-20261111.md). The collector never writes SD,
SPI or root images; qualification has the helper's explicit NVMe sync behavior.

Evidence is exclusively published under private `out/spi-boots-TAG/`, including
captured collector, dependency and helper source pins, all bounded raw/partial transport output, stderr/exit state,
request references, UART chunks, observations and incomplete or completed events.
Helper and private evidence reads retain file identity, ownership, permissions,
link count, size and nanosecond modification/change timestamps across capture;
read-driven access-time updates are permitted. Source length and digest checks
remain required. Copy this evidence and full boundary flash snapshots off-device
before final review. Successful collection always retains `hardware_acceptance=false` and
`coordinator-review-required`.

For an interrupted run, preserve its context and gather one read-only observation
into a new private `out/spi-observation-TAG-UUID/` directory:

```sh
python3 scripts/collect-spi-boots.py --observe-only --tag EXISTING-TAG --host 192.168.1.118
```

An explicit `--host` also permits read-only recovery after a DHCP address change,
using the same pinned board identity. Changing the endpoint never clears global
intents, bypasses the execution lock, resolves unknown acknowledgements or grants
credit without the required distinct boot and UART evidence.
This reads the existing request context without changing it and never stages
RAM helpers, synchronizes qualification, requests power, creates a global intent
or credits a transition. It reports old boot, distinct authenticated boot observed
or uncertain. After coordinator review, start a separate series from an explicitly
observed new boot and adjust its remaining count. Automatic resume is not provided.

The regression tests use fake clocks, transport, storage and UART inputs. Passing
host tests establishes software behavior only. No physical collection or new
hardware acceptance is claimed by this guide.

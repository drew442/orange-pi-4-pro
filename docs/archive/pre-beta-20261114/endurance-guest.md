> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Endurance guest preparation

`build/endurance-guest.py` prepares an initramfs for the existing four-vCPU
QEMU virt kernel. It never runs a guest, connects to a board, formats a disk,
or copies a kernel or disk image. Software packaging success does not establish
KVM, boot, endurance or release acceptance.

Produce a release candidate from the authenticated corrected guest archive:

```sh
python3 build/endurance-guest.py \
  out/guest-initramfs-identity-20261108/initramfs.cpio.gz \
  out/endurance-guest-release-NAME
```

On the builder the same authenticated input is retained at
`/var/lib/a733-counter3-guest-backup-20261108-01/verified/initramfs.cpio.gz`.
Its exact size is 975394 bytes and SHA-256 is
`10b45e928ab1726e818f78ced73ab6015f0fbabef4f18403d574091fde0adaa2`.
The producer authenticates compressed bytes before bounded decompression,
validates every newc record, replaces only `init`, and adds
`etc/endurance.conf` and `etc/endurance-status.cgi`. All other original records
remain byte-for-byte unchanged. The original confirmation CGI remains in the
archive but is hidden beneath a fresh RAM `/www` mount. The producer records
its own and helper/source hashes. New records use epoch 1793750400; gzip uses
mtime zero. A fresh direct `out/endurance-guest-NAME` directory is private
(mode 0700), with mode 0600 output files. Outputs are ignored artifacts.

The default is exactly 28800 seconds with manifest mode `release`. To prepare
an explicitly separate smoke fixture, add `--smoke-seconds 60` (allowed range
30–300). Both modes record `hardware_acceptance: false`; smoke completion
cannot satisfy an 8-hour gate. The guest reports lower-case modes in JSON and
serial markers.

The current acceptance threshold is eight hours. Existing measured workload
evidence covering at least 28800 seconds qualifies for duration review without
a new run, including an already-running legacy 86400-second guest. Its immutable
configuration and historical records must remain unchanged; coordinator review
must distinguish an operator stop from the legacy guest's automatic completion.
The current producer and guest guard accept exactly 28800 seconds for new
release fixtures, so a legacy artifact is not silently relabeled.

Use the existing standard guest enrollment/rendering tools for a future VM
named `endurance`, UID/GID 60002, four vCPUs, 256 MiB guest RAM and a 512 MiB
service memory ceiling. The pinned guest kernel is 5251080 bytes, SHA-256
`1294959bcaa5a5ad3791913e33cffb8f0893708396aac8be9837c03a0e1f9377`;
`configs/guest-kernel.fragment` enables SMP with `CONFIG_NR_CPUS=4`. This
producer does not compile or substitute a kernel. Enrollment and hardware
execution remain coordinator work.

Use a disposable copy of the 128 MiB fixture seed at
`out/guest-fixture-seed-20261104/guest-disk.raw`, SHA-256
`ff595fac0b9a7d990f099edc0dd0f9d4859fb9942e185d2f2377088ce32bd577`.
The manifest records expected kernel and seed pins; the producer does not
read these large inputs. Enrollment must authenticate them. Preserve the
counter-3 backup and its original enrollment. Never attach a physical host
device as this fixture's disk.

The guest requires PID 1 and the exact QEMU virt DT compatible string
`linux,dummy-virt` before disk access. It considers only `/dev/vda`, requires
262144 sectors, ext4 UUID `d67f33ba-e201-46bc-980b-61901bbd1156`, label
`kvm-fixture`, magic and feature bytes copied from the authenticated corrected
init. It mounts `ro,noload`, verifies `a733-kvm-guest-fixture-v1` and a bounded
counter, then unmounts and mounts read-write. It increments the counter once;
only the counter and a fixed 16 MiB scratch file receive workload writes.
It removes an old scratch name before creating the file, avoiding symlink
following. No disk scan or formatting path exists.

Four CPU workers repeatedly SHA-256 a shared 1 MiB zero payload in RAM and
verify the expected digest. Each atomically publishes a monotonic heartbeat
once per second; the parent requires progress at each disk cycle as well as
worker liveness. Every approximately 30 seconds, the guest rewrites the
16 MiB zero scratch file, syncs and verifies the SHA-256 of all bytes. Reading
after sync may use the guest page cache; this does not prove a cache-bypassing
media read. RAM mounts and workload files have fixed size bounds.

DHCP configures `eth0`; BusyBox HTTP serves `/payload.bin` (1 MiB zero bytes)
and a read-only `/cgi-bin/status` on TCP 8080. Only GET with an empty query is
accepted by the CGI, and request data never becomes a command or path. Status
is published by atomic rename in RAM. Its fixed schema is:

```json
{"schema":1,"profile":"a733-kvm-endurance-v1","run_id":"guest-boot-UUID","mode":"release","state":"running","required_seconds":28800,"elapsed_seconds":30,"cycles":2,"disk_counter":1,"cpu_workers":4,"cpu_workers_alive":4,"disk_sha256":"expected-16-MiB-zero-digest"}
```

State is `running`, `finishing`, or best-effort `failed`; failure status uses
zero live workers conservatively. A fresh run ID is the guest boot UUID.
The collector must authenticate the expected mode/duration, require counter
and cycle progress, repeatedly transfer and hash the payload through the
physical host's SSH/LAN path, and retain serial output. HTTP availability
ends at orderly guest shutdown; it is not required after poweroff.

The timer uses `/proc/uptime`. Once elapsed time reaches the configured
minimum, the guest stops CPU/HTTP workers, syncs, unmounts its fixture disk,
emits a unique `ENDURANCE_PASS` record including mode, elapsed/required time,
cycles, counter and hashes, then requests its own `poweroff -f`. Failure emits
`ENDURANCE_FAIL` and attempts cleanup and poweroff. Collectors must reject any
failure marker, even if QEMU exits zero; they must separately verify normal
service/QEMU exit and guest-initiated shutdown. There is no remote confirmation
wait, 120-second deadline, interactive fallback, or host stop requirement.

Actual release validation still requires the corrected physical A733 host,
real KVM evidence, at least eight measured hours of release workload (including
qualifying legacy evidence), four additional low-priority
host CPU workers, and the host collector's thermal, memory, NVMe health and
network evidence. Preserve the 2 GB SD, nominal 250 GB NVMe, minimal host and
zero routine SD writes requirements. A smoke pass, unit tests or emulation
cannot replace those hardware observations.

## Retained eight-hour package

Two constructions on the Ubuntu 22.04 builder match byte for byte. The selected
future-use package is `out/endurance-guest-release-8h-20261109-01/` on codex:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `initramfs.cpio.gz` | 976,723 | `564f9445c03b1cf44d45326d385f1c9a33f0355bf42f007361aa7e8d2030bd8b` |
| `manifest.json` | 5,074 | `1f8593e6a07e864673e0ffe4c9bfd3b76eb0b1a96515f88ef20fbbd60e1b0088` |

Independent archive inspection confirms the 28,800-second configuration and
matching guard, exact source dependencies, original kernel/seed pins and all
preserved archive records. This new package is unbooted. The accepted
[existing longer run](../../evidence/eight-hour-endurance-result-20261109.json) supplies
the eight-hour hardware evidence; packaging does not require another workload
run. Preserve the stopped legacy fixture before any future fresh enrollment;
do not silently replace its initramfs or manufacture a clean disk checkpoint.

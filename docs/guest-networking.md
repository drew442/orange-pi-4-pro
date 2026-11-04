# Isolated guest networking with a loopback TCP forward

Guests retain QEMU's restricted user network. An optional, explicitly configured
TCP forward binds only to host `127.0.0.1`, targeting `10.0.2.15` inside that
guest's private user network. No bridge, TAP, host route, firewall or management
interface is changed. There is no outward NAT profile in this implementation.
Actual board networking and a KVM guest network exchange remain unproven.

QEMU documents that `restrict=on` isolates the guest from the host/outside while
allowing explicitly configured forwarding. The fixed host address avoids the
wildcard behavior of an omitted address. See the [QEMU 7.2 option source](https://github.com/qemu/qemu/blob/v7.2.0/qemu-options.hx)
and [network option reference](https://www.qemu.org/docs/master/system/invocation.html#network-options).

## Configuration and tool availability

Before enrolling a new guest, optionally add this exact field to its otherwise
validated configuration:

```json
"network": {"mode": "isolated", "host_tcp_port": 18080, "guest_tcp_port": 8080}
```

Host ports must be integers from 1024 through 65535; guest ports from 1 through
65535. One TCP forward is supported. Unknown fields, other modes, arbitrary
addresses, strings, booleans and incomplete port pairs are refused. The guest
must use the default DHCP address `10.0.2.15`. Select a free host port; QEMU
refuses a bind collision and does not replace an existing listener. Every guest
needs a distinct host port. Local host users can access the forwarded service.

The generated unit supplies paired `--host-tcp-port` / `--guest-tcp-port` flags.
The backend is fixed to:

```text
user,id=guestnet,restrict=on,ipv6=off,hostfwd=tcp:127.0.0.1:18080-10.0.2.15:8080
```

Legacy configs without `network` keep their previous unit and restricted backend,
with no forwarded listener. Invalid launch options are rejected before input
opening/hashing. Dry-run prints the proposed arguments and opens no listener.
Actual launch still requires the protected NVMe inputs, unprivileged account,
KVM access, retained descriptors and `-accel kvm -cpu host`, with no fallback.

The root images in [the installed-tools record](guest-tools-package.md) predate
this optional forward. Use the updated complete portable tree below; do not
replace only its config or launcher module. It preserves 38 sources/39 entries
and 16 commands. Keep networking in the trusted manifest from enrollment;
in-place network configuration changes for an existing guest are not implemented.
Checkpoint, backup and restoration preserve the configured field.
Older strict configuration readers reject this new field. Keep the compatible
portable tree and generated unit path available on NVMe when testing rollback;
do not silently remove networking metadata to bypass that refusal. Cross-version
rollback with configured networking still needs physical validation.

Updated package: `tools.tar.gz`, 113,789 bytes, SHA-256
`8e8629540c21f5830359c6e7ca806407885efa148429aea88195bb3495c4b006`.
Its manifest pin is
`60731890107e504dfce9e9d7297032daac46b320b7cee528e99d40fcd9fae5e1`.
The archive and manifest match a fresh-checkout rebuild. Download from the
builder's `guest-network-artifacts-20261104/package/` beneath
`/home/drew/orange-pi-4-pro-build/`. Follow [complete-tree staging](guest-tools-package.md),
using a fresh versioned directory such as `guest-tools-0d83950` and the new
archive pin. Point `A733_GUEST_TOOLS` at its unpacked `a733-tools` directory.
Neither published recovery SD nor existing root images were silently replaced.

## Current disposable network fixture

The rebuilt guest uses the same pinned source/toolchain and `virt` configuration;
its kernel Image matches the earlier build byte for byte. New artifacts:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `Image` | 5,251,080 | `1294959bcaa5a5ad3791913e33cffb8f0893708396aac8be9837c03a0e1f9377` |
| `initramfs.cpio.gz` | 974,226 | `2f3360a8f6365b84c352cae99949a5ae92a81001efe28dbae221f334805f854b` |
| `guest-disk.raw` | 134,217,728 | `48074e898f24a18ed617c5a999f81806d4c6064086712acb4d4831d6ce4102ff` |

The disk retains its disposable UUID/label/marker and initial counter zero.
It is a new artifact, not a replacement checkpoint pin for an existing guest.
Compared with the earlier seed, debugfs shows different builder UID/GID and
source ctime metadata. Full seed-filesystem reproducibility remains open; no
whole-disk byte reproducibility is claimed. Source hashes and initial contents
are verified separately from physical execution.

The fixture checks CPU and disk, increments the counter, syncs and unmounts the
disk, then obtains DHCP and serves an HTTP endpoint on guest port 8080. It waits
up to 120 seconds for a host read followed by exact counter confirmation.
Confirmation is written to a private RAM file and atomically renamed, so init
cannot observe an empty marker. Failure does not emit network PASS. No guest
has actually run this init on the board.

Rebuild on the prepared builder with two jobs already fixed by the recipe:

```sh
build/guest-fixture.sh /home/drew/orange-pi-4-pro-build/kernel \
  /home/drew/orange-pi-4-pro-build/busybox/bin/busybox \
  "$(pwd -P)/out/guest-fixture-network-20261104-01"
```

The build passed in a protected root checkout at
`/var/lib/a733-guest-network-work-20261104`. Use a fresh output name to repeat it.
It never modifies the source kernel tree or writes a storage device. Resources
were sampled every 30 seconds; compilation used two jobs. Download copies are
under the builder's `guest-network-artifacts-20261104/fixture/` directory.

## Physical request/response acceptance

After authorized board installation, measured resource allocation and verified
guest storage/account activation, enroll the new fixture with the optional
network field above. Keep the source seed immutable and use a private NVMe disk.
Render/review the unit from that exact config with the updated complete tools.
Start it using the [guest management procedure](guest-enrollment.md), capturing
the complete console. Wait for:

```text
PASS disk previous=0 counter=1
READY network tcp=8080 counter=1; host must verify and confirm HTTP response
```

In another shell on the host, use the observed counter and configured port. The
following is prospective hardware usage, not a performed KVM acceptance test:

```bash
set -euo pipefail
: "${A733_NETWORK_PORT:?Set enrolled host_tcp_port}"
: "${A733_EXPECTED_COUNTER:?Set counter from this guest console}"
[[ "$A733_NETWORK_PORT" =~ ^[0-9]{1,5}$ ]]
(( 10#$A733_NETWORK_PORT >= 1024 && 10#$A733_NETWORK_PORT <= 65535 ))
[[ "$A733_EXPECTED_COUNTER" =~ ^[1-9][0-9]{0,8}$ ]]
expected="a733-kvm-guest-network-v1 counter=$A733_EXPECTED_COUNTER"
response=$(curl --noproxy '*' --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:$A733_NETWORK_PORT/cgi-bin/probe")
[[ "$response" == "$expected" ]]
printf '%s\n' "$response"
confirmation=$(curl --noproxy '*' --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:$A733_NETWORK_PORT/cgi-bin/probe?confirm=$A733_EXPECTED_COUNTER")
[[ "$confirmation" == "$expected" ]]
printf '%s\n' "$confirmation"
```

Capture `ss -ltnp` while the guest runs: the chosen listener must show only
`127.0.0.1`, with no IPv6/wildcard listener for that port. Record management SSH
continuity and verify the forward cannot be reached on the wired management
address. The fixture should report `PASS network loopback-http counter=N`, then
request a clean shutdown. Record QEMU's exit status, stopped service, disk
persistence and a restart with its reviewed checkpoint. A local HTTP response
without actual KVM-only guest execution does not satisfy acceptance.

## Software checks and limits

Actual pinned ARM QEMU network backends exchanged 53 bytes with a synthetic
Ethernet/TCP endpoint. QMP reported zero CPUs; `/proc/net/tcp{,6}` showed only an
IPv4 loopback forward. A synthetic SYN toward a separate host-gateway TCP
listener produced zero host accepts over 0.75 seconds. This is one bounded
policy check, not exhaustive isolation or guest-network validation.

The pinned static ARM BusyBox ash/httpd served the actual CGI, refused wrong
confirmation and recorded the exact counter. A separate packaged ARM workflow
preserved the network config through enrollment, checkpointing, backup and
restoration, parsed the unit, and checked the launcher's exact dry-run backend.
No guest kernel or KVM execution was substituted for hardware acceptance.

```sh
sudo /usr/bin/python3.10 -I -B tests/verify_guest_network_arm.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/busybox/bin/busybox
```

The probe owns private network/PID/mount namespaces and uses loopback only.
Board DHCP/virtio operation, actual KVM traffic, long workloads, restored guest
networking and management connectivity across boot/update/rollback remain open.

Evidence: [new fixture](evidence/guest-network-fixture-20261104.json),
[file readback](evidence/guest-network-fixture-verify-20261104.json),
[updated package](evidence/guest-network-package-20261104.json),
[ARM network/CGI](evidence/guest-network-arm-20261104.json) and
[packaged ARM configuration workflow](evidence/guest-network-config-arm-20261104.json).

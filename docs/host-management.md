# Management runtime prototype

Managed roots contain `a733-management-v1`. This interim Debian prototype has
passed file and ARM userspace checks, but has not booted on the A733 or accepted
an SSH connection. The [diagnostic image](diagnostic-image.md) remains the first
hardware boot. Buildroot and the release-base decision follow baseline hardware
validation as specified in the [plan](hypervisor-plan.md).

## Persistent inputs and activation

[State creation](host-state.md) requires the operator's Ed25519 public key,
actual wired Ethernet MAC, hostname and canonical IPv4 administration client
CIDR. DHCP is required. The committed fixture uses documentation-only network
values and a test public key whose private key was deleted; it cannot provide
operator access. No real administration key has been supplied yet.

Before handing control to systemd, the bootstrap runs the management helper
with isolated Python imports and bytecode writes disabled. It checks selected
NVMe root/state partition roles, read-only root, writable state, state UUID,
marker, kernel ABI, ownership, bind sources and RAM overlay provenance.
Unexpected nested mounts fail closed. Configuration failure returns to serial
recovery rather than continuing to start management.

The helper prepares only the RAM-backed `/etc`: hostname, systemd-networkd and
resolved configuration, service links and journald limits. It masks the native
SSH service/socket aliases. Ethernet selection requires one physical wired
interface matching the configured MAC; DHCPv4 is enabled there, with IPv6 RA,
link-local addressing, LLMNR and mDNS disabled. Board DHCP/network behavior is
still untested.

The custom service waits for exactly one usable IPv4 address and launches SSH
bound to that address. It stops/restarts the child on address changes and fails
on ambiguity. SSH permits public-key root access only from the configured client
CIDR; password/keyboard-interactive authentication and forwarding are disabled.
The helper rechecks runtime provenance before starting service work.

Host private keys are generated only in selected NVMe state, never embedded in
root images. Publication uses atomic `renameat2(RENAME_NOREPLACE)`, with no
replacement fallback. Existing valid keys are reused; a missing public key can
be regenerated from the private key. Files and directories are synchronized.
The installer still needs to expose a trusted public fingerprint for first
connection verification. Never treat the test fingerprint in evidence as a
board identity.

Persistent journals are capped at 128 MiB, volatile journals at 16 MiB, with
256 MiB free-space reserve and seven-day retention. `/var`, operator home and
identity live on NVMe; temporary configuration lives in RAM. These settings do
not establish total state-space bounds or zero SD writes by firmware. Accounting
log rotation, time policy and measured board memory budgets remain operational
work. Actual zero-SD-write acceptance is a separate hardware gate.

## Verified commands and evidence

From the repository on the prepared Ubuntu 22.04 builder:

```sh
python3 -m unittest discover -s tests -p 'test_host_management.py'
sudo python3 tests/verify_host_management_arm.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz
sudo python3 tests/verify_managed_payload.py \
  /home/drew/orange-pi-4-pro-build/management-work-20261104/out/host-payload-20261104-02
```

The ARM checker requires prepared binfmt support. It authenticates the userspace
archive and creates a disposable private chroot; it does not mount filesystems,
configure networking or start listeners. Test private keys are removed with the
fixture. Actual ARM key generation/reuse, atomic publication, OpenSSH `-t`/`-T`,
locked-root PAM account checking and systemd unit verification passed. Account
checking is not SSH authentication or session execution. Configuration-file
preparation used a mocked mount guard only inside this disposable fixture; the
real CLI rejected the missing NVMe environment before configuration writes.

See [ARM results](evidence/management-arm-20261104.json),
[root inspection](evidence/managed-payload-verify-20261104.json),
[build resources](evidence/managed-build-resources-20261104.json), and the
[root](host-payload.md), [state](host-state.md) and [handoff](host-handoff.md)
guides for artifact hashes and reproducible recipe commands. Readback verified
both complete raw roots, compressed hashes/expansions, runtime contents, empty
machine identity and absence of host keys in `/etc/ssh`. No physical target was used.

On-board systemd boot, DHCP renewal/rebinding, trusted host-key verification,
actual operator login, persistence across reboot and recovery remain required.
No listener or KVM guest execution is established by these software checks.

## Upstream references

Policy follows the pinned [OpenSSH 9.2 portable configuration manual](https://raw.githubusercontent.com/openssh/openssh-portable/V_9_2_P1/sshd_config.5),
[systemd 252 network configuration](https://www.freedesktop.org/software/systemd/man/252/systemd.network.html)
and [journald limits](https://www.freedesktop.org/software/systemd/man/252/journald.conf.html).
The PAM probe uses the [Linux-PAM 1.5.2 interface constants](https://raw.githubusercontent.com/linux-pam/linux-pam/v1.5.2/libpam/include/security/_pam_types.h).

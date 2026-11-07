# Management runtime prototype

Historical generated paths below may have been archived. Consult the
[retention map](build-cache-retention.md#replay-and-prototype-output-retention)
before reusing them; use the [personalized deployment](deploy-personalized-20261106.md)
for the current installation.

Baseline managed roots contain `a733-management-v1`. The opt-in time profile
uses `a733-management-v2-timesync`; its source-integrated boot guard performs a
quiescent clock-state audit before PID 1. Live update and selector checks use
the read-only `directory_ready()` binding check, which ignores clock-file
contents and timestamps while checking directory identity, device, mode,
ownership and state UUID. Old tools reject v2, and new tools accept v1 for
rollback. This interim Debian prototype has
passed file and ARM userspace checks. Its first installed A733 boot reached
systemd, but DHCP/SSH failed. The [permission repair](installed-host-repair-20261107.md)
addresses machine-id readability; successful physical SSH remains pending. The installer-preparation image has physically booted; use
the personalized deployment above for installation. Buildroot and the release-base
decision follow baseline hardware validation in the [plan](hypervisor-plan.md).

## Persistent inputs and activation

[State creation](host-state.md) requires the operator's public-key bundle (Ed25519 and/or RSA),
actual wired Ethernet MAC, hostname and canonical IPv4 administration client
CIDR. DHCP is required. The committed fixture uses documentation-only network
values and a test public key whose private key was deleted; it cannot provide
operator access. The operator has now supplied RSA3072 and Ed25519 keys and authorized any
IPv4 source (`0.0.0.0/0`); [input fingerprints](evidence/operator-inputs-20261105.json)
record the real settings. Existing historical fixtures remain unsuitable for deployment.

Before handing control to systemd, the bootstrap runs the management helper
with isolated Python imports and bytecode writes disabled. It checks selected
NVMe root/state partition roles, read-only root, writable state, state UUID,
marker, kernel ABI, ownership, bind sources and RAM overlay provenance.
The package-state descriptor and eight read-only binds must match the selected
root, including exact mount origins and source/bound inodes. Unexpected package
ancestor/descendant and immutable `/usr` overmounts fail closed. Configuration failure returns to serial
recovery rather than continuing to start management.

The helper prepares only the RAM-backed `/etc`: hostname, systemd-networkd and
resolved configuration, service links and journald limits. It masks the native
SSH service/socket aliases plus APT, dpkg-backup and catalog-update maintenance
units. Ethernet selection requires one physical wired
interface matching the configured MAC; DHCPv4 is enabled there, with IPv6 RA,
link-local addressing, LLMNR and mDNS disabled. Board DHCP/network behavior is
still untested.

The custom service waits for exactly one usable IPv4 address and launches SSH
bound to that address. It stops/restarts the child on address changes and fails
on ambiguity. SSH permits public-key root access from the configured client CIDR (including
explicit `0.0.0.0/0` for any IPv4 source); password/keyboard-interactive authentication and forwarding are disabled.
The helper rechecks runtime provenance before starting service work.

Host private keys are generated only in selected NVMe state, never embedded in
root images. Publication uses atomic `renameat2(RENAME_NOREPLACE)`, with no
replacement fallback. Existing valid keys are reused; a missing public key can
be regenerated from the private key. Files and directories are synchronized.
The SSH supervisor prints `A733 SSH host key for ADDRESS: ED25519 SHA256:...`
to journal and the configured serial console before launching the listener.
Compare the first SSH prompt against that physical-console fingerprint; actual
serial visibility and SSH login remain hardware checks. Never treat a generated
test fingerprint in evidence as the board identity.

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
sudo /usr/bin/python3.10 -I -B tests/verify_host_management_arm.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz
sudo /usr/bin/python3.10 -I -B tests/verify_managed_payload.py \
  /home/drew/orange-pi-4-pro-build/package-state-work-20261104/out/host-payload-package-state-20261104-01
```

The recorded artifact recipe/runtime bytes match commit `f2428b9`.
Artifact manifests retain the exact built bytes. A new build must use its own generated manifest.

The ARM checker requires prepared binfmt support. It authenticates the userspace
archive and creates a disposable private chroot; it does not mount filesystems,
configure networking or start listeners. Test private keys are removed with the
fixture. Actual ARM key generation/reuse, atomic publication, OpenSSH `-t`/`-T`,
locked-root PAM account checking and systemd unit verification passed. Account
checking is not SSH authentication or session execution. Configuration-file
preparation used a mocked mount guard only inside this disposable fixture; the
real CLI rejected the missing NVMe environment before configuration writes.

See [ARM management checks](evidence/package-management-arm-20261104.json),
[ARM package query/VFS results](evidence/package-payload-arm-20261104.json),
[root inspection](evidence/package-payload-verify-20261104.json),
[managed loop checks](evidence/package-state-loop-20261104.json),
[package-state verification](evidence/package-state-verify-20261104.json), and the
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

## Real operator inputs

The supplied keys are `drew@BoganEdition` (RSA3072) and `drew@codex` (Ed25519).
Both will be installed into the same root `authorized_keys`, with canonical
key bytes and an ordered fingerprint list authenticated through state-image
readback and installation intent. Raw public-key inputs remain in ignored
per-installation storage; no private operator key is required by the builder.
The default hostname is `a733-host`, MAC is `10:f4:ae:90:5e:57`, and addressing
uses DHCP. The requested unrestricted source policy is IPv4 `0.0.0.0/0` because
the management listener currently uses IPv4 only. Password login and forwarding
remain disabled. RSA keys use the supported RSA SHA-2 signature algorithms;
the configuration does not enable the legacy `ssh-rsa` SHA-1 signature algorithm.

Changing these validation sources requires fresh roots, state and installation
artifacts. Earlier single-key fixtures and the currently published preparation
image do not become personalized simply because the inputs have been recorded.
Actual SSH authentication with both keys remains a board acceptance check.

Machine-id is a public system identity: new state seeds it as `0644`, and
management preparation upgrades older bound copies after identity/mount guards.
The containing identity directory remains `0700`; keys/configuration stay private.
The regular root serial login is locked and has no password. Use the installer
RAM root shell for the documented recovery procedure.

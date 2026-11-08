# Private SSH RAM installer derivation

`build/derive-ssh-installer.py` creates a personalized initramfs for the known
Orange Pi 4 Pro A733. It does not construct or write an SD image, install NVMe,
connect to the board, download packages, or execute ARM binaries. Its output
contains the existing board's private SSH host key. Keep the entire output
private; it is unsuitable for public distribution.

The compatible firmware/kernel/DTB, 2 GB SD and nominal 250 GB NVMe requirements
remain unchanged. This derivation does not establish boot, SSH, storage-idle,
zero SD writes or actual KVM guest acceptance.

[Actual construction and validation](evidence/ssh-ram-installer-20261108.json)
now pass for runtime03, including independent artifact inspection and ARM
SSH/PAM/PTY/rejection tests. The matched SD is also constructed and independently
reviewed. Physical boot and operator-key login remain pending.

## Inputs and invocation

Run as root in the prepared, pinned Ubuntu 22.04 builder checkout on known fixed
storage. The script checks the recorded binutils provider, disk-backed workspace,
at least 3 GiB free and 1 GiB MemAvailable before staging. Do not run a native
full build on the constrained coordinator host. Capture build and test logs in
ignored private `tmp/` or `out/`, never in Git.

All six CLI paths must be absolute. Output must be a fresh direct child named
`out/ssh-installer-NAME` in this checkout. Input paths must be canonical without
symlinks. Example paths refer to the builder, not the live board:

```sh
umask 077
python3 build/derive-ssh-installer.py \
  /var/lib/a733-installer-refresh-20261107/out/installer-initramfs-refresh-20261107-01/initramfs.cpio.gz \
  /var/lib/a733-installer-refresh-20261107/out/installer-initramfs-refresh-20261107-01/manifest.json \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /ABSOLUTE/PRIVATE/admin.pub \
  /var/lib/a733-original-host-identity-20261108-01/identity.tar \
  /ABSOLUTE/BUILDER/CHECKOUT/out/ssh-installer-NAME
```

The authenticated inputs are:

| Input | Bytes | SHA256 |
| --- | ---: | --- |
| Refreshed installer initramfs | 26977583 | `55cfb5ed7b7e7f423ad1ef528e2d926caf29159730ff6b696e0750a8e906f2fd` |
| Frozen ARM64 rootfs | 169852145 | `1967e76c0c831c1c0bab432e7116457d4372c15b413090c054f8958b72dcb392` |
| Original same-board identity tar | 10240 | `ae3930cec481912a164361e11c926ca02d81561a49c0fc49b70514dd19d3d06e` |

The base manifest must describe every authenticated CPIO entry exactly. Its
complete original bytes are embedded under
`usr/share/a733-ssh-installer/provenance/base-manifest.json`. The derived
`manifest.json` identifies a new profile and records actual output pins, mutations,
source files, ELF dependencies and pending acceptance gates. It does not impersonate
the original installer receipt. Existing installer assembly is intentionally not
compatible with this profile; matched SD assembly requires a separate reviewed
implementation.

Both plain public keys are mandatory, validated by `configs/host_config.py`:

- RSA: `SHA256:WVU8wsBgfy5mGFNAZMdrvf+a3cTYo85B8llJlHIhUKA`
- Ed25519: `SHA256:Dw9WPpqQ741skiDShdwAP3qXQtpgxgbUSlCp2hmod7k`

Only `identity/ssh/ssh_host_ed25519_key` and its `.pub` are selected from the
identity archive. The private member must be root-owned, regular and mode 0600.
The machine ID is excluded. Public fields and the retained public key must match
`SHA256:Epfegy9GsJ6ng4/0EH3dZmir7beQj5uYTnIbE4JPfEI`. This structural
check does not establish that the private seed signs for the public key; the ARM
SSH test below must establish that relationship. Private bytes are never printed.

## Contents and boot behavior

Unchanged base CPIO records retain their exact bytes, including metadata. Only
`init`, `etc/passwd` and `etc/group` may replace base content; the existing locked,
nonexpired root shadow stays unchanged. All 39 maintenance sources and their
`package.json` remain unchanged. Conflicting additions fail instead of replacing
base files. Output expansion is bounded to 256 MiB; compressed bytes plus the
64-byte wrapper must be strictly below 64 MiB.

The authenticated rootfs supplies sshd, ssh-keygen, the AArch64 loader and recursive
ELF closure. The dedicated PAM stack uses absolute module paths because the
minimal RAM root has separate `/lib` and `/usr/lib` directories. It explicitly
adds pam_deny, pam_unix and
pam_permit plus their dependencies. NSS files/DNS modules are explicit runtime
inputs. All available package copyright notices and common licenses are retained.
No package installation or download occurs.

The RAM init mounts proc, sysfs, devtmpfs, run/tmp tmpfs and devpts, probes modules,
selects physical Ethernet MAC `10:f4:ae:90:5e:57` and requests DHCP. It starts sshd
only after an IPv4 address, the pinned public host fingerprint and `sshd -t`
succeed. SSH listens on IPv4 port 22 from any subnet, requires a public key,
allows root with `prohibit-password`, uses PAM for the locked root account, and
disables forwarding. Authorized keys and the host private key are 0600, `.ssh`
is 0700, and a separate sshd account supports privilege separation. The serial
root shell remains available. There are no NVMe filesystem mounts, swap,
automatic physical writers or erase actions. Existing maintenance commands remain
operator-invoked and retain their own guards.

## Required validation

Host fixture tests cover framing, expansion caps, entry preservation, collisions,
identity framing and policy. They are not ARM runtime or hardware evidence.

Before accepting the profile, independently inspect the derived archive and run
an isolated ARM64 chroot/binfmt test using disposable client keys: pinned target
ssh-keygen host-key proof; `sshd -t` and `-T`; locked-root PAM account acceptance;
actual authenticated command and PTY; wrong-key/password rejection; forwarding
denial; and sshd reexecution/privilege separation. Preserve exact test logs and
pins outside Git. The minimal PAM stack is provisional until those tests pass.

Then independently review the matched SD image and physically boot it. Verify
RAM root, both operator key logins, retained host trust, serial recovery, idle
NVMe with no mounts/swap/holders/users, and zero SD writes. Never overwrite the
running NVMe to transition into the installer. Revalidate the known NVMe identity
and authenticated payload immediately before any separately guarded installation;
preserve guest checkpoints off-device. Actual KVM guest acceptance remains a
separate required hardware gate.

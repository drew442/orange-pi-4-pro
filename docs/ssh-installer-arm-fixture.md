# Private ARM64 SSH installer fixture

`tests/verify_ssh_installer_arm.py` tests the actual personalized initramfs in a
fresh ARM64 chroot through the prepared Ubuntu 22.04 builder's persistent
AArch64 binfmt registration. Execution is pending until its private `result.json`
records PASS. Host unit fixtures do not establish ARM execution or hardware
acceptance. The A733, 2 GB SD, nominal 250 GB NVMe, minimal host and zero routine
SD write requirements remain unchanged.

Run as root on known fixed builder storage with at least 1 GiB extra free space
plus a 256 MiB floor and 1 GiB MemAvailable. The native OpenSSH client package must
match `configs/builder-packages.lock.tsv`. No package install or binfmt changes
are performed. Supply a manifest SHA256 obtained from the independently inspected
private derivation, through the trusted operator/coordinator channel; merely
hashing an unauthenticated received manifest does not authenticate its contents.
All paths must be absolute and canonical; the output parent must already exist.
The output must be a fresh directory named `ssh-arm-fixture-NAME`:

```sh
umask 077
python3 tests/verify_ssh_installer_arm.py \
  /ABSOLUTE/PRIVATE/ssh-installer-NAME/initramfs.cpio.gz \
  /ABSOLUTE/PRIVATE/ssh-installer-NAME/manifest.json \
  CALLER_PINNED_64_LOWERCASE_HEX_MANIFEST_SHA256 \
  /ABSOLUTE/FIXED/PRIVATE/ssh-arm-fixture-NAME
```

The manifest bytes are authenticated before JSON parsing; they bind compressed
archive bytes before bounded gzip/CPIO parsing. Existing derivation helpers reject
unsafe names, duplicate entries, hardlinks, devices and symlink ancestors.
Extraction creates directories, regular files and finally aliases in a new
private scratch tree. It authenticates every mode/content entry, the locked root
shadow, exact SSH/PAM policies and actual AArch64 executable headers. Both input
files are read again after successful sessions to establish unchanged pins.

External `unshare --mount --net --pid --fork --kill-child=SIGKILL` launches the
runner. Mount propagation becomes private before mounting a private procfs, RAM
runtime directories and a fresh devpts instance. The chroot has only null, zero,
random, urandom and tty device nodes plus its own PTYs. No host `/dev` bind,
external interface, host port, disk mount or host PAM/SSH change is used.
Loopback is brought up only in the fresh network namespace; the native SSH client
contacts its ARM sshd on port 2222. The `--namespace` interface is internal and
refuses execution outside namespace PID 1.

Disposable Ed25519 client keys replace only the scratch copy of authorized_keys.
The target ARM `ssh-keygen -y` proves the retained private key derives the public
identity `SHA256:Epfegy9GsJ6ng4/0EH3dZmir7beQj5uYTnIbE4JPfEI`. Native clients pin
that public identity with strict known-host checking, so successful handshakes
also establish actual signing. The tests run target `sshd -t` and `-T`, verify
PAM and public-key-only/forwarding policies, authenticate a locked-root command
session returning UID 0, allocate a PTY, reject a wrong key and password-only
authentication, and reject an explicitly requested remote TCP forward. Normal
daemon mode exercises sshd's reexecution and privilege-separation session path.
PAM remains enabled throughout.

Each command has a timeout; the namespace has a 180-second overall deadline.
Namespace PID 1 exit destroys its remaining processes and mounts; the parent
terminates the process group on timeout. The parent checks for host-visible
scratch mounts before deleting the extracted tree and always removes disposable
client keys. Unexpected mounts cause failure and retain the private tree for
inspection. Namespace and per-command logs, public-only host-key proof, source
pins, binfmt interpreter record and `result.json` remain in the 0700 output
directory after success or runtime failure. Never publish this directory: the
artifact and transient extracted tree contain the actual private host key, and
server logs contain management metadata. Private key bytes are never printed or
copied into logs. The configured binfmt interpreter path is hashed; the
kernel-held interpreter descriptor cannot be independently inspected.

This fixture establishes software ABI/session behavior only. Matched SD assembly,
physical RAM boot, both original operator logins, serial recovery, NVMe idle,
zero SD writes and actual KVM guest acceptance remain separate hardware gates.

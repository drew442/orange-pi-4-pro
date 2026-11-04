# Portable maintenance tools

`build/maintenance-tools.py` packages the current closed maintenance source set
for later staging by an installed host or recovery runtime. It does not install,
execute or authorize the package. The package contains a read-only `tree/`,
`tools.tar.gz` and `manifest.json`; the seven command entrypoints are
`install-nvme`, `acquire-nvme`, `plan-nvme`, `prepare-host-update`,
`update-host`, `select-host-root` and `recover-host-root`. Their adjacent Python
imports are retained under `tree/scripts`, `tree/build` and `tree/configs`;
invoke scripts in isolated Python mode with `-I -B` only after staging the
complete tree.

## Build on fixed storage

The builder requires a trusted checkout and existing inputs on known fixed
storage. Run it as root on the prepared Ubuntu 22.04 builder; its output must be
a new absolute directory directly under this checkout's `out/`. For a clean
checkout at commit `d5c71f5`, use a fresh output name:

```sh
sudo /usr/bin/python3.10 -I -B build/maintenance-tools.py \
  "$(pwd -P)/out/maintenance-tools-20261104-01"
```

The builder rejects non-fixed workspace storage, unsafe or shared-writable
ancestors, symlinks, and existing output directories. It publishes atomically
and prints the manifest. Preserve the artifact and manifest hashes in the trusted build record.
The current artifacts and software checks are recorded below.
Do not treat a manifest downloaded beside an archive as an independent trust
anchor.

`tests/verify_maintenance_arm.py` is an opt-in, root-only software probe. It
requires package, runtime and candidate-A directories plus independently
trusted SHA-256 pins for the package manifest, payload manifest and QEMU
interpreter. It verifies package bytes against the trusted checkout, then runs
isolated AArch64 Python/import/CLI and filesystem compatibility checks in a
private chroot fixture. It uses no physical target arguments or block devices.
The result is ARM64 binfmt/chroot software evidence only: it does not prove an
A733 boot, KVM guest, real NVMe update, recovery, or SD write behavior. Its
`F`-flag check hashes the registered interpreter pathname, but cannot
independently inspect the interpreter file descriptor retained by the kernel.

## Stage an archive after installation exists

Only after a real host installation exists, stage the trusted archive on fixed
NVMe state. The live root is immutable. Use a new versioned private directory
and never replace a package while a maintenance command is running. In a root
shell, after checking that `/run/a733-state` is the installation's mounted NVMe
state and setting `A733_TOOLS_ARCHIVE` to the ordinary downloaded archive:

```sh
: "${A733_TOOLS_ARCHIVE:?Set absolute downloaded tools.tar.gz path}"
umask 077
install -d -o root -g root -m 0700 /run/a733-state/maintenance-20261104-01
cd /run/a733-state/maintenance-20261104-01
install -o root -g root -m 0400 "$A733_TOOLS_ARCHIVE" tools.tar.gz
printf '%s  %s\n' ca8428bddf7415a2983aee2db2c834aaa1e2d86d83a2ec8ffcbdd0c604ebd742 tools.tar.gz | sha256sum -c -
mkdir -m 0700 unpacked
tar --no-same-owner --same-permissions -xzf tools.tar.gz -C unpacked
export A733_TOOLS=/run/a733-state/maintenance-20261104-01/unpacked/a733-tools
/usr/bin/python3 -I -B "$A733_TOOLS/scripts/select-host-root.py" --help
```

The literal checksum above authenticates this release's archive before any tar
parsing. For a later release, obtain its pin from an independently authenticated
build record. The tar root is `a733-tools/`, with root-owned `0555` directories
and `0444` files; the containing directory stays `0700`. These are prospective
host staging instructions, not evidence of a reproduced board deployment.

The existing recovery initramfs recipe stages this package at
`/usr/lib/a733-tools` (including `/usr/lib/a733-tools/scripts`). It is not yet
physically installed or validated on the board. Host update and root-selection
documents use `A733_TOOLS` for the explicitly staged host package. Package
staging does not authorize a physical write. Preserve the
A733 target, 2 GB SD, nominal 250 GB NVMe, minimal host and zero routine SD
writes. Physical acceptance still requires actual hardware boot, NVMe operation
and an actual KVM guest.

## Current artifacts and software evidence

Built on Jammy from source commit `d5c71f5`. The 58,524-byte `tools.tar.gz` has
SHA-256 `ca8428bddf7415a2983aee2db2c834aaa1e2d86d83a2ec8ffcbdd0c604ebd742`.
The outer manifest SHA-256 is
`a903d00fd62dd14fc81a53c8e059304945ab7d012d12e8af34bc2b3195e7fafe`.
[Package records](evidence/maintenance-tools-20261104.json) retain all 19 file
hashes/modes, source records and archive metadata.

Download the ordinary files with:

```sh
scp drew@build.drewnet.online:/home/drew/orange-pi-4-pro-build/maintenance-artifacts-20261104/tools.tar.gz .
scp drew@build.drewnet.online:/home/drew/orange-pi-4-pro-build/maintenance-artifacts-20261104/tools-manifest.json .
```

[Clean-checkout packaging](evidence/maintenance-rebuild-20261104.json) reproduced
the tools archive, preparation initramfs and raw/compressed SD images byte for
byte using authenticated cached inputs. This is not a full clean vendor build.
The archive checksum/extraction and isolated native selection/recovery help
commands were also exercised in a private directory on fixed builder storage.
The [preparation guide](installer-preparation.md) provides the current 256 MiB
SD artifact, flashing authorization requirements and serial handoff.

[ARM execution evidence](evidence/maintenance-arm-20261104.json), verifier
commit `c44d670`, passed all module imports, seven CLI help paths, the actual
4 GiB candidate hash, real ARM `debugfs` metadata/selector inspection, GPT/sysfs
fixtures, atomic selector storage with queued SIGINT and live/recovery guard
refusals. Elapsed time was 1m38s, maximum RSS 102,560 KiB. The exact verified
command on the builder was:

```sh
sudo -i
cd /var/lib/a733-maintenance-work-20261104
/usr/bin/python3.10 -I -B tests/verify_maintenance_arm.py \
  "$PWD/out/maintenance-tools-20261104-01" \
  "$PWD/out/installer-runtime-maintenance-20261104-01" \
  /var/lib/a733-update-final-20261104/candidate-A \
  --package-manifest-sha256 a903d00fd62dd14fc81a53c8e059304945ab7d012d12e8af34bc2b3195e7fafe \
  --payload-manifest-sha256 51f8dfa7f0f66b005b9bfd08dc5f29fcf25f0791e5e844ab6af22de94aa9a44c \
  --qemu-interpreter-sha256 92c4aeb8dd898de7152c184370ab22f09ae7c5b7279fd300081c76cee3f78a80
```

The private namespace root had a nonrecursive self-bind so real storage guards
could inspect its mount entry, with read-only proc/sys and only `/dev/null`.
The signal fixture explicitly installed Python's normal SIGINT handler and
unblocked it before testing interruption. Neither correction changed production
policy. No block target, update apply, journal replay, host boot, KVM guest or
physical recovery was tested.

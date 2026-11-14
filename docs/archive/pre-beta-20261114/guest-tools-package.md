> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Installed and portable guest tools

Use the installed **40-file** tree at `/usr/lib/a733-tools` on the selected
[normalized distribution](../../current-images.md); it includes isolated loopback
networking. [Physical KVM](../../kvm-vgic-results-20261108.md) and
[update/rollback](../../physical-host-update-rollback-20261110.md) records authenticate
their respective installed closures. A fresh installation needs its own closure
and execution receipts. Automatic guest startup remains an explicit operation.

The package counts, portable archives and root pins below describe the
**historical November 4 builds**, retained for reproduction and older-root
maintenance. Their unperformed-status statements describe that checkpoint.
Do not replace the current installed tree with an older archive.


The current immutable A/B root build includes the complete maintenance and guest
tool tree at `/usr/lib/a733-tools`. The same source set is available as an
authenticated portable archive. Operational commands no longer need a Git
checkout. These root components have not been installed or booted on the board.
The published preparation/recovery SD retains its earlier maintenance subset.
The later [networking extension](../../guest-networking.md) requires its updated
portable archive; the root components recorded here have not been rebuilt with
that optional-forward support.

The package contains 38 explicit source files plus `package.json`, with 16 CLI
entrypoints for installation, host maintenance and guest activation, enrollment,
KVM-only launch, checkpointing, backup and local/HTTPS restoration. Relative
imports remain under `scripts/`, `configs/` and `build/`. No credentials, host
configuration, guest payload or generated disk image is included.

Files are root-owned `0444`, package directories root-owned `0555`. Invoke scripts
with `/usr/bin/python3 -I -B`. Packaging activates no mount, account, service or
guest. Guest operations require the installed managed NVMe host and its
systemd/sysusers runtime; RAM recovery is not an alternate guest host. Automatic
boot integration and guest networking remain implementation work.

## Use the complete tree

After a real installation of the new root components:

```sh
export A733_GUEST_TOOLS=/usr/lib/a733-tools
/usr/bin/python3 -I -B "$A733_GUEST_TOOLS/scripts/enroll-guest.py" --help
cat "$A733_GUEST_TOOLS/package.json"
```

For an older installed root, stage the complete archive on verified NVMe state.
Never replace a tree while a command is running. In a root shell, after checking
the selected host and `/run/a733-state` mount:

```sh
: "${A733_TOOLS_ARCHIVE:?Set absolute downloaded tools.tar.gz path}"
umask 077
mkdir -m 0700 /run/a733-state/guest-tools-cd80603
cd /run/a733-state/guest-tools-cd80603
install -o root -g root -m 0400 "$A733_TOOLS_ARCHIVE" tools.tar.gz
printf '%s  %s\n' e7495d225ef754a3ad591baecd098db7555a494dfe36d6b808de298be7f1ba07 tools.tar.gz | sha256sum -c -
mkdir -m 0700 unpacked
tar --no-same-owner --same-permissions -xzf tools.tar.gz -C unpacked
export A733_GUEST_TOOLS=/run/a733-state/guest-tools-cd80603/unpacked/a733-tools
/usr/bin/python3 -I -B "$A733_GUEST_TOOLS/scripts/restore-guest.py" --help
```

Authenticate before tar parsing. The containing directory stays private. Use a
fresh destination if it exists. On the separate backup machine,
`A733_BACKUP_TOOLS` can point to this complete portable tree with a compatible
Python runtime. These are prospective commands, not reproduced board deployment.

## Artifacts and build commands

Built on Jammy from source `cd80603`:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `tools.tar.gz` | 113,133 | `e7495d225ef754a3ad591baecd098db7555a494dfe36d6b808de298be7f1ba07` |
| tool manifest | 15,933 | `6b448c939a105a6bc9296187f6677d2740bd7ed388a7fb695349609af967e9b8` |
| `root-A.ext4` | 4,294,967,296 | `6329177322737505220da66b082b126e4ed66fd618e039c8b2e012214c0bbfda` |
| `root-B.ext4` | 4,294,967,296 | `a07015569e3c2b8fcd9094f1e462929c93be481ba6f1dd94426ee9b70eb9acfe` |

Compressed roots are 143,416,603 and 143,415,900 bytes. Complete hashes, package
BOM and root verification are retained in the linked evidence below. Builder
outputs are under `/var/lib/a733-guest-tools-work-20261104/out/`, with directories
`maintenance-tools-guest-20261104-01` and `host-payload-guest-tools-20261104-01`.
Download copies/logs are exported under
`/home/drew/orange-pi-4-pro-build/guest-tools-artifacts-20261104/`.
Raw roots remain in the protected build output; exported payloads contain the
compressed roots. Download the portable package to the provisioning machine:

```sh
mkdir -m 0700 guest-tools-cd80603
scp drew@build.drewnet.online:/home/drew/orange-pi-4-pro-build/guest-tools-artifacts-20261104/package/tools.tar.gz guest-tools-cd80603/
scp drew@build.drewnet.online:/home/drew/orange-pi-4-pro-build/guest-tools-artifacts-20261104/package/manifest.json guest-tools-cd80603/
cd guest-tools-cd80603
printf '%s  %s\n' e7495d225ef754a3ad591baecd098db7555a494dfe36d6b808de298be7f1ba07 tools.tar.gz | sha256sum -c -
printf '%s  %s\n' 6b448c939a105a6bc9296187f6677d2740bd7ed388a7fb695349609af967e9b8 manifest.json | sha256sum -c -
```

Rebuild from a protected complete checkout on fixed storage using fresh output
names and the existing pinned inputs:

```sh
sudo /usr/bin/python3.10 -I -B build/maintenance-tools.py \
  "$(pwd -P)/out/maintenance-tools-guest-20261104-01"
sudo /usr/bin/python3.10 -I -B build/host-payload.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/artifacts \
  "$(pwd -P)/out/host-payload-guest-tools-20261104-01"
sudo /usr/bin/python3.10 -I -B tests/verify_managed_payload.py \
  "$(pwd -P)/out/host-payload-guest-tools-20261104-01"
```

The root recipe retains the matching kernel/DTB/modules and immutable,
journal-free 4 GiB slot format. Root markers and the outer manifest bind exact
tool files, modes and entrypoints; provenance uses the same captured bytes.
Independent readback checks raw roots, compressed expansion, the checkout-derived
complete package, directory contents and root ownership/modes. The portable
archive and manifest match a fresh-checkout rebuild byte for byte. Full root
filesystem byte reproducibility is not claimed.

## ARM and release validation

The packaged probe authenticates the external manifest pin, exact tree and
archive, then compares captured bytes again before execution. Only the package
is staged at `/usr/lib/a733-tools`; no repository tree is placed in the chroot.
Actual ARM sysusers, enrollment, checkpointing, backup, local/HTTPS restoration,
full readback and unit parsing run. Hardware policy boundaries remain explicit
fixture substitutions. No guest or service is started.

```sh
sudo /usr/bin/python3.10 -I -B tests/verify_guest_tools_arm.py \
  "$(pwd -P)/out/maintenance-tools-guest-20261104-01" \
  --package-manifest-sha256 6b448c939a105a6bc9296187f6677d2740bd7ed388a7fb695349609af967e9b8 \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/recipe/out/guest-fixture-20261104-03
```

The earlier recovery SD, full-disk NVMe fixture and root components remain
separate pinned artifacts. Do not mix them into a per-installation candidate
without identity/compatibility checks. Deployment still needs the real admin
key, wired MAC and storage geometry. Actual board boot, guest networking/KVM,
zero SD writes, physical recovery and repeated acceptance runs remain open.

Evidence: [portable package](../../evidence/guest-tools-package-20261104.json),
[root components](../../evidence/guest-tools-payload-20261104.json),
[raw/compressed root verification](../../evidence/guest-tools-payload-verify-20261104.json),
[packaged ARM workflows](../../evidence/guest-tools-arm-20261104.json) and
[ARM maintenance compatibility](../../evidence/guest-maintenance-arm-20261104.json).

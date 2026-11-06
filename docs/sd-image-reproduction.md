# Frozen-builder SD image reproduction

The [executed replay](evidence/frozen-sd-replay-20261106.json) reproduces both
current deployment SD images byte for byte, including gzip files. The host and
installer raw images are each 268,435,456 bytes and fit the actual
1,977,614,336-byte SD. The published installation paths and hashes in
[personalized deployment](deploy-personalized-20261106.md) remain unchanged.
This result requires no new operator reflash.

The Git-owned [inner recipe](../build/replay-sd-images.py) ran successfully from
clean commit `de5eb78d02869de0ac0f02b7a26a2d97e9e07cf6` in the
[restored frozen builder](clean-builder.md). Kernel/module and U-Boot components
come from the separately verified fresh-source replays. The 169,852,145-byte
ARM64 userspace archive is an authenticated retained binary input; this command
does not reconstruct that userspace or the full NVMe image.

## Prepared namespace and inputs

Use the locked AMD64 Ubuntu 22.04 builder on `build.drewnet.online`. The executed
outer launcher retained private UID/GID mapping `1048576:65536`, private network,
no new privileges and the existing seven dropped capabilities. The OS, input
bundle, vendor checkout and kernel artifacts were read-only. Writable build
space and a separate private disk-backed `/var/tmp` were bound from the new
phase directory. The runtime `/tmp` was private and writable. The exact nspawn
argument vector and successful probe are retained in the evidence record.

| Namespace path | Prepared content |
| --- | --- |
| `/a733-debs` | `rootfs.tar.gz`, pinned ARM64 `busybox`, freshly reproduced EL2 `boot_package.fex`, authenticated `baseline-build-recipe.tar.gz` |
| `/a733-sources` | Freshly rebuilt `Image`, `board.dtb`, `kernel.config`, and `modules/lib/modules/` |
| `/home/drew/orange-pi-4-pro-build/vendor` | Fresh pinned vendor checkout containing A733 boot0 |
| `/a733-export-output/project` | New full committed project checkout with existing `out/` |
| `/var/tmp` | Private writable disk scratch for the standalone inspectors |

The actual input bundle is retained at
`/var/lib/a733-sd-replay-inputs-20261106` on the builder. Its provenance and core
hashes are in the evidence. The recipe authenticates all committed project files,
the exact 842-package builder profile, every core input and final raw target.
It requires at least 4 GiB disk free and 3 GiB RAM available. Kernel and NVMe
build guards remain separate and unchanged. Keep the outer resource monitor.

## Executed inner command

Inside that prepared namespace, this command passed:

```sh
/usr/bin/python3 -I -B /a733-export-output/project/build/replay-sd-images.py \
  /a733-debs /a733-sources \
  /home/drew/orange-pi-4-pro-build/vendor frozen-formal-20261106-01
```

Use a fresh checkout and tag when repeating; every existing output is refused.
The tag groups direct `out/` directories for installer runtime, both initramfs
files and both SD images. `out/sd-replay-TAG/result.json` records the source
commit, pins, package profiles, products and command logs. Failed output trees
are preserved. Both standalone SD inspectors run before the target comparisons.

The original diagnostic module-carrier archive is regenerated from its exact
pinned source archive and fresh modules. Its historical umask is `0002`; current
SD packaging uses `0022`. Authenticated archived permissions must be preserved:
the GPL notice is `0664`, not `0644`. The legacy archive supplies modules to the
current host/installer recipes; their current init scripts are regenerated.
The recipe fixes PATH to include `/usr/sbin` and `/sbin`, and the outer launcher
must supply writable disk-backed `/var/tmp`. Earlier failed attempts establish
these requirements and remain recorded in the evidence.

Independent review confirms both full raw/gzip hashes, all initramfs anchors,
1,462 installer-runtime entries and both native image inspections. The formal
recipe also has ten focused tests; repository checks pass 939 discovery cases
with 95 native skips, four diagnostic assembly cases and all five shell suites.
These are software results. Physical installation, SSH, actual KVM execution,
SD-write/endurance checks and independently reproduced deployment remain open.

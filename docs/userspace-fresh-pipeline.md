# Fresh interim userspace reconstruction

The [complete fresh pipeline](evidence/userspace-fresh-pipeline-20261106.json)
passes on the pinned frozen Ubuntu 22.04 builder. It authenticates the original
signed metadata and 220 raw package archives, extracts a new foreign tree,
configures it through ARM execution, and exports a normalized generic base.
The result matches the preceding normalized exports byte for byte.

No historical rootfs archive, configured tree or foreign tree seeds this run.
The existing raw package cache and frozen builder OS remain pinned inputs.
This establishes an interim userspace build; production root/state/NVMe images,
independent operator reproduction and physical acceptance remain open.

## Tracked phases and recorded invocation

Source revision `aa6922de0e9ab818343991215c92014e046e94d7` supplies:

1. [`prepare-host-userspace.py`](../build/prepare-host-userspace.py): authenticate
   the offline mirror and pinned stock adapter, then run native `--foreign`
   extraction only. No ARM maintainer scripts run in this phase.
2. [`configure-host-userspace.py`](../build/configure-host-userspace.py): validate
   foreign inputs, complete trusted device/API setup and configure exactly 220
   locked packages through the pinned ARM build interpreter.
3. [`export-host-userspace.py`](../build/export-host-userspace.py): remove generated
   identity/cache/build state, normalize later timestamps and fully verify the
   archive/BOM before exclusive publication.

These exact outer commands ran successfully on the prepared builder:

```sh
sudo python3 -I -B /tmp/a733-launch-userspace-foreign-recipe-20261106.py
sudo python3 -I -B /tmp/a733-launch-userspace-configure-fresh-20261106.py
sudo python3 -I -B /tmp/a733-launch-userspace-export-fresh-20261106.py
```

The outer wrappers and their runners/input manifests are retained with hashes
under the corresponding ignored codex output directories:
`out/userspace-foreign-recipe-20261106/`,
`out/userspace-configure-fresh-20261106/` and
`out/userspace-export-fresh-20261106/`. These commands record this prepared run;
the fixed phases now exist and must not be overwritten. A
[tracked fresh-tag launcher](userspace-pipeline-launcher.md) now consolidates
staging and phase sequencing; its actual execution is still pending capacity
before a clean-checkout build guide can be declared reproduced.

The new work tree is
`/var/lib/a733-clean-replay-20261106/userspace-foreign-recipe-01/work`.
Configuration consumes the freshly extracted tree there; the original retained
`userspace-foreign-01` is unchanged. The foreign-stage empty-status observation is
historical: this fresh tree is now configured and cleaned. Its foreign receipt
remains bound into the configuration contract and final export receipt.

## Inputs and isolation

The source manifest declares committed Git blobs, complete SHA-256 and lengths.
The native recipe checks every declared read-only file and rejects undeclared
project files/directories, including bytecode caches, before dependency imports.
It requires the recorded Ubuntu release/architecture, namespace UID/GID map,
no-new-privileges and seven dropped capabilities. It rejects loader/fakeroot/
bootstrap environment overrides and constructs the command environment explicitly.

Each phase uses a new namespace, read-only frozen OS and recipe inputs, private
network and writable disk-backed work/scratch bindings. The outer native wrapper
checks the exact 842-package installed host lock before creating its phase.
The later configuration wrapper retains the reviewed root helper handshake:
initial-user-namespace root performs the 54 device/API mount operations in the
payload's other namespaces, exits, then authenticates continuation. The payload
keeps its restrictions. No block device or `/dev/kvm` is exposed.

The source manifest, raw index and all source files are initial-root owned and
read-only. Real GPG verification and complete repository checks retain the signed
index unchanged. The foreign target has eight ordinary empty mode-0666 device
placeholders and empty proc/sys/API directories. The
[Ubuntu debootstrap manual](https://manpages.ubuntu.com/manpages/jammy/man8/debootstrap.8.html)
describes the foreign/second-stage split; the pinned adapter manifest records the
exact project overrides and stock source hashes.

## Actual results and independent review

The new repository manifest is byte-identical to the original authenticated one:
195,321 bytes, SHA-256
`b323d3fe609940f7c6d57ebf51bfa71271dfd143c81f8d09f5faf0a1ef81d9e9`.
All 669 BOM files and 220 package controls/hashes are checked. Foreign extraction
caches 99 archives and extracts 96 required packages; its dpkg status remains empty
until configuration begins. Configuration produces 99 base and 220 complete
installed locked tuples, empty `dpkg --audit`, successful `apt-get check` and no
APT `_apt` permission fallback. Only PIDs 1 and 2 remain at its final check.

The final archive is at
`out/userspace-export-fresh-20261106/rootfs.tar.gz` on codex and at
`work/export-fresh-01/rootfs.tar.gz` beneath the new builder work tree:

- Compressed **138,025,729 bytes**, SHA-256
  `98d6a57bbb70ff55bef32b6bd051ebfcb6bc0fad4f6c8b3167e8619b5a9d961e`.
- Raw tar **468,398,080 bytes**, SHA-256
  `a3b1ccfb69c24844d50cf60fb4c4286eb366dc6ba8da0f77ff672c8202b6e730`.
- **15,876 entries**, identical compressed/BOM bytes to canonical-01 and the same
  raw tar hash. The [normalization contract](userspace-export-reproducibility.md)
  preserves earlier timestamps and removes five generated identity/cache files.

Independent raw tar/PAX readback covers all file bytes/hashes, timestamps, modes,
owners, links, raw xattrs, three hardlinks and eight character headers. It also
verifies the complete foreign → configuration → export receipt chain, source
manifests, exact installed inventories, daemon suppression, host inventory and
process/mount cleanup. Locked root, empty machine ID and absence of generated
keys/build files are confirmed without exposing credential values.

The three phases record 14, 155 and 52 resource samples respectively. The minimum
free disk is **3,762,524,160 bytes** and available RAM **7,122,173,952 bytes** across
the pipeline. Verify per-phase counts/minima against the evidence before reuse.
Capacity was recovered through [verified file retention](userspace-rebuild-capacity.md);
no disk resize or guard reduction occurred. All 1,121 native discovery cases pass
with 96 environment skips, plus four assembly cases and all five shell suites.

The [explicit userspace profile](userspace-profiles.md) now permits both root
and state builders to select the normalized archive, with full staging evidence.
Their default remains the historical archive; published installation files are
unchanged. Next: execute the tracked outer launcher once capacity permits, rebuild
and completely inspect production roots/state/NVMe, and reproduce deployment
independently.
Follow the [existing deployment procedure](deploy-personalized-20261106.md) for
physical NVMe boot, SSH and actual KVM tests; those results remain pending.

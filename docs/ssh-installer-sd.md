# Private SSH RAM installer SD packager

`build/assemble-ssh-installer.py` packages a **PRIVATE same-board SSH RAM
installer**, with no device access or automatic erase. It preserves the paired
corrected VGIC SD's EL2 firmware, kernel and proven VGIC DTB. Its only profile is
`experimental-el2-vgic-v1`, kernel ABI `6.6.98-a733-diag`. The result remains
a construction artifact whose [physical RAM boot now passes](ssh-installer-remote-boot-20261108.md).
Final corrected-host and release acceptance remain open.

The archive includes the installed board's actual SSH host private key. Keep the
entire output private; never publish or commit its raw image, compressed image,
initrd or contents. A fresh `out/ssh-installer-sd-NAME` directory has mode 0700,
with files created under umask 077. Failed tags cannot be reused; retain their
assembly logs privately for diagnosis. Logs contain tool output, never archive
contents or private key bytes.

The initial implementation pins the full 256 MiB corrected VGIC image
`f02c74b4…`, its 17,471-byte manifest `47df42be…`, the firmware ranges, kernel,
DTB, first 32 MiB, and the private runtime archive and manifest. Full digests
are constants in the recipe. Authentication precedes JSON and CPIO parsing.
All inputs must be absolute nonsymlink single-link regular files; device,
`/proc` and `/sys` paths are refused. Open descriptors retain identity and hash
checks until completion. Extraction copies only the partition snapshot required
for bounded `debugfs` kernel/DTB reads. Firmware offsets are **8192** and
**16793600** bytes (the latter is `2050 * 8192`, never 20 MiB).

Use the prepared Ubuntu 22.04 builder with known fixed workspace storage, at
least **1.5 GiB scratch plus 256 MiB retained free space**, and **1 GB available
memory**. Runtime checks preserve those floors during child processes and image
compression/readback. The source checkout and builder are trusted and quiescent.
The shared `HOST.validate_builder_packages` checks the existing Jammy lock with
its cooperative, unbounded `dpkg-query`; explicit tool version queries have
30-second limits. Assembly is limited to 900 seconds and extraction/wrapping to
120 seconds. The manifest records the actual executable hashes and tool versions.
No DTC operation is needed because the input DTB is already patched and pinned.

Invoke the new recipe explicitly; the older installer packager has no profile
coercion or new default:

```sh
python3 build/assemble-ssh-installer.py \
  --image /absolute/paired-vgic/a733-host-sd.img \
  --parent-manifest /absolute/paired-vgic/manifest.json \
  --archive /absolute/private-runtime/initramfs.cpio.gz \
  --ssh-manifest /absolute/private-runtime/manifest.json \
  --name reviewed-candidate-01
```

The recipe executes a captured copy of `assemble-diagnostic.sh` with an explicit
captured `configs/installer-boot.cmd`. That RAM boot command uses `rdinit=/init`
and never mounts an SD filesystem. It does not use the paired host's state360d
boot command or select an NVMe state UUID. It does not change the host firewall
or initiate installation. The preserved maintenance tools remain manually
invoked; operator device checks and existing storage authorization still apply.

The compressed archive must expand to at most 256 MiB; its legacy ARM ramdisk
wrapper must remain below 64 MiB. The wrapper uses epoch 1793750400. Readback
checks MBR/partition layout, the entire unchanged 32 MiB firmware prefix, all
five boot files, exact wrapper headers/CRCs/payloads, and complete gzip image
expansion. The generated image is exactly 268,435,456 bytes and fits the actual
1,977,614,336-byte SD. Both original manifests are retained verbatim alongside
the result, which preserves historical producer receipts without representing
them as current source validation. The archive check verifies its full entry
manifest, embedded producer provenance and all 40 historical maintenance files.

`tests/test_assemble_ssh_installer.py` uses tiny fixtures only. Physical release
requires independent artifact review, successful ARM SSH/PAM validation, actual
RAM boot, both operator keys and host trust, idle NVMe and zero routine SD writes.
A real KVM guest remains a separate hardware gate. Neither successful packaging
nor emulation satisfies these requirements.

The pinned third runtime archive is **28,946,324 bytes**, SHA-256
`209257f1c240d6662831ce27f14e41947f859992d2ceffc11efd39b5bd94ea36`.
Its 664,617-byte manifest has SHA-256
`cc2f5f467d9dd1ab13fd7a1eaea21119dc29b171d8737b208e2d1f37528dc8a6`.
The actual ARM fixture passes host-key proof, locked-root PAM login, command and
PTY sessions, and wrong-key/password-only/forwarding rejection. Earlier failed
artifacts are retained for diagnosis. Independent SD artifact review and actual RAM boot now pass. Final deployment
and release acceptance remain separate gates.

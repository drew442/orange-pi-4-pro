# Bootstrap matched to the corrected host chain

A new **256 MiB** bootstrap has been constructed in the frozen Ubuntu 22.04
builder for the [corrected roots](production-host-roots-runtime-fixes.md) and
[matched state](production-host-state-runtime-fixes.md), UUID
`360dfb39-f49f-487e-b307-0bb887c854c5`. It fits the actual
**1,977,614,336-byte SD**. The producer's complete SD inspection and independent artifact review pass;
exact pins and proofs are in the [evidence](evidence/production-host-bootstrap-runtime-fixes-20261108.json).

This is a future matched component. **Do not flash it for the existing `00fd…`
NVMe installation.** Continue the separate [VGIC diagnostic SD trial](next-host-boot-20261108.md).
The current installation, published delivery endpoints and SPI remain unchanged
by this construction. Matched full-NVMe composition remains pending.

## Executed construction

Source `0a3d2a1ff78108e445d1c832362b2667897a9c9e` authenticates all **689**
tracked files. The [registered adapter](bootstrap-runtime-fixes-controller.md)
pins the historical controller, state adapter and complete accepted state
lineage; all historical predicates and execution checks remain. Independent
source review passed before commit. The actual state receipt, manifest and full
readback proof authenticate through the computed source policy and held files.
The committed CLI plan and complete privileged suite passed before construction:
**1,735 unit tests without skips**, five shell suites, five diagnostic assembly
cases and Bash syntax checks for 12 files. TOML and diff checks also pass.

```sh
sudo python3 -I -B \
  /var/lib/a733-bootstrap-runtime-fixes-project-20261108-01/project/build/launch-host-bootstrap-runtime-fixes.py \
  --project /var/lib/a733-bootstrap-runtime-fixes-project-20261108-01/project \
  --tag timesync-runtime-fixes-01 \
  --userspace /var/lib/a733-sd-replay-inputs-20261106/rootfs.tar.gz \
  --diagnostic /var/lib/a733-bootstrap-inputs-20261107 \
  --busybox /var/lib/a733-sd-replay-inputs-20261106/busybox \
  --state-receipt /var/lib/a733-clean-replay-20261106/host-state-timesync-runtime-fixes-02/receipt.json \
  --state-receipt-sha256 a25aa352b42bc49c13218d61587ea68636e83d21b77a34af52c6fd68db12ae88
```

The same command with `--plan` passed first. Its phase is
`/var/lib/a733-clean-replay-20261106/host-bootstrap-timesync-runtime-fixes-01`;
this tag is consumed. SD and initramfs outputs are respectively
`work/project/out/host-sd-timesync-runtime-fixes-01` and
`work/project/out/host-initramfs-timesync-runtime-fixes-01`.
Receipt SHA-256 is
`f084529efc51b880119c4a425194914e196f372d3cbc7bd6b2f2a64243f20ba5`.

The baseline archive supplies locked initramfs storage tools only. The
221-package time-enabled archive remains the separate root/state userspace.
Firmware, kernel, DTB and module carrier remain the compatible diagnostic
inputs; experimental EL2 uses service `0x8000ff40`, SPSR `0x3c9`.
**This bootstrap does not include the separate VGIC diagnostic DT change or
NVMe-first U-Boot.** Acceptance of those changes still requires hardware proof
and a separately matched integration. Package-state ABI remains
`a733-package-state-v1`; it is distinct from the time-enabled management profile.

## Artifacts and limits

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `a733-host-sd.img` | 268,435,456 | `1063c591c3955e1e8531c8b22960bc09eb080024a6e79d331846572c3e0d2f76` |
| `a733-host-sd.img.gz` | 17,271,950 | `90d5aacb8f38c4afbb260712d01d119d83c324cb8b9bf89f6b96fd0006adc686` |
| `uInitrd` | 4,762,436 | `b10bdad8671a32b4157bf6f7ff553618024fc928e59caf691b529c8658b12d16` |

Producer and independent inspection check MBR/raw blobs, kernel/DTB, boot script CRC and exact
contents, legacy ext4 features, initrd wrapper, all **424 newc entries**, state
UUID and complete gzip expansion. Independent `e2fsck -fn` exits zero with
nine inherited timestamp warnings and no structural filesystem error. No image
was mounted or repaired. Personalization intentionally changes the
initramfs and wrapper bytes; no byte-identical personalized repeat is claimed.

Admission required **4,665,083,250 bytes**. Sampled minima were
**18,639,495,168 free disk bytes** and **7,403,929,600 available RAM bytes**.
All 842 builder packages remain unchanged, with no mapped processes or phase
mounts remaining. Final builder free space was **18,874,372,096 bytes**, below
the unchanged **22 GiB** full-NVMe admission guard before staging. Fresh verified
retention is needed; no guard reduction or further LV growth is authorized.

Records and sparse raw/gzip/initramfs artifacts are retained under
`out/bootstrap-runtime-fixes-20261108/generated`. All copied bytes match their
producer manifest pins. Native retention keeps its separate 256 MiB free-space
floor. Generated files remain outside Git.

This build establishes no physical boot, automatic time, masked-unit behavior,
complete KVM guest, zero routine SD writes, SPI boot/recovery, update/rollback,
endurance or independently reproduced deployment result.

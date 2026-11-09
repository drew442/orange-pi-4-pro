# Protected STATE NVMe BOOT component

`build/protected-state-nvme-boot.py` creates a regular-file BOOT component for
Orange Pi 4 Pro A733. It changes only the reviewed STATE-protected init and
preserves the corrected VGIC DTB, kernel, complete BOOT namespace and NVMe boot
commands. It writes no devices and creates no full NVMe image. ROOT-A, ROOT-B,
STATE, GUESTS and GPT are outside its output scope. The 2 GB SD, nominal 250 GB
NVMe, minimal host and zero routine SD write requirements remain unchanged;
physical boot and an actual KVM guest remain acceptance gates.

Run from a trusted, quiescent checkout on the qualified builder with a fresh tag:

```sh
python3 build/protected-state-nvme-boot.py \
  --base-gzip /absolute/retained/nvme.img.gz \
  --base-manifest /absolute/retained/manifest.json \
  --output "$PWD/out/protected-state-nvme-boot-FRESH"
```

The producer authenticates the complete compressed input before parsing:
545,632,496 bytes, SHA-256
`3e560264bb64a9bc23b342febdff2146955ca97cb6e0279e5240c29d4e6d85bc`.
The exact historical corrected construction manifest is 35,023 bytes, SHA-256
`addfcafe239a2aa075435383cc4742869d256d98beecd2526806da206d98f4a2`;
the local retained authority is `out/vgic-nvme-image-20261108/build-02.stdout`.
Its historical full raw is 256,060,514,304 bytes, SHA-256
`835031b81d0320d6f9f35e74b9be67b494006c26a25fa9070f3e0a8f434ebc11`.
There are no pin overrides or arbitrary initrd inputs.

Only the decoded prefix through byte 537,919,488 is consumed. The 512 MiB BOOT
extent starts at byte 1,048,576; its independently measured fixed hash is
`5860889755d7479e619e86b83bc32237e51bacd3a3fd2e2d2ebe976898f44899`.
The coordinator measured that extent read-only on 2026-11-09 from the retained
builder `nvme-vgic-corrected-02/nvme.img`, with unchanged held-file identity.
The producer checks this extent hash before filesystem parsing, then compares
the complete namespace to the authenticated historical manifest. It does not
freshly expand the full 256 GB raw, check the whole gzip trailer, or verify bytes
outside BOOT. The fixed complete compressed hash and historical full expansion
evidence provide that provenance; the output manifest states this distinction.

The new uInitrd is derived using the same exact helpers as
[the SD producer](protected-state-bootstrap.md): bounded legacy-header CRC,
single gzip/newc validation, replacement of only init data/size, unchanged ARM
BusyBox and exact STATE UUID. Source init SHA-256 remains
`2cf52e1cee8d4f2050fe3a90eca0a0a8c50b6f30718d210667f7273f9f08ab12`.
The resulting 4,763,312-byte uInitrd must match the constructed SD pair,
SHA-256 `2149513ae5cf90f1edb6047e6ebf8ba903dc86c55b4b43754a5a2cfd3368c786`.
All imported recipe files and init are held and reauthenticated before publication.

NVMe `boot.cmd` remains `c9f0db91…ce6c26` and `boot.scr` remains
`d82fd8c4…56c938`, as pinned in the authenticated manifest. SD commands are
never copied. The complete before/after namespace comparison permits only
uInitrd data/size changes. It preserves uid/gid/mode and all four inode timestamps,
checks xattr absence, and runs read-only `e2fsck`. Ext4 allocation/bookkeeping
bytes can change.

Outputs are `boot.ext4`, deterministic `boot.ext4.gz`,
`reverse-boot.ext4.gz`, `uInitrd`, `initramfs.cpio.gz`, `patch.log` and
`manifest.json`. Both compressed BOOT capsules receive complete expansion/hash
checks. The reverse capsule is the retained construction's BOOT, **not a backup
of the personalized installed board**. A separate reviewed offline updater must
validate live target identity/bounds/idle state and preserve a live rollback
capsule before any physical deployment. Never replace the personalized full NVMe
with the unpersonalized construction image for this fix.

Admission requires 3,000,000,000 free disk bytes plus 2 GiB scratch budget and
1,000,000,000 available memory bytes. The budget covers one raw BOOT, two
worst-case gzip BOOT capsules, bounded namespace/archive work and headroom.
The existing full-NVMe producer's admission guard remains unchanged.

Focused validation:

```sh
python3 -m unittest discover -s tests -p test_protected_state_nvme_boot.py
```

Five tests cover exact bounded extraction including zeros, truncation, fixed-wire
rejection and input mutation, unsafe paths, output/resource guards, wrong NVMe
commands/VGIC/STATE/layout/namespace and a real 16 MiB regular ext4 replacement
with complete metadata preservation. Shared SD fixtures cover archive/wrapper
validation. These software checks do not establish full candidate construction,
physical dirty-STATE recovery, SD-write, NVMe boot or KVM acceptance. Independent
review remains required before integration/deployment.

Two fresh constructions on the qualified Jammy builder now match in raw BOOT,
gzip, reverse capsule and initramfs bytes. Independent actual artifact review
passes; see [construction evidence](evidence/protected-state-nvme-boot-20261109.json).
Physical installation and acceptance remain pending.

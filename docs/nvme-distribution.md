# Compressed NVMe payloads

`build/nvme-bundle.py` turns an authenticated builder disk image into a gzip
transfer bundle without copying its 250 GB expanded contents. It reads every
logical source byte, including sparse zero ranges. It preserves the source
manifest, including absent or false installation intent; compression cannot
make fixture credentials or network settings deployable. The
[writer](nvme-installation.md) independently refuses those fixtures.

The bundle contains `nvme.img.gz`, `manifest.json` and `manifest.sha256`.
The manifest records both encoded and expanded sizes/hashes, the source
manifest digest, active compression runtime, exact builder package versions,
recipe hashes and verification performed. Its digest must reach the operator
through an authenticated builder/release channel. A checksum downloaded beside
an untrusted payload does not authenticate that payload.

## Build contract

Use the prepared Ubuntu 22.04 builder on known fixed storage. Invoke the pinned
system interpreter in isolated mode:

```sh
sudo /usr/bin/python3.10 -I -B build/nvme-bundle.py \
  /absolute/source/nvme-image-directory \
  "$(pwd -P)/out/nvme-bundle-UNIQUE-NAME"
```

The output parent must already exist. The recipe refuses existing output,
symlink paths, recognizable MMC/removable/RAM backing and unsupported storage.
It requires at least 2 GiB free for a normal image, caps encoded output against
the measured budget and leaves a private incomplete directory on failure.
It does not reserve free space against other processes; monitor the builder.
An SD reader that presents fixed sdX storage cannot always be recognized, so
the operator must locate the checkout on known builder storage.

Gzip uses level 6, modification time zero and no original filename. The source
is held open and checked for metadata changes. After synchronization, the
recipe hashes the encoded file and independently expands the complete stream
with an explicit expanded-byte bound, checking its hash and gzip integrity.
Only then does it publish the success manifest. It creates no physical-device
output and claims no release signature, firmware license clearance or hardware
acceptance. Deterministic gzip does not make freshly generated disk/filesystem
identities deterministic.

The active interpreter, gzip implementation and loaded zlib must match the
pinned Jammy profile; merely having those packages installed is insufficient.
Their versions, ownership and file hashes are retained. This checks the prepared
builder, not acquisition of a clean immutable builder environment.

## Acquisition and installation boundary

Keep the compressed payload on identified external storage, or explicit RAM
storage only after measuring enough available capacity. The installer image's
`/run` and `/tmp` are each limited to 64 MiB. Never stage the payload on the 2 GB
SD or expand it to a 250 GB temporary file. No acquisition step selects,
formats, mounts or authorizes erasure of a physical device.

The guarded writer accepts gzip directly. It authenticates both encoded and
expanded contents against the externally trusted manifest digest, resolves
source backing storage to reject target overlap, and separately requires exact
target identity and interactive authorization. Download success is not an
installation plan or authorization. The current fixture remains ineligible.

Network acquisition and physical board networking are separate validation
gates. Builder file verification and loopback software probes cannot establish
A733 Ethernet, DHCP, DNS, TLS, installed boot, zero SD writes or KVM execution.

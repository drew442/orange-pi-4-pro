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

## Verified fixture bundle

The full-size fixture build passed on Jammy using recipe bytes matching commit
`03dcd62`. The [manifest](evidence/nvme-bundle-20261104.json) preserves its older
source manifest's absent deployment intent; the writer therefore refuses it.
It uses the independently inspected raw fixture from `c368563`, not real
administrator/network or SSD settings.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `nvme.img.gz` | 630,156,553 | `399f27eac0fcfaad3cb85a564cff794d35f7b65e2cb9e627f8f7782ee7f8a972` |
| Expanded image | 250,000,000,000 | `6aa51794bf82b3814568a526c9242b5abbe041af603255d2b3b9438847fc4d98` |
| `manifest.json` | 12,365 | `443971233d9aa955dfdc581693fe581d8f784246d05f08852f39899785d765e9` |

Builder source/output directories are
`/home/drew/orange-pi-4-pro-build/bundle-work-20261104/` and
`out/nvme-bundle-250GB-20261104/` beneath it. The exact command that passed was:

```sh
sudo /usr/bin/python3.10 -I -B build/nvme-bundle.py \
  /home/drew/orange-pi-4-pro-build/nvme-clean-20261104/out/nvme-image-250GB-repeat-20261104 \
  "$(pwd -P)/out/nvme-bundle-250GB-20261104"
```

Use a new output name when repeating. The monitored run took about 39 minutes
for compression, complete source hashing and full expansion verification on
this builder. Gzip determinism has small-file repeat tests; a second full-size
compression has not been run. Manifest provenance includes source identities
and the free-space budget, so independent staging need not reproduce its bytes.

Readable transfer files are under builder
`/home/drew/orange-pi-4-pro-build/bundle-artifacts-20261104/`. Downloading the
fixture is optional and authorizes no installation:

```sh
mkdir -m700 a733-nvme-fixture
scp drew@build.drewnet.online:/home/drew/orange-pi-4-pro-build/bundle-artifacts-20261104/nvme.img.gz \
  drew@build.drewnet.online:/home/drew/orange-pi-4-pro-build/bundle-artifacts-20261104/manifest.json \
  a733-nvme-fixture/
cd a733-nvme-fixture
printf '%s  %s\n' 443971233d9aa955dfdc581693fe581d8f784246d05f08852f39899785d765e9 manifest.json | sha256sum -c -
printf '%s  %s\n' 399f27eac0fcfaad3cb85a564cff794d35f7b65e2cb9e627f8f7782ee7f8a972 nvme.img.gz | sha256sum -c -
```

These commands write ordinary files on the chosen provisioning computer. Use
known non-SD storage with sufficient free space. Keep gzip intact for the
writer; do not expand it to a 250 GB temporary file.

The [full-size native HTTPS check](evidence/nvme-bundle-https-20261104.json)
then passed from commit `05ff311`: it served this private bundle over temporary
loopback TLS and exercised the complete acquisition/storage guard, encoded
download/readback and full expanded hash. No raw temporary image was created.
The acquired manifest bytes matched exactly and the writer's eligibility
validator still refused the legacy fixture. It took about 12 minutes.
[Resource records](evidence/nvme-distribution-resources-20261104.json) retain
sampled minima and exact commands; raw samples remain outside Git.

The native test command was run on the prepared builder from its verified source
snapshot, with a new destination on known fixed storage:

```sh
sudo /usr/bin/python3.10 -I -B tests/verify_nvme_bundle_https.py \
  /home/drew/orange-pi-4-pro-build/bundle-work-20261104/out/nvme-bundle-250GB-20261104 \
  "$(pwd -P)/out/payload-acquired-250GB-20261104"
```

The harness computes its pin from the trusted local builder manifest; it does
not reproduce distribution to another operator or validate the production
writer's prepare/apply path. It removes its certificate/private key and listener,
retaining acquired ordinary files for inspection. ARM transport used a small
loopback fixture; full-size acquisition has not run on ARM or the A733.

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

## Verified HTTPS acquisition tool

`scripts/acquire-nvme.py` downloads into a new private directory and authenticates
the manifest before parsing it. It requires HTTPS with certificate/hostname
verification, refuses redirects and ignores ambient proxy variables. Requests
have bounded reads and a total deadline. Current source authenticates encoded
size/hash while downloading and publishes `encoded_verified=true` and
`decoded_verified=false`. It performs no decompression or stored-file readback.
Expanded size/hash and gzip CRC are checked during the writer's single write
pass. Download and authentication progress appear on stderr. Older published
installer images still perform the earlier full expansion; the
[refreshed installer](installer-refresh-20261107.md) includes the faster behavior. It retains the
manifest's exact authenticated bytes. Receipt URLs omit query strings, which
may contain credentials. Keep credentials out of URL paths as well.

After arranging an authenticated HTTPS endpoint and obtaining its manifest
digest independently, the installed preparation-console command is:

```sh
/usr/bin/python3 -I -B /usr/lib/a733-installer/acquire-nvme.py \
  --manifest-url "$TRUSTED_MANIFEST_HTTPS_URL" \
  --image-url "$TRUSTED_IMAGE_HTTPS_URL" \
  --manifest-sha256 "$TRUSTED_MANIFEST_SHA256" \
  --output "$NEW_ABSOLUTE_PAYLOAD_DIRECTORY" \
  --storage-kind external
```

From a repository checkout, replace the script path with
`scripts/acquire-nvme.py`. These variables are operator inputs, not a configured
production endpoint. No production distribution service exists yet. An explicit
`--ca-file` can add a trusted private CA; certificate verification cannot be
disabled. The clock must be correct for TLS. Use `--max-seconds` to adjust the
default 3,600-second acquisition/verification allowance, up to 86,400 seconds.
The Linux main-thread alarm bounds slow HTTP headers as well as transfer and
verification. The tool refuses an existing, blocked or pending alarm and
restores its handler afterward. Some libc DNS waits and uninterruptible kernel
operations can defer signal delivery; the allowance is not a process-kill
guarantee for those operations.

`external` requires supported local block-backed storage and refuses MMC,
loops and unknown backing topology, including MMC behind a device mapper.
It allows removable USB storage, whose identity the operator must verify;
some USB SD readers are indistinguishable. It does not identify the future
installation target. The writer separately refuses source/target overlap.
Private directory/file permissions must work on the mounted filesystem;
incompatible FAT/exFAT mount masks are refused. Multi-device btrfs is refused.
`--storage-kind ram` requires an already prepared tmpfs/ramfs with enough free
space. The tool never mounts or enlarges storage automatically.

Failures retain private incomplete files without a successful manifest/receipt.
Retry with a new output directory. A receipt records software verification,
not target authorization or physical installation; never edit a fixture into
a deployment candidate. Downloads and full verification can take minutes even
when compressed contents are small relative to the expanded disk.

The implementation follows Python's [gzip stream interface](https://docs.python.org/3.10/library/gzip.html),
[verified TLS contexts](https://docs.python.org/3.11/library/ssl.html#ssl.create_default_context)
and [explicit URL handlers](https://docs.python.org/3.11/library/urllib.request.html).
The file/hash checks and externally supplied digest establish the acquisition
contract; loading CA data alone is not evidence of a board TLS connection.

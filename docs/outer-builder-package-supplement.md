# Retained outer-builder package supplement

The seven November 11 updates accepted by the deterministic-root launcher now
have exact retained binaries and signed repository metadata. This is acquisition
and input custody only. It establishes neither a freshly provisioned native outer
host nor image construction or physical acceptance.

The [machine lock](../configs/builder-outer-package-supplement-20261113.lock.json)
records all seven package/version/architecture tuples, filenames, sizes and
SHA256 values; complete acquisition and copy checks are in the
[evidence](evidence/builder-outer-package-supplement-20261113.json).
The exact current outer inventory remains 842 packages, SHA256 `f779568a3e4e82afc488c1b99ccad0a88f2d96e74efe3b00172927c7539f4ef3`.
The historical frozen replay and binary archive locks are unchanged.

## Retained contents and authentication

The builder directory is
`/home/drew/orange-pi-4-pro-build/outer-package-supplement-20261113-02`.
Its independently rehashed and fsynced second copy is
`out/builder-outer-package-supplement-20261113-02/` on codex.
Each contains seven binaries totaling **11,606,666 bytes**, both complete copied
InRelease files and decoded main Packages indexes, the copied Ubuntu archive
keyring, signature logs, decoded Release bodies, exact tuple verification logs,
inventory snapshots, acquisition script and receipt. The 35 retained files total
55,885,427 bytes. Raw metadata and binaries remain outside Git.

The copied updates and security InRelease signatures verify with the exact copied
builder keyring and fingerprint
`F6ECB3762474EDA9D21B7022871920D1991BC93C`. Both decoded index sizes and SHA256
values match their signed Release SHA256 sections. All seven exact stanzas agree
between indexes and retain the original read-only audit pins. Each acquired
binary passes its full signed size/SHA256 and `dpkg-deb -f` package, version and
architecture checks. The outer installed inventory is unchanged before/after.

A separate native verification checks both signatures using the existing codex
keyring and checks both decoded indexes against those signed releases. Its
3,607-byte keyring differs from the builder's 7,399-byte keyring. Attempt `-01`
incorrectly required the codex keyring pin on the builder and refused before
metadata copy or downloads. Both hosts retain that failed receipt; no package or
keyring drift is inferred.

## Repeating the bounded acquisition

On the same approved native builder, use the retained script with a fresh absent
output path under the existing build directory:

```sh
python3 -I -B \
  /home/drew/orange-pi-4-pro-build/outer-package-supplement-20261113-02/acquire.py \
  --output /home/drew/orange-pi-4-pro-build/outer-package-supplement-NEW
```

The script preserves the exact current outer inventory guard, audit metadata
hashes and signer fingerprint. It requires more free disk and available memory
than 256 MiB plus retained source metadata and binary bytes. It refuses changed
APT metadata, an existing destination or differing keyring instead of silently
selecting new versions. Re-audit explicitly if cached metadata has advanced.
It copies/fsyncs the full signed metadata before acquiring only the seven pinned
HTTPS security.ubuntu.com filenames. Downloads run sequentially with redirects
refused, bounded retries/timeouts and exact maximum sizes. Files become mode
0400 within private mode0700 directories. Failures retain their receipt and
before/after inventory observations. The receipt retains the actual commands.
No package is installed or extracted and no maintainer script runs.

## Reproduction boundary

The other **835 tuples** share historical binary archive pins. This acquisition
did not rehash their bytes or establish a completely reverified 842-package cache.
The historical `linux-firmware` archive was separately retained; follow its
[restoration requirement](evidence/builder-deb-retention-20261107.json) before
reconstructing the complete historical cache. Preserve the original caches.

The current launcher constructs fresh regular-file roots on the exact approved
native Ubuntu 22.04 AMD64 outer host using the frozen inner builder. Its initial
root/user-namespace requirement remains intact. The historical
`fetch-builder-packages.py`, `clean-builder.py` and `install-builder-packages.py`
APIs still require the old frozen replay inventory; this supplement adds no
alternate inventory or provisioning API. Independently provisioning a new native
outer host requires a separate reviewed installation/configuration procedure
and execution evidence. This archive supplement does not satisfy that gate.

The A733, nominal 250 GB NVMe, 2 GB SD recovery card, minimal host, zero routine SD
writes and actual KVM hardware acceptance requirements remain in force.

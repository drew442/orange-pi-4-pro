# STATE pairing for resolver-readable roots

The registered launcher and full reader prepare a STATE regular-file payload
for [resolver-readable root construction 01](public-config-roots-20261109.md).
[Independent reviews and 2,155 privileged Ubuntu tests](evidence/public-config-state-preparation-20261109.json)
pass. These software checks and parent evidence do not establish a new installation,
physical resolver repair or release acceptance. [Construction, full readback and independent artifact review](evidence/public-config-state-build-20261109.json)
now pass. The running endurance host remains unchanged.

The fixed [parent binding](evidence/public-config-roots-state-binding-20261109.json)
authenticates the actual root producer receipt, root manifest, expected trees and
complete filesystem readback. The new controllers retain SHA-pinned historical
execution bodies: the launcher changes five registered string constants and the
reader four. They preserve source closure, UUID guards, isolation, exact input
hashes, publication checks and resource floors. Their provenance field is
`public_dirs_controller`. Old registrations remain byte-for-byte unchanged.

The intended STATE UUID is `360dfb39-f49f-487e-b307-0bb887c854c5`, matching the
current protected BOOT. This is a new distribution component for the same
installation identity; it does not write or reuse the mounted physical STATE.
Its selector must authenticate new root A marker `286494…`, rather than the
older distribution's `e1cc…` marker. The root package-state profile and immutable
kernel/firmware interfaces remain compatible.

## Recorded build invocation

Commit both controllers and their binding in the same authenticated source
checkout before constructing STATE; the reader authenticates its presence in
the producer's source closure. The actual invocation used source `3f923a887f8cb2b51f17d9d255faf84b48734401`:

```sh
sudo python3 -I -B \
  /var/lib/a733-public-config-state-source-20261109-01/project/build/launch-host-state-public-dirs.py \
  --project /var/lib/a733-public-config-state-source-20261109-01/project \
  --tag timesync-public-dirs-01 \
  --userspace /var/lib/a733-clean-replay-20261106/userspace-timesync-account-dates-01/work/export-timesync-result/rootfs.tar.gz \
  --config /home/drew/orange-pi-4-pro-build/operator-inputs-20261105/management.json \
  --admin-keys /home/drew/orange-pi-4-pro-build/operator-inputs-20261105/admin.pub \
  --roots-receipt /var/lib/a733-clean-replay-20261106/host-roots-timesync-public-dirs-01/receipt.json \
  --roots-receipt-sha256 81d700ba1c9414e0e794bdf9f8dfb71872c88f2b025f14dfa691d9612f3eee93 \
  --state-uuid 360dfb39-f49f-487e-b307-0bb887c854c5
```

An actual `--plan` preceded construction. Use `--plan` first for later builds. The controller authenticates the original two public keys,
management inputs, userspace and reviewed root. An existing phase refuses reuse;
a later construction needs a fresh tag. Fresh machine identity intentionally
changes between STATE constructions. SSH host private keys remain absent;
this unpersonalized component is not a backup of the live board.

The actual complete reader passed for producer receipt
`26375aeb3d2510f75af489a5e30da25dfb931232c5c483f5f18cd83fe4962561`.
For later constructions, authenticate the actual producer receipt and invoke
`build/verify-host-state-public-dirs.py` with `--receipt`, the observed
`--receipt-sha256` and a fresh `--output` outside the producer phase. Serialize
input-ancestor directory changes while the strict full reader is active.
The reader checks the complete filesystem plus selected-root marker, operator
configuration/key fingerprints, ownership, attributes and clock-state policy.
Independent actual-artifact review also passes for all 38 artifacts, all 857
source files and the complete gzip expansion. All 1,172 expected filesystem
paths and 7,337,572 regular bytes pass full readback; direct critical-file
extraction separately checks identity, keys, clock state and selector.
The 8 GiB raw STATE hash is
`2e9d3dcf4dcabfaead65e125e7778c0aed092eeb665e67f3559719d08e3564ec`;
its 10,009,266-byte gzip and manifest pins are in the linked evidence.

The new full distribution must combine these matched roots and STATE with the
existing protected BOOT, updating partition hashes and selector metadata.
Preserve original board identity and current guest backups separately before
any later physical installation. Installation readback remains optional; builder
artifact verification is separate from work performed on the Orange Pi.

# STATE pairing for resolver-readable roots

The registered launcher and full reader prepare a STATE regular-file payload
for [resolver-readable root construction 01](public-config-roots-20261109.md).
[Independent reviews and 2,155 privileged Ubuntu tests](evidence/public-config-state-preparation-20261109.json)
pass. These software checks and parent evidence do not establish a new installation,
physical resolver repair or release acceptance. New STATE construction is still
pending; the running endurance host remains unchanged.

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

## Prepared build invocation

Commit both controllers and their binding in the same authenticated source
checkout before constructing STATE; the reader authenticates its presence in
the producer's source closure. The prepared invocation is:

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

Use `--plan` first. The controller authenticates the original two public keys,
management inputs, userspace and reviewed root. An existing phase refuses reuse;
a later construction needs a fresh tag. Fresh machine identity intentionally
changes between STATE constructions. SSH host private keys remain absent;
this unpersonalized component is not a backup of the live board.

After construction, authenticate the actual producer receipt and invoke
`build/verify-host-state-public-dirs.py` with `--receipt`, the observed
`--receipt-sha256` and a fresh `--output` outside the producer phase. Serialize
input-ancestor directory changes while the strict full reader is active.
The reader checks the complete filesystem plus selected-root marker, operator
configuration/key fingerprints, ownership, attributes and clock-state policy.
Independent actual-artifact review is also required before full-image packaging.

The new full distribution must combine these matched roots and STATE with the
existing protected BOOT, updating partition hashes and selector metadata.
Preserve original board identity and current guest backups separately before
any later physical installation. Installation readback remains optional; builder
artifact verification is separate from work performed on the Orange Pi.

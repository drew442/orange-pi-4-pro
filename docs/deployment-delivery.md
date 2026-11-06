# Authenticated LAN deployment delivery

The prepared helper serves a verified compressed installation bundle over HTTPS
on `192.168.1.132:8443` (codex). It has passed native TLS tests and independent
review. The personalized bundle is complete and the service is running. A full encoded
LAN transfer from the build host passed pinned-CA/IP verification, length and
SHA-256 checks. Actual board transfer remains unperformed. Follow the
[concrete deployment procedure](deploy-personalized-20261106.md), which preserves
the user's existing authorization for the exact recorded NVMe.

The operator obtains the public delivery certificate and manifest SHA-256 through
the trusted serial/repository instructions. Copying an unverified endpoint's CA
and trusting it would defeat that bootstrap. On the board, correct UTC first,
check the pasted certificate's SHA-256, then use the existing acquisition tool
with `--ca-file`. Manifest authentication and complete encoded/expanded image
checks remain mandatory. Never disable payload certificate verification.

The current delivery certificate has IP SAN `192.168.1.132`, validity from
2026-11-06 07:42:33 UTC through 2026-11-20 07:42:33 UTC, and PEM SHA-256
`4c71c2528ab650b2f9b8dc2a4554ed6248d77461abb7191bb58dd030a9eb9b56`.
Regenerating it requires updated out-of-band instructions and a new hash.
Its private key remains in ignored `out/deployment-delivery-20261106/private/`;
only `delivery-ca.pem` belongs in the public directory.

The active service was started with this command; run it in a separate terminal
only if the existing endpoint is no longer listening, keeping the verified
public files and certificate unchanged:

```sh
python3 -I -B build/serve-deployment.py \
  --directory "$PWD/out/deployment-delivery-20261106/public" \
  --certificate "$PWD/out/deployment-delivery-20261106/private/server.crt" \
  --key "$PWD/out/deployment-delivery-20261106/private/server.key" \
  --bind 192.168.1.132 --port 8443
```

Startup authenticates `nvme.img.gz` against the manifest and holds the opened
file. Requests serve only GET/HEAD for `nvme.img.gz`, `manifest.json` and
`delivery-ca.pem`; there is no directory listing, upload or private-key route.
Ancestor symlinks, traversal, extra URL components and source metadata changes
are refused. TLS requires version 1.2 or newer. A single client and bounded
buffers limit resource use. The ten-second socket timeout applies per operation;
a continuously slow client can still monopolize this temporary endpoint.

Tests cover real localhost TLS, rejection of untrusted CA, allowlist and method
checks, symlink/privacy guards, image mutation and idle-handshake recovery.
These are software checks; actual board transfer and installation remain
separate hardware evidence. Stop this temporary endpoint after deployment and
retain the bundle, certificate/hash record and captured installation reports.

The manifest SHA-256 is
`bdd3c8f103e08fc1b759ca97e631ee20255d43aefe42abcce5361614a41b041e`.
The 547,077,631-byte encoded image SHA-256 is
`4d1145c5ff8689c1e8ca847e39551eb36f54e703a2843f35fd0ce7ec7574b526`.
The builder verified its complete 256,060,514,304-byte expansion. The retained
[receipt](evidence/personalized-nvme-20261106.json) separates that verification
from the native LAN transfer and still-pending board result.

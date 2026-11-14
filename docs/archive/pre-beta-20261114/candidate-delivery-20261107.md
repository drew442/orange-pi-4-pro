> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Second-candidate LAN delivery

The [verified second NVMe candidate](../../production-nvme-repeat.md) is available at
**`https://192.168.1.132:8444`** on codex. The
[delivery evidence](../../evidence/candidate-delivery-20261107.json) records actual
complete acquisition from the Ubuntu builder into RAM, TLS verification and
cleanup. The [matched deployment guide](../../deploy-candidate-20261107.md) now contains
exact URLs and trusted public CA bytes. This is delivery preparation, not physical
deployment, boot, KVM or release acceptance.

The current board action remains the
[existing-install permission repair](../../installed-host-repair-20261107.md), then
booting its original matching host SD and returning serial/DHCP/SSH results.
Do not reinstall to fix management.

| Port | Manifest SHA-256 | State UUID |
| --- | --- | --- |
| 8443, original deployment | `bdd3c8f103e08fc1b759ca97e631ee20255d43aefe42abcce5361614a41b041e` | `00fd936c-49fb-4fea-87c8-928b9c1599e0` |
| 8444, second candidate | `c0f036dcc5d300a24785ebaac4c438bb0aef7cf1b8b073dc97a2d15a7896f563` | `feaaee34-d782-4c18-bd04-1029540ab26a` |

Both endpoints were checked independently over verified TLS. The original
manifest, payload content length and process remain unchanged. The candidate
endpoint serves only `/manifest.json`, `/nvme.img.gz` and `/delivery-ca.pem`;
unknown paths return 404. No directory listing or private key is served.
The new gzip is 545,633,737 bytes, SHA-256
`4b6cbe6eb16cc3d3cd236b0d8701908408d38e5993d5aca5ffb4882708766df0`.
The shared certificate's IP SAN matches `192.168.1.132`, and its PEM SHA-256 is
`4c71c2528ab650b2f9b8dc2a4554ed6248d77461abb7191bb58dd030a9eb9b56`.
Its validity ends **2026-11-20 07:42:33 UTC**; renewal must update the trusted
instructions before further use. Do not disable clock, hostname or CA checks.
The public CA copy adds under 1 KiB; the gzip is served directly from its retained
regular file without copying, hardlinking or changing its bytes/link count.
Private TLS material remains outside the served directory and Git.

## Service operation on codex

The unchanged [bounded server](../../../build/serve-deployment.py) runs as `drew`,
binds only the explicit LAN address and authenticates the full gzip before
listening. It holds source descriptors and checks their identities while
serving. The two services use different ports. No firewall or existing service
configuration was changed; availability is verified now, not promised after a
host reboot. If the candidate listener is absent, restart it in a dedicated
terminal with:

```sh
cd /home/drew/orange-pi-4-pro
python3 -I -B build/serve-deployment.py \
  --directory /home/drew/orange-pi-4-pro/out/nvme-repeat-candidate-retained-20261107 \
  --certificate /home/drew/orange-pi-4-pro/out/deployment-delivery-20261106/private/server.crt \
  --key /home/drew/orange-pi-4-pro/out/deployment-delivery-20261106/private/server.key \
  --bind 192.168.1.132 --port 8444
```

Keep that process running for an intentional deployment. If port 8444 is already
bound, inspect `ss -ltnp '( sport = :8444 )'` and its exact command before acting;
do not start a duplicate or stop an unrelated service. Stop this foreground
process with Ctrl-C when delivery is no longer needed. Port 8443 is independent.

The actual acquisition used the same four source files pinned in the refreshed
installer, executing native Python on the build host. It authenticated the
complete compressed payload and recorded `decoded_verified=false`; it did not
read the expanded 256 GB image, select a physical target, run installer
prepare/apply, or touch SD/NVMe/SPI. The RAM download was removed after recording
the receipt. It neither executes the ARM installer nor establishes independently
reproduced physical deployment. Public redistribution grants remain unresolved.

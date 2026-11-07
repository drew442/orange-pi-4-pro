# Pinned Linaro compiler component sources

The [binary toolchain lock](../configs/diagnostic-sources.lock.json) continues to
select the original 109,519,380-byte Linaro archive, SHA-256
`42ccb2ed99d65b7090499e6f5ad65c4c94ae6bd022decfea102d50109557c7d2`.
Its embedded 10,307-byte manifest is authenticated directly from those archive
bytes, SHA-256 `de0cd8327e74bc22ee2dccfab5a52203f4c181866d50f739eab2337abcf4a279`.
The compiler runs on the Ubuntu builder; board images and boot components are
unchanged. The Linux 4.10.3 entry below belongs to the compiler manifest; the
board continues to use its pinned 6.6.98 kernel.

[Component pins](../configs/linaro-source-inputs.lock.json) preserve the manifest's
13 component rows and separate ABE build-script identity. Three rows are marked
MinGW-only by the publisher. The Linux rows have nine distinct source inputs;
ABE makes ten. GDB and GDBserver reference one shared Git commit. Recorded link flags and ABE settings are raw manifest data, with no inference
about license scope.
The [Linaro release description](https://releases.linaro.org/components/toolchain/binaries/7.4-2019.02/)
provides historical release context and package versions; the embedded manifest
supplies the exact input identities used here.

## Retained archives and notices

[Actual acquisition and independent data checks](evidence/toolchain-sources-20261107.json)
retain four archives on `drew@build.drewnet.online` under
`/var/lib/a733-toolchain-sources-20261107`. All four match the MD5 values recorded
inside the SHA-256-pinned binary archive. New SHA-256 pins bind their exact bytes
for subsequent acquisition/cache checks. No source signature was verified.

| Component | Primary source | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| GMP 6.1.2 | [GNU archive](https://ftp.gnu.org/gnu/gmp/gmp-6.1.2.tar.xz) | 1,946,336 | `87b565e89a9a684fe4ebeeddb8399dce2599f9c9049854ca8c0dfbdea0e21912` |
| MPFR 3.1.5 | [GNU archive](https://ftp.gnu.org/gnu/mpfr/mpfr-3.1.5.tar.xz) | 1,126,668 | `015fde82b3979fbe5f83501986d328331ba8ddf008c1ff3da3c238f49ca062bc` |
| MPC 1.0.3 | [GNU archive](https://ftp.gnu.org/gnu/mpc/mpc-1.0.3.tar.gz) | 669,925 | `617decc6ea09889fb08ede330917a00b16809b8db88c29c31bfbb49cbf88ecc3` |
| Linux 4.10.3 | [kernel.org archive](https://cdn.kernel.org/pub/linux/kernel/v4.x/linux-4.10.3.tar.xz) | 94,234,984 | `194165f6bf8ed6ad81eb2231e1cdf497d70a07a489d5f3161544f61a198528d4` |

Eight regular `COPYING*` files were selectively read from these archives,
retained under `notices/` in the same builder directory, and individually pinned.
No source tree was extracted onto the host filesystem or executed. Archive
contents and notices remain outside Git. Small native proof records live under
`out/toolchain-sources-20261107` on codex.

The following exact checksum procedure passed from
`/home/drew/orange-pi-4-pro` on codex:

```sh
scp configs/linaro-infrastructure-sources.sha256 \
  drew@build.drewnet.online:/tmp/a733-linaro-infrastructure-sources.sha256
ssh drew@build.drewnet.online 'sudo bash -s' <<'SH'
set -euo pipefail
cd /var/lib/a733-toolchain-sources-20261107
sha256sum -c /tmp/a733-linaro-infrastructure-sources.sha256
SH
```

For a missing archive, use its exact HTTPS URL, filename and size in the lock,
fetch into a fresh private regular file, and require the locked SHA-256 before
using it. Reject HTML responses, truncated/oversized files and checksum errors;
preserve existing verified files. Do not download a similarly named release
and assume it corresponds to the pinned binary.

The acquisition admitted the unchanged builder 3 GiB free-space floor plus all
97,977,913 archive bytes and completed with 21,811,671,040 bytes free. It installed
no packages and wrote no physical device. Future source acquisition must measure
capacity again; no further LV growth or guard reduction is authorized.

## Remaining source inputs and limits

The following exact Git identities are recorded but source acquisition remains
open. GDBserver shares the GDB row.

| Input | Manifest revision |
| --- | --- |
| GCC | `56ec6f6b99cc167ff0c2f8e1a2eed33b1edc85d4` |
| Binutils | `0231dce141801ffcdfef8b7f14cd903a6d3f21ba` |
| glibc | `6b95c49d8e2b0bea8b2edcf13827e37e477fb19e` |
| GDB/GDBserver | `5ed1741ab59763dfaa4ea5ac9ac5ff928f958ce5` |
| DejaGNU | `21f2ff7c065d7ead6aec3e5ed528ecb0f9eadbac` |
| ABE | `a09711bff8dc7781dd7192b370bdd630b296d45e` |

On November 7, actual builder GET requests for the historical Linaro GCC source
archive and its `.asc` URL redirected to `https://www.linaro.org/contact/` and
returned 60,125-byte HTML pages with HTTP 200. These were recorded and rejected
as source. A successful HTTP status alone does not establish an archive or
signature. Locate an exact retained upstream/mirror source and verify its
relationship to the recorded revision before closing this gap.

This inventory does not establish the compiler's complete build-host dependency
closure, a rebuild of the compiler binary, legal sufficiency or public binary
redistribution clearance. [Vendor boot-input grants](evidence/vendor-boot-license-inventory-20261106.json)
remain independently unresolved. The current board still needs its
[management repair](installed-host-repair-20261107.md), actual KVM execution and
all physical [release acceptance](hardware-validation.md).

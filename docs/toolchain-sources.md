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
capacity again. All free capacity on the Ubuntu builder is now authorized for
use/expansion; revalidate exact targets and retain independent review as recorded
in the [current host assessment](build-host-assessment.md#november-9-capacity-and-standing-authorization).
Resource guards remain unchanged.

## Exact Git source snapshots

[Additional acquisition evidence](evidence/toolchain-git-20261107.json) now retains
Binutils, GDB/GDBserver and glibc from the
[upstream Git service](https://sourceware.org/gdb/current/), and ABE from the
explicit third-party mirror
[Mi-AIoT/linaro-abe](https://github.com/Mi-AIoT/linaro-abe). The ABE commit is the
exact identity in the authenticated binary manifest; no claim of official mirror
ownership or signature verification is made.

Each private bare cache contains a permanent `refs/pinned/manifest`, the exact
requested commit and tree, and all tracked blobs. `git fsck --full --strict`
passes with a declared depth-one boundary. Full parent ancestry is absent.
Pack/index SHA-256 values are recorded separately from immutable Git content
identities. No checkout, source execution or compiler build was performed.
Thirty-five `COPYING*` paths were read and pinned across the four source caches;
some share contents between repositories.

| Input | Builder bare cache | Exact tree |
| --- | --- | --- |
| Binutils | `/var/lib/a733-toolchain-git-20261107/binutils.git` | `193eea30265348d6e413321287ad706953bc11a1` |
| GDB/GDBserver | `/var/lib/a733-toolchain-git-20261107/gdb.git` | `95668921da5b435e6d5bba3707bcafa8a8bc433c` |
| glibc | `/var/lib/a733-toolchain-git-20261107/glibc.git` | `3f389ec45348f0593525c9c019c0b0e9068e5bfb` |
| ABE | `/var/lib/a733-toolchain-abe-20261107/abe.git` | `6c7a470c318260fc4d14cd75616d40ec57b64725` |

The following read-only cache check passed on the builder; repeat with another
retained cache path from the table as needed:

```sh
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
SOURCE_CACHE=/var/lib/a733-toolchain-git-20261107/gdb.git
sudo env GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  git --git-dir="$SOURCE_CACHE" rev-parse 'refs/pinned/manifest^{commit}' 'refs/pinned/manifest^{tree}'
sudo env GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  git --git-dir="$SOURCE_CACHE" fsck --full --strict --no-reflogs
```

The caches use `refs/pinned/manifest`; an unborn default `HEAD` notice is
expected. The fetched source identities below remain those recorded by the publisher.
GDBserver shares the GDB row. GCC and DejaGNU remain unacquired.

| Input | Manifest revision | Acquisition |
| --- | --- | --- |
| GCC | `56ec6f6b99cc167ff0c2f8e1a2eed33b1edc85d4` | Open |
| Binutils | `0231dce141801ffcdfef8b7f14cd903a6d3f21ba` | Retained |
| glibc | `6b95c49d8e2b0bea8b2edcf13827e37e477fb19e` | Retained |
| GDB/GDBserver | `5ed1741ab59763dfaa4ea5ac9ac5ff928f958ce5` | Retained |
| DejaGNU | `21f2ff7c065d7ead6aec3e5ed528ecb0f9eadbac` | Open |
| ABE | `a09711bff8dc7781dd7192b370bdd630b296d45e` | Retained |

On November 7, actual builder GET requests for the historical Linaro GCC source
archive and its `.asc` URL redirected to `https://www.linaro.org/contact/` and
returned 60,125-byte HTML pages with HTTP 200. These were recorded and rejected
as source. A successful HTTP status alone does not establish an archive or
signature. Locate an exact retained upstream/mirror source and verify its
relationship to the recorded revision before closing this gap.

The exact GCC commit was also requested from the
[primary GCC repository](https://gcc.gnu.org/git.html); that fetch failed with
`upload-pack: not our ref`. The single bounded DejaGNU attempt against
`gnu-mirror-unofficial/dejagnu` rejected its exact commit the same way. Legacy
Linaro Git probes timed out or failed TLS handshakes. These failures are retained;
no alternate revision was substituted. An exact retained source copy is still
needed for each open row.

The Git phases used at most two concurrent fetches with one pack/index thread
each, a 3 GiB runtime disk floor and 1 GiB available-memory floor. All jobs are
terminal, with no deadline or resource-floor termination. After the final probe,
21,640,597,504 bytes remain free on the builder. Source object caches and small
proof records remain outside Git.

This inventory does not establish the compiler's complete build-host dependency
closure, a rebuild of the compiler binary, legal sufficiency or public binary
redistribution clearance. [Vendor boot-input grants](evidence/vendor-boot-license-inventory-20261106.json)
remain independently unresolved. The board now passes
[management repair and SSH](installed-host-repair-results-20261108.md) and
[actual KVM CPU/disk/network and clean restart](kvm-vgic-results-20261108.md).
The [November 9 workload result](evidence/eight-hour-endurance-result-20261109.json)
passes the 8-hour criterion using existing data (72,659 measured seconds);
legacy A733 data meeting the threshold does not require a rerun. Remaining physical
[release acceptance](hardware-validation.md) is still open.

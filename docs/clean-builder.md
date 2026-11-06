# Clean Ubuntu builder

The signed Ubuntu Base seed now passes an actual isolation probe on
`drew@build.drewnet.online`. This is the AMD64 build environment. The current
Orange Pi installation continues to use the [personalized deployment](deploy-personalized-20261106.md).
The [fresh offline installation of 842 packages](evidence/builder-offline-install-20261106.json)
now passes. The frozen builder digest and clean artifact rebuilds remain open;
these builder checks supply no A733 guest or deployment acceptance.

## Verified seed and isolation

[The runtime record](evidence/clean-builder-seed-probe-20261106.json) binds the
successful run to project commit `76906800e1ff6553dcc1fd908ae4e59ffb1593fc`,
Ubuntu Base 22.04.5 AMD64, and its authenticated 29,832,285-byte archive,
SHA-256 `242cd8898b33ea806ef5f13b1076ed7c76f9f989d18384452f7166692438ff1a`.
All 3,506 members passed path/ownership auditing, with no special nodes or
writes through symlink parents. The source checkout is private and root-owned;
Git hooks, fsmonitor and external configuration are disabled or refused.

The prepared host's exact 842-package inventory passed before and after.
Inside the namespace, UID/GID `0` map to host `1048576`, for 65,536 IDs.
The seven prohibited capabilities are absent from effective, permitted and
bounding sets, and `NoNewPrivs=1`. Only loopback exists, with no IPv4 routes;
remaining IPv6 routes are loopback-only. No block or KVM devices are visible.
Block-device creation returns `Operation not permitted`; an ordinary private
write succeeds. Firmware is empty under a read-only mount. Both root-directory
ownership IDs actually shift to `1048576`; the ownership change persists.
Normal exit leaves no mapped processes or output mounts. Real signal-injection
cleanup has not been tested.

The firmware mask uses an explicit private empty-directory read-only bind.
With private networking, nspawn mounts a fresh `/sys` before custom mounts;
its bind handler can create the missing target there. The subsequent filtered
sysfs population leaves this bind in place. This ordering is documented in the
[pinned mount implementation](https://github.com/systemd/systemd-stable/blob/v249.11/src/nspawn/nspawn-mount.c)
and [execution sequence](https://github.com/systemd/systemd-stable/blob/v249.11/src/nspawn/nspawn.c).
The Ubuntu source comparison and failed attempts are retained in the evidence.
A complete argument parse using `--help` runs before extraction; systemd 249
requires the explicit boolean `--no-new-privileges=yes`.

## Executed command and retained state

On the builder, this exact command completed successfully from the root-owned
checkout. The seed input was copied and fully hashed/fsynced before use.

```sh
sudo -n env -i PATH=/usr/bin:/bin HOME=/nonexistent LC_ALL=C TZ=UTC \
  /usr/bin/python3 -I -B \
  /var/lib/a733-clean-builder-project-20261106-04/build/clean-builder.py \
  --seed /var/lib/a733-clean-builder-inputs-20261106/base.tar.gz \
  --output /var/lib/a733-clean-builder/seed-probe-20261106-04
```

That output already exists; the recipe refuses overwriting it. A new run needs
an unused `seed-probe-NAME` directly under the fixed private workspace. Its
initial guard requires more than 24 GB free and at least 3 GiB available RAM;
the broader construction planning allowance is 25 GB. Recheck resources for
each later phase and keep the existing kernel/NVMe guards and two-job limit.
The successful sample ended with 24,529,219,584 bytes free.

The output retains the shifted `rootfs/`, source/host bindings, exact command,
probe payload/log and receipt. Verified off-host copies of all nine records
are under `out/clean-builder-seed-20261106/seed-probe-04/` on codex. Three failed
pre-payload attempts remain separate; their receipts record clean host checks.
No downloaded package maintainer script ran in this phase.

## Verified offline package installation

A fresh `seed-probe-20261106-05` passed the same isolation checks, then installed
all 842 pinned archives. Its complete installed tuples match the lock and every
package is fully configured. Private-root `dpkg --audit`, offline `apt-get check`
and checks of host configuration, caches, processes and mounts pass. Sixty-four samples
record at least 18,526,777,344 bytes free and 7,244,427,264 bytes available RAM.

The [runtime evidence](evidence/builder-offline-install-20261106.json) binds the
run to commit `150055f582e428cf522cb9c2767d8fe359044fa6`. This exact command
completed on the builder:

```sh
sudo -n env -i PATH=/usr/bin:/bin HOME=/nonexistent LC_ALL=C TZ=UTC \
  /usr/bin/python3 -I -B \
  /var/lib/a733-builder-corrected-install-project-20261106/build/install-builder-packages.py \
  --seed-output /var/lib/a733-clean-builder/seed-probe-20261106-05 \
  --package-cache /home/drew/orange-pi-4-pro-build/builder-package-acquisition-20261106/out/builder-packages-20261106-02
```

The existing phase is not resumable or overwriteable. The installer verifies
and privately copies every input, checks the boundary inside the child before
maintainers, supplies a private `policy-rc.d` returning 101, and binds an empty
regular APT source list and empty source directory read-only. The successful
run's 19 records have verified copies under
`out/builder-offline-install-20261106/success-05/` on codex.

APT 2.4.13's `--no-download` shuts down local acquisition before absolute archive
paths are assigned. INSTALL therefore permits local-file acquisition from the
explicit verified paths; external repositories remain absent and networking
private. The [pinned APT implementation](https://github.com/Debian/apt/blob/c37792be25062eaa1c1631d10ddecca7c56ac450/apt-private/private-install.cc#L151)
explains the correction. The first failed run remains separate, including its
[retained input cache](build-cache-retention.md#failed-builder-installation-cache).
The corrected installation used a fresh seed.

## Remaining construction

Freeze and hash the installed builder filesystem with its namespace ownership
and metadata preserved, validate restoration, independently acquire the locked
sources/toolchains, and reproduce artifacts in fresh environments. Keep service,
capability/device and network restrictions throughout those operations.
Hardware KVM execution and all [release gates](hardware-validation.md) remain
separate requirements.

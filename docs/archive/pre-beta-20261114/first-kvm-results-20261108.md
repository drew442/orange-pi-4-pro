> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# First physical KVM guest results — 2026-11-08

## Result

A real A733 KVM guest executed on the installed host, proving a **partial CPU and disk path**. This is not full guest or release acceptance. QEMU PID 3648 held `/dev/kvm`, a KVM VM fd and a vCPU fd (`out/first-kvm-installed-20261106/board-evidence-final/kvm-process-identity-1.txt`). The guest kernel logged `PASS cpu sha256=5ab247...29f1c`; its disk check logged `PASS disk previous=0 counter=1`, after mounting ext4 read-only, then read-write, and unmounting before networking (`console-identity-1-complete.txt`, lines 130–138).

The installed GICv3 DT node has no `interrupts` property and host `KVM_CAP_IRQCHIP=0` (`out/host-repair-20261108/kvm-gic-capabilities.json`). The original guest launch failed because userspace GICv3 is unsupported with KVM. A RAM-only diagnostic changed exactly two QEMU arguments from the installed launcher: machine `virt,gic-version=host` to `virt,gic-version=2`, and NIC `romfile` from default firmware to `romfile=`. Its closure verified successfully; it changed no installed tools or production profile (`out/guest-gicv2-no-rom-hotfix-20261108/manifest.json`, `final-closure-verification.json`).

The account-controller RAM correction verified an 11-file closure and changed only `scripts/guest-accounts.py`; the locked `firstkvm` account is UID/GID 60001 without supplementary memberships. The unit adds `kvm` GID 103 to the QEMU process. QEMU ran with 256 MiB, one vCPU and a 512 MiB memory limit (`unit-identity-1.txt`, `kvm-process-identity-1.txt`). The corrections remain diagnostic and are not installed.

## Attempts and stop

The first diagnostic fixture, PID 3339, passed CPU and then failed at `blkid` before disk writes. It was stopped; disk SHA-256 remained `ff595fac...bd577`; its directory was archived as `/run/a733-guests/.firstkvm-failed-fixture-20261108` without erasing it. The corrected initramfs is 975,394 bytes, SHA-256 `10b45e928ab1726e818f78ced73ab6015f0fbabef4f18403d574091fde0adaa2` (`out/guest-initramfs-identity-20261108/manifest.json`). Only that initramfs changed for the successful PID 3648 run.

Networking reached DHCPDISCOVER and then SELECT for `10.0.2.15`; no lease was confirmed. The board loopback HTTP request timed out, as captured in `out/host-repair-20261108/first-kvm-session.typescript`. Separately, the native LAN-address request was refused as expected for the loopback-only listener (`out/host-repair-20261108/guest-forward-lan-1.stderr`). The host requested SIGTERM; QEMU exited cleanly with status 0 and the unit became inactive. This was **not** guest clean shutdown. No checkpoint, restart or second disk counter was proven. Offline counter inspection after stop showed 1, establishing persistence of the single write.

The host remained on boot ID `94851e72-e3b9-4824-88c6-fb82ad7fb610`; installed-tool hashes remained unchanged. No TCG, storage reinstall, SPI write or SD-write acceptance was performed.

## Next physical gate

Follow [next host boot procedure](../../next-host-boot-20261108.md): reviewed SD maintenance candidate with PPI9 enabled, contingent on hardware proof. Then repeat guest networking, guest-initiated clean shutdown, restart, and disk-marker progression. Full release gates remain open; emulation and this partial run do not satisfy them.

Machine-readable artifact index: [first KVM evidence](../../evidence/first-kvm-results-20261108.json).

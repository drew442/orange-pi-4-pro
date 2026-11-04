# Diagnostic component notices

`GPL-2.0.txt` is the unmodified GPL v2 text from the pinned vendor build tree's
`LICENSE`. `busybox-copyright.txt` is the unmodified Ubuntu ARM64
`busybox-static_1.30.1-7ubuntu3.1_arm64.deb` copyright notice. The initramfs
includes both. This directory does not assign a blanket license to every file
in this repository or to opaque vendor firmware.

The build preserves kernel `COPYING`, its syscall exception, and U-Boot
`Licenses/README` alongside artifacts. Exact Git commits, configurations and
build recipes identify the corresponding kernel/U-Boot source. BusyBox's exact
Ubuntu source descriptor, upstream archive and Debian patch archive are downloaded,
hash checked and copied into `artifacts/busybox-source/`. Their hashes come from
the retrieved source descriptor; its uploader signature has not been independently
validated. Archive integrity is checked against the committed lock.

The Linaro archive contains `gcc-linaro-7.4.1-2019.02-linux-manifest.txt` and
per-component license documentation. The compiler is a builder input, not part of
the SD filesystem. Its component source archive acquisition remains a release
provenance task.

No component-specific permission/source-offer record was located for A733
`boot0_sdcard_a733.fex`, `monitor.fex`, `scp.fex` or the proprietary pack tools.
The vendor tree's umbrella GPL notice does not establish those blobs' licensing.
Retain hashes and provenance, and resolve this before public binary release.

# Physical paired host restoration

The original host's ROOT-A, ROOT-B and STATE were restored on the recorded
Orange Pi 4 Pro A733. An interrupted p2 restore was followed by a complete
retry, flush and readback of all 16 GiB. The restored host booted, retained its
management identity/configuration, and ran a nonroot KVM guest.
[Machine-readable evidence](evidence/paired-host-restore-20261110.json).
This passes the paired host-restoration case. Fresh normalized installation,
inactive-slot update/rollback, missing-NVMe and dirty-journal cases remain open.

## Recorded sequence

1. RAM boot `732ffcf6-d815-4ec0-80d5-02e9ecb69c96` admitted the known SPCC
   NVMe serial `134807190FC700149277`, 256060514304 bytes. The
   [immutable original backup](evidence/offline-host-preservation-20261110.json)
   and guest backups had passed independent review. Its trusted 1361-byte index
   SHA-256 is `44e8fc1f551f7e3a0be3ad537e48631d1fbe4d090f689e18fa28f811ee64372e`.
2. The unchanged reviewed restore helper authenticated all compressed inputs,
   exact GPT/geometry and compatible BOOT. The concrete plan and interruption
   wrapper passed independent review. The wrapper sent SIGTERM only to its own
   restore child after p2 progress. The child exited 1 with the exact interruption
   error, reporting **140213361** accepted p2 bytes. p3/p4 had no attempted writes;
   p2 had neither a completed stream nor a flush/readback. The board stayed in RAM.
3. A fresh preparation produced the same reviewed plan. Full retry exited zero;
   its terminal receipt authenticated the conservative stored report. Every
   4/4/8-GiB stream, flush and optional readback completed and matched its backup.
   Final census found no remaining claims, mounts, holders, raw consumers,
   mappings or I/O. All six NVMe block nodes were protected read-only again.
4. The reviewed SD writer restored only the protected 256-MiB prefix to the
   exact SU02G card, CID `035344535530324780716f216e00a8f5`, 1977614336 bytes.
   Source raw hash was `ab5e8c…ad207`; write and flush completed. No SD readback
   was requested. This was an explicit maintenance write. GPT, NVMe BOOT, p5 and
   SPI were outside the paired restore write set.
5. UART and pinned-key SSH establish new host boot
   `0ff0e3d0-eddd-4d27-9777-af8ccfe6f88c` at `192.168.1.116`. Its old root A
   marker `e1cc…` and selector `bd27…` match the preserved installation. The
   management runtime guard, all 40 installed tool hashes, NTP, both authorized
   keys and any-subnet policy pass. STATE was probed block-protected, unmounted,
   then reopened writable; p2 mounted read-only. The reboot SSH observation
   timed out after 30 seconds; the command was not retried. New UART/SSH boot
   evidence establishes the transition independently of that timeout.
6. After explicit p5/account activation, QEMU PID 1860 ran as UID/GID 60001,
   with no capabilities and `NoNewPrivileges=1`. Its actual KVM VM, vCPU and
   VGIC descriptors were captured alongside `-accel kvm -cpu host`. The guest
   passed CPU, disk **5 → 6** and loopback HTTP checks. Its forwarded port was
   confined to 127.0.0.1; a wired-address connection was refused. This probe is
   not a new physical-network throughput test.
7. HTTP confirmation caused guest-requested poweroff. Guest console and PID1's
   successful deactivation bind to invocation `2c8ef549a6dd4a22aa7352a9e0fb7ad2`.
   Detailed service exit fields had already been garbage-collected: the first
   strict exit snapshot refused, and no raw QEMU exit code is claimed. The full
   journal is retained. The stopped disk was then durably checkpointed at counter
   6, enrollment SHA-256
   `5486d205d643b149884a8bfa3083338f536c3b7aa00b565a0b7748474d9dff46`.
   All observed SD write/discard/flush fields stayed zero during this host boot
   and guest execution.

## Limits and next use

This was a handled process interruption during real NVMe writes, not a power cut.
The existing p5 guest was reused; the newly preserved counter-5 guest archive has
not yet been restored. The original paired archives remain immutable on the
builder. Neither final boot counts, SPI recovery/SD-removed boot nor normalized
image installation is established here. No new endurance run was started.

Use [the paired restore procedure](known-host-state-restore.md) for recovery and
[current status](implementation-status.md) before any live command. RAM address
`.115` and its previous staging files are no longer current. The installed
host at `.116` is ready for the changed inactive-slot update/rollback cases.

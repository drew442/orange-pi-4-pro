# Physical changed-root update and manual rollback

The recorded A733 passed a real interrupted inactive-B update, recovery into
retained A, a full B retry, selector interruptions before and after publication,
pending-selection cancellation, booted B and manual rollback to old A. Both B
and rolled-back A ran actual nonroot KVM guests with CPU, disk and HTTP checks,
guest-requested poweroff and independently retained zero exit status.
[Machine-readable evidence](evidence/physical-host-update-rollback-20261110.json).
A [verified private evidence copy](evidence/physical-update-rollback-custody-20261110.json)
is durable on the builder.

## Inputs and write boundaries

The target was SPCC M.2 PCIe SSD serial `134807190FC700149277`,
256060514304 bytes. Only inactive p3 was rewritten during staging; selection
changed regular files on STATE p4. Protected SD `ab5e8c…ad207`, NVMe BOOT
`cc2ad9…26c313`, GPT and SPI were not update targets. Guest checks intentionally
advanced the existing disposable p5 guest disk and its enrollment checkpoints.
The original paired roots/STATE and guest backups remain immutable off-device.

| Input | Trusted binding |
| --- | --- |
| Old A retained for rollback | Root `d868…7940`, marker `e1cc…fa61`, selector `bd27…20a`, manifest `4f28…97b9` |
| Old B before update | Previous full readback `3369…bdab`; actual bounded marker `48ca…c3e1` |
| Normalized B candidate | Root `42fb…4ab2`, marker `521e…f8f8`, selector `27ea…2971`, manifest `c1bc…83f2` |

Exact complete pins are in the [normalized maintenance inputs](normalized-maintenance-20261109.md)
and [retained old pairing](matched-host-updates-20261109.md). The normalized A
selector was never applied to the still-old A root. B was transferred through
pinned SSH into a new private sparse regular file on STATE, authenticating all
4 GiB without placing raw images on codex or SD. It allocated 454037504 bytes;
STATE retained over 7.69 GB available after staging. Source files were root-owned
0400 and their directory 0700. The production controllers were not modified.

## Physical results

| Case | Observed result |
| --- | --- |
| Interrupted changed inactive B write | Reviewed pidfd watcher sent one SIGTERM to exact updater PID 2524 after 1 GiB console progress. Exit 1 reported 1103101952 accepted bytes, no verification, activation readiness or selector writes. Actual first MiB changed, A marker/selector and GPT remained, and p3 could be claimed exclusively again. No exact durable-prefix claim is made. |
| Recovery and retry | Clean boot `53ddb8c0-5bb8-4232-9b03-bd204b7b11e1` retained old A and management. Persistent source survived. Fresh plan differed only in changed inactive metadata and its resulting plan hash. Complete 4 GiB write, flush and readback matched `42fb…`; exit zero. |
| Before selector publication | SIGTERM to PID 2002 after the new journal appeared. Exit 1 truthfully reported durable journal, no selection-write attempt and no publication. Actual selector remained old A. |
| After selector publication | Retrying the same authenticated journal, SIGTERM to PID 2134 after B became visible. Exit 1 truthfully reported B published and durable. Actual selector was B; the running root remained A. |
| Cancel pending B | Production `cancel-pending` authenticated old A against its old manifest, durably selected A and exited zero. No root blocks were written or running root switched. |
| Normal B activation and boot | A fresh plan matched the reviewed B transaction. Explicit publication exited zero. New boot `2ec8feae-b716-4490-a648-d5b5e9e5e7ce` mounted p3 read-only with marker `521e…`. All 40 normalized tool files and original management identity/configuration authenticated; NTP passed. |
| B policy and KVM | Resolver directory was 0755; global LLMNR/mDNS were disabled and no 5355/5353 listeners existed. Actual QEMU PID 1887 had KVM VM/vCPU/VGIC descriptors, UID/GID 60001, empty capabilities and NoNewPrivileges. Guest CPU/disk/HTTP passed, counter 6 → 7. A prestarted D-Bus observer retained exit code 0 and clean guest poweroff. Checkpoint 7 was durable. |
| Manual rollback and boot | The reviewed old-manifest activation authenticated p2 against `d868…`, published old A and exited zero. New boot `987d0392-ec8d-491c-a711-33abe07d614c` mounted p2 read-only with the original marker, selector, 40 tool files and management identity/configuration. |
| Rolled-back A KVM | Actual PID 1866 passed CPU/disk/HTTP with KVM descriptors, nonroot identity and restrictions, counter 7 → 8, guest poweroff and observer-retained exit zero. Checkpoint 8 is durable; the guest is stopped and its inactive RAM unit now matches that checkpoint. |

All observed Linux SD write/discard/flush fields remained zero across these
host boots and guest checks. This does not replace the final whole-card and
firmware-write reconciliation. Neither these test reboots nor earlier accidental
restarts count toward the final ten warm/ten cold release boots.

The post-B SMART snapshot had critical warnings 0, media errors 0 and unsafe
shutdowns unchanged at 77. Error entries rose 153 → 157 while controller power
cycles rose 97 → 101; the latest admin invalid-field status 8194, parameter
location 40 and namespace/LBA 0 match the earlier baseline. This is consistent
with initialization probing; the exact originating command is unproved. It
is not labeled an empty error log. This snapshot preceded the last A reboot.

## Current state and limits

The board is at `root@192.168.1.116`, boot `987d0392…614c`, running old A; inactive
B is the normalized root. Guest counter 8 enrollment SHA-256 is
`aab3db8c869d6411b1cc44eefa00a8093df821e5ebb32986f42f7c36e2766f7b`.
The live old A retains its known resolver-directory finding; only normalized B
has passed the correction's physical check so far.

These were handled process interruptions, not power cuts. The rollback was
from a healthy running B. Failed-health/offline root-selection recovery,
missing-NVMe recovery, an actual dirty-journal recovery trial, fresh normalized
full installation, new guest-archive restoration, final boot counts and the
selected SPI/immutable-SD route remain separate gates. No endurance rerun was
started. Use [current status](implementation-status.md) before live maintenance.

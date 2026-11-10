# Controlled SSH failure and offline root selection

The controlled SSH-service failure, UART transition into RAM, offline root-B
selection, subsequent protected boot and post-recovery actual KVM guest pass.
[Evidence](evidence/controlled-ssh-recovery-20261110.json) pins 96 bounded records;
[verified private builder custody](evidence/controlled-ssh-recovery-custody-20261110.json)
preserves their second copy. This closes this controlled service-failure case;
broader physical recovery and release gates remain open.

On installed old-A boot `987d0392-ec8d-491c-a711-33abe07d614c`, a separate
systemd oneshot stopped only `a733-management-ssh.service`. Its nonce-bound UART
receipt showed successful stop and MainPID 0; a fresh TCP22 connection was
refused. This deliberately induced service failure does not explain the earlier
uncontrolled loss of SSH and does not prove recovery from a fully hung kernel.

Independent capture remained running on the serial bridge. The first emergency
sync completion was unobserved. After raising console verbosity and requesting
blocked-task diagnostics, a new sync completed at kernel time 2355.196825.
Emergency remount completed at 2372.135486. The independent observer reported all
15 mounted NVMe filesystem entries read-only and all six devices' in-flight I/O
zero, with the expected boot UUID and nonce. Kernel records interrupted its JSON;
independent review reconstructed the receipt by removing only the timestamped
kernel records. The original ext4 `systemd-journal` warning remains an anomaly;
no clean-health claim or exact prior console-loglevel claim is made.

One UART reboot entered private RAM boot
`14b1fb2d-165f-47aa-9797-80d32c46cdb9` at `.115`, with pinned SSH identity,
rootfs and tmpfs, no swap and no mounted NVMe filesystem. The authenticated
40-file portable tool closure ran the unmodified offline controller against
SPCC NVMe serial `134807190FC700149277`, 256060514304 bytes, and STATE UUID
`360dfb39-f49f-487e-b307-0bb887c854c5`.

Independently reviewed plan
`61de3e4af98d4632c6d6683f86cf82cb6dfc5a9738e19773f37e90c68c5449c2`
authenticated normalized B, published selector `27ea5d…2971`, durably stored its
journal and unmounted its owned STATE mount. The terminal exit was zero.
Independent postchecks found no NVMe filesystem mounts, open device handles,
holders or in-flight I/O. Backup confirmation covered the preserved original
compatible p2/p3/p4 cohort, canonical normalized B and original guest archives;
later disposable STATE additions have no claimed byte-exact backup. Restore the
whole original cohort if needed, never its STATE alone over normalized roots.

The reviewed SD writer restored and flushed the 256 MiB protected prefix
`ab5e8c…ad207` on the exact SU02G card, CID
`035344535530324780716f216e00a8f5`, without optional readback. One RAM reboot
produced installed B boot `c2dc588f-97d9-4bee-8fce-f1ede4550f4d`. Root, selector,
all 40 tool files, management identity/configuration, NTP, resolver policy,
EL2/VHE and observed zero SD-write counters pass. The old RAM SSH transport was
terminated only after this new boot was authenticated; no reboot was retried.

Raw receipts are in ignored `out/failed-health-recovery-20261110-01/`.
The staging wrapper reused the original RAM-SD preparation stdout filename;
that original stdout is no longer retained. Its prior independent review and
actual write/exit receipts remain intact. The replacement protected-SD plan has
a distinct filename; no original stdout was reconstructed. SPI,
missing-NVMe, dirty-journal, corrupt-root and power-cut recovery are outside this
trial's proof.

After boot, the existing guest ran as UID/GID 60001 with empty capabilities and
NoNewPrivileges. PID 1956 held actual KVM VM, vCPU and VGIC descriptors and used
`-accel kvm -cpu host`. CPU/disk/network checks passed with disk counter 8 → 9.
The guest requested poweroff; the prestarted independent observer retained
ExecMainCode 1, ExecMainStatus 0 and matching timestamps/PID. Counter 9 is durably
checkpointed in enrollment `217cc43d…1727a7` (enrollment checkpoint number **6**),
and its inactive RAM service was refreshed with the new disk pin. SD write,
discard and flush counters remained zero.

The initial readiness adapter expected a MainPID field in the observer's ready
event and refused before launching the guest. The correction checked its actual
ActiveState schema and the same live observer PID 1920 separately. The observer
was not restarted. An initial read-only journal collector likewise refused an
incorrect mode-0400 assumption; source and actual stat establish mode 0600.
Both errors remain retained. Builder custody initially refused a Python-version
API mismatch after writing/flushing its 471040-byte archive; a compatible
chunked reader verified that same archive and all 96 records without rewriting
or retransmitting it. The custody receipt records this limitation.

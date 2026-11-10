# Controlled SSH failure and offline root selection

The controlled SSH-service failure, UART transition into RAM, offline root-B
selection and subsequent protected boot pass. The post-recovery guest check is
pending; this document does not close the complete recovery or release gate.

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
a distinct filename; no original stdout was reconstructed. Complete the
post-recovery KVM check and evidence custody before closing this case. SPI,
missing-NVMe, dirty-journal, corrupt-root and power-cut recovery are outside this
trial's proof.

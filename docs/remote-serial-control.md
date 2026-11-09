# Independent serial capture and recovery

The operator connected A733 debug UART to the Orange Pi One Plus at
`orangepi@192.168.1.117`, with passwordless sudo for this task. The existing
`ttyS3-capture.service` configures `/dev/ttyS3` for **115200, 8N1, raw, no echo,
no flow control**, and appends output to `/var/log/ttyS3.log`.
[Physical evidence](evidence/serial-control-20261109.json) records the observed
service, new A733 boots and actual UART control response.

The One Plus uses `/dev/zram0` for `/var/log`: its log can be lost or revert on
reboot. The independent codex service `a733-uart-relay-20261109-01.service` now
mirrors that file over strictly verified SSH to private ignored storage at
`out/serial-recovery-20261109/serial-live.log`. It is bounded to 64 MiB and does
not restart automatically; inspect its state and retain the source file if the
relay fails. A stopped relay or restarted capture board leaves a coverage gap,
not a successful continuous capture. Preserve complete logs before any rotation.

## UART recovery capabilities

A733 currently has `CONFIG_MAGIC_SYSRQ=y`, `CONFIG_MAGIC_SYSRQ_SERIAL=y` and
runtime `kernel.sysrq=1`. Sending UART BREAK then **only `h`** produced the actual
kernel help response. This proves the bidirectional path; it neither rebooted
the board nor authenticated a serial shell. Normal serial root password login
remains locked.

[Linux SysRq documentation](https://docs.kernel.org/admin-guide/sysrq.html)
describes BREAK followed within five seconds by a command. Diagnostic `w`, `m`
and `t` can provide blocked-task, memory and task-state evidence when SSH fails.
Only help has been physically tested here. Keep the receiver running while
writing through a separate UART descriptor; the reader need not be stopped.

Emergency recovery requires separate controlled actions: request sync and wait
for its completion, request read-only remount and wait for completion, then
request reboot. The reboot command alone skips those steps. UART reboot and
recovery during a hung kernel remain **unperformed**; a fully locked kernel may
not process SysRq. Routine boot tests should use clean systemd reboot instead.

## Reset wiring

The reviewed [Orange Pi 4 Pro schematic](https://fox-exe.ru/Files/OrangePi4Pro/OPI%204%20PRO%20V1_3_2_20260109.pdf),
PDF page 16/sheet 17, connects the physical RESET switch to **AP-RESET**.
PDF page 17/sheet 18 lists the 40-pin connector and does not expose AP-RESET.
The physical board revision has not been established; source title/filename
revisions differ. No header pin is recommended for reset wiring.

The existing UART supplies useful kernel-assisted control now, so no additional
GPIO connection was requested or made. A true out-of-band hardware reset would
need a separately validated interface to the physical reset switch, independent
of kernel health. Do not infer that an ordinary header GPIO is a reset input.
Leave the heatsinks attached.

# Complete an automatic qualification fixture

This external test helper is pending deployment with the next repaired root.
Its software review and focused tests pass; root/module/helper pins now match independently verified F349 artifacts.
Combined collector validation and actual hardware checks remain required. Do not invoke
it against the installed `4334a22` root, whose native stop lacks the bound API.

Normal managed guests run until explicitly stopped. `complete-managed-fixture.py`
finishes only the disposable automatic `autokvm` qualification run. It does not
set a production guest lifetime or replace completed-cohort qualification.

Keep the power packet in `/run/a733-spi-collector` limited to its qualifier and
power helper. Stage completion code and its receipts separately under
`/run/a733-spi-fixture-completion`, beside its exact pinned `qualify-managed-boot.py` in a
protected RAM directory. Its default invocation prints a plan. After authenticating
the installed cohort and observing the intended current boot, execution uses:

```sh
python3 -I -B /run/a733-spi-fixture-completion/complete-managed-fixture.py \
  --complete --expected-boot-id CURRENT-BOOT-UUID --expected-counter EXPECTED-COUNTER
```

An optional `--expected-run-id` binds an already observed run. Derive the expected
counter from the trusted preceding completed run or initial seed; never guess it
from a console fragment. Preserve stdout, stderr, return status and both helpers'
intents/receipts off-device.

The helper authenticates installed source bytes, then observes protected current
run/head metadata, the nonroot QEMU process, cgroup and actual KVM descriptors.
A single HTTP probe verifies the expected counter without sending confirmation.
It waits 135 seconds after that successful execution witness, rechecking identity.
Live observations grant no disk/run trust or checkpoint authority; the running
supervisor owns those locks and performs HTTP confirmation itself.

Before stopping, it durably publishes an exclusive boot/run intent. The native
stop receives all four observed identity fields:

```sh
python3 -I -B /usr/lib/a733-tools/scripts/manage-guests.py stop autokvm \
  --expected-boot-id CURRENT-BOOT-UUID --expected-run-id CURRENT-RUN-UUID \
  --expected-main-pid SUPERVISOR-PID --expected-main-start-ticks START-TICKS
```

The native command authenticates the loaded supervisor and signals its exact
pidfd once, using Python 3.11's [process descriptor](https://docs.python.org/3.11/library/os.html#os.pidfd_open)
and [descriptor-bound signal](https://docs.python.org/3.11/library/signal.html#signal.pidfd_send_signal) APIs. A replacement run cannot receive that signal. SIGTERM reaches the
normal guest power-key shutdown and checkpoint path. It does not arm systemd's
stop timeout or escalation; native observation remains bounded at 420 seconds,
with no extra signal or name-based fallback. The helper allows 450 seconds for
the command and 900 seconds overall, below the eight-hour test cap.

Native observation retains immutable admission bytes while replaying the legal
old/new manifest transitions. It waits through ready-head/registry/completion
publication windows and requires the same run's coherent ready state and
successful terminal service. This is still not completed-cohort acceptance.

Afterward, run the separate managed qualifier to authenticate the completed
automatic cohort. Boot/UART evidence and coordinator acceptance remain necessary.
An existing intent, identity change, timeout, lost acknowledgement or malformed
receipt refuses repetition. Inspect actual state with read-only operations;
never delete an intent, resend a stop or claim success from timing alone.

# First KVM guest exit observer

[`scripts/observe-first-kvm-exit.py`](../scripts/observe-first-kvm-exit.py) preserves
systemd's raw execution fields for the fixed `a733-guest-firstkvm.service` unit.
An inactive static service can otherwise be garbage collected before a later
query, losing the process exit evidence. This diagnostic helper uses Python 3's
standard library and `libsystemd.so.0`; it accepts no arguments and never starts,
stops or changes the guest unit. It does not change installed packages or guest
profiles. It writes only flushed stdout records and stderr diagnostics.

The observer calls `RefUnit` on one private system-bus connection, then `GetUnit`,
and retains that reference through the complete run and final property capture.
`RefUnit` loads unit metadata without activating the service. Initial state must
be inactive; a single `ready` JSON line means the reference is held. An observed
active state followed by inactive is mandatory before final capture. Initial
inactivity alone cannot complete a run.

## Run from RAM

Transfer the reviewed source to a temporary RAM location on the board, then
stage a root-owned copy. Confirm all these paths resolve to RAM:

```sh
sudo install -d -o root -g root -m 0700 /run/control
sudo install -o root -g root -m 0400 /run/observe-first-kvm-exit.py /run/control/observe-first-kvm-exit.py
sudo python3 -B /run/control/observe-first-kvm-exit.py
```

Keep the observer and its transport alive. Capture stdout/stderr off-device or
under `/run/control`; preserve zero routine SD writes. After `ready`, launch the
separately reviewed guest workflow in another session. The observer performs no
launch, shutdown or storage actions. It is a manually staged diagnostic helper,
not an installed host service.

The observer polls every 0.2 seconds with a 300-second monotonic observation
budget. Each synchronous method call uses the remaining budget; reference cleanup
has an additional two-second method-call budget. Failed state, missing properties,
timeout or inconsistent final fields produces a nonzero exit and no `final`
JSON. A run shorter than the polling interval may be missed and time out.
Concurrent external restarts are outside this single-run observer's assumptions.

A `final` record contains raw state/result, current and historical PIDs, execution
code/status and monotonic start/exit timestamps. Validation requires
loaded/inactive/dead, `Result=success`, current `MainPID=ControlPID=0`, a positive
`ExecMainPID`, **`ExecMainCode=1` and `ExecMainStatus=0`**, and positive ordered
execution timestamps. Capture occurs before `UnrefUnit`; final output follows
successful cleanup. `ready` is never acceptance, and service exit evidence must
be correlated with the real guest's disk, network and guest-initiated shutdown
records.

## ABI and checks

The implementation follows official **systemd v252** sources, matching the
board's systemd major version:

- [sd-bus header](https://github.com/systemd/systemd/blob/v252/src/systemd/sd-bus.h)
  defines the error structure, function signatures and void `sd_bus_error_free`.
- [D-Bus interface](https://github.com/systemd/systemd/blob/v252/man/org.freedesktop.systemd1.xml)
  defines GetUnit `s → o`, RefUnit/UnrefUnit `s → ()`, string states/result,
  PID `u`, execution code/status `i`, and monotonic microsecond timestamp `t`.
- [Manager implementation](https://github.com/systemd/systemd/blob/v252/src/core/dbus-manager.c#L767)
  confirms RefUnit loads and references metadata; UnrefUnit needs no load.
- [Method-call documentation](https://www.freedesktop.org/software/systemd/man/latest/sd_bus_call_method.html)
  and [property-getter documentation](https://www.freedesktop.org/software/systemd/man/latest/sd_bus_get_property.html)
  describe varargs and caller ownership of replies and allocated strings.

The ctypes declarations specify each function's fixed arguments, with explicit
string varargs. The GetUnit path is copied before reply release, allocated
property strings are copied before libc `free`, and replies/errors are released
in `finally`. Enter failure and UnrefUnit failure both close the connection.

Run the twelve focused pure tests without a system bus or hardware:

```sh
python3 -m unittest discover -s tests -p 'test_first_kvm_exit_observer*.py' -v
```

The tests cover inactive waiting, active-to-inactive capture, failure/timeout,
invalid raw exit fields, final-output ordering, exact void error-free signature,
scalar types, copied path lifetime, string freeing and cleanup failure paths.
The local x86 root ABI smoke also passed against an existing inactive service
without activation. These checks establish neither physical boot nor guest
acceptance.

## Physical evidence, 2026-11-08

The coordinator reports that the actual ARM64 ABI smoke and physical counter-3
capture passed. The retained raw record is
`out/vgic-live-boot-20261108/counter3-exit-observer.jsonl` (ignored evidence,
not a repository artifact). It records loaded/inactive/dead, `Result=success`,
`MainPID=ControlPID=0`, `ExecMainPID=2665`, `ExecMainCode=1`,
`ExecMainStatus=0`, start `1582336465` and exit `1624373575` monotonic microseconds.
The earlier counter-2 run had a garbage-collection gap in its final systemd
execution fields; do not infer those missing raw fields from counter-3.

See the [physical VGIC boot record](vgic-live-boot-20261108.md) and
[KVM VGIC guest results](kvm-vgic-results-20261108.md) for the surrounding hardware
and guest evidence. The production script preserves the reviewed and physically
used source bytes, SHA-256
`334f79c44ca9033556f9a7752d837ada7f37c4c34a7c7a51798807e195faefb4`.

# Endurance guest exit observer

`scripts/observe-endurance-kvm-exit.py` observes only
`a733-guest-endurance.service`. It never launches or stops a service, changes
storage or networking, or changes SSH. It leaves the installed first-guest
observer and its closure unchanged.

The wrapper authenticates the sibling `observe-first-kvm-exit.py` against
SHA-256 `334f79c44ca9033556f9a7752d837ada7f37c4c34a7c7a51798807e195faefb4`
before executing those exact bytes. It rejects symlinks, special files, writable
source files and ancestors owned by anyone other than root or writable by group
or others. It executes from memory without importing or generating bytecode
cache files. Its only changes to the authenticated observer module are the fixed
unit and timeout. The original sd-bus signatures, reference handling, observation
logic and entry point remain unchanged.

Stage both scripts as root-owned mode `0400` files in a protected RAM directory
such as `/run/a733-endurance-observer` (root-owned mode `0700`). All ancestors
must be root-owned and protected; a user checkout and `/tmp` fail this policy.
Use the host's trusted Python interpreter with isolated mode, which excludes the
script directory and user Python configuration from dependency imports:

```sh
python3 -I /run/a733-endurance-observer/observe-endurance-kvm-exit.py --mode smoke
```

`--mode` is required. `smoke` uses a 300-second budget; `release` uses 29,100
seconds (8 hours plus five minutes). The extra five minutes only bounds launch
and exit observation; it does not extend the eight-hour workload requirement.
There is no arbitrary unit, timeout or
source override. The host requires Python 3, `libsystemd.so.0`, a working system
bus and permission for the fixed observer calls. Missing dependencies cause
failure; no package installation or bus reconfiguration is attempted.

Already-collected legacy workload evidence of at least eight hours qualifies
for duration review without a new run. Preserve any active legacy observer and
its original budget; starting this new wrapper cannot recover an earlier unit
reference or substitute for the retained exit evidence.

Run the observer before separately launching the guest. Wait for its flushed
JSON `ready` record on stdout: this establishes that `RefUnit` succeeded and the
unit was initially inactive. Preserve that observer process and its bus connection
through the entire guest run. The reference prevents garbage collection of the
unit's exit fields; it is released during cleanup after completion or failure.
No `ready` means the guest must not be launched for this observation. Observer
failure requires a fresh observation rather than inferring the missed exit.

A successful exit produces a flushed JSON `final` record only after cleanup
succeeds, with the original exact clean-exit checks (including
`ExecMainCode=1`, `ExecMainStatus=0`). Diagnostics go to stderr and failures exit
nonzero. The unchanged base observer's timeout error text still says
“300-second” even in release mode; the selected numeric budget controls the
actual monotonic deadline. Keep stdout and stderr as separate evidence streams.

Host tests and this wrapper do **not** establish hardware acceptance. A clean
exit alone does not prove an 8-hour run: independently retain console/KVM
workload evidence and measured guest duration, CPU/disk/network results,
temperatures, memory use and NVMe errors. The coordinator must verify the actual
8-hour hardware run and remaining [hardware gates](hardware-validation.md),
including zero routine SD writes. A733, 2 GB SD, nominal 250 GB NVMe and the
minimal host remain the deployment constraints.

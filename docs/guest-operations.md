# Guest runtime reference

The current supported procedure is the [manual guest guide](manual-guest-guide.md).
It covers boot preparation, accounts/enrollment, systemd unit rendering, start,
guest-owned shutdown, checkpoint and restart. A running service survives SSH
disconnection; automatic boot dispatch is deferred.

The original detailed storage/descriptor discussion and fixture-specific sequence
are retained as a [historical technical reference](archive/pre-beta-20261114/guest-operations.md).
Their old release counts and pending-work sections are not current gates.

The launcher retains verified file descriptors through exec, requires real KVM,
uses an unprivileged identity, coordinates disk locks and enforces selected NVMe
placement. Manual checkpoint, backup and restore retain shared admission and
consumer checks. Do not bypass these with a raw launch against an enrolled disk.

# Shared guest admission and maintenance

Manual-beta retains the shared storage, ownership, account, namespace, lock and
consumer checks. These protect manual launch/checkpoint/backup/restore and host
updates as well as experimental managed operations. Retain the complete source
closure; do not delete managed modules to disable automatic startup.

`a733-guest-storage.service` runs authenticated preparation and its existing
teardown. The historical `a733-guest-prepare.service` is dispatch-only and is
masked in manual-beta. Preparation retains account reconstruction and manual
attestation, but skips managed reconciliation and managed unit publication.
The immutable payload selects the profile; mutable network configuration or an
environment variable cannot opt in. `managed-experimental` is a development
build option, outside beta support.

Manual operations still require the protected prepared admission runtime. Initial
policy creation is covered by the [manual guide](manual-guest-guide.md). A closed
shutdown runtime cannot be reset, deleted or reopened to continue work; use a
supported fresh boot. Host maintenance waits for required guest/data consumers
and refuses uncertain state. Standard host signals alone do not attest clean
shutdown inside the guest.

Status/disable/stop and supported experimental restore/custody remain available
for recovery. Managed enroll/enable/start, dispatch and run admission reject in
manual-beta. The [paused restoration receipt](evidence/goal-pause-and-restoration-20261113.json)
remains authoritative for the last observed unit state; it has not been rerun.

[Original protocol/design](archive/pre-beta-20261114/guest-maintenance-admission.md)
is reference material. Its old installation and managed-release TODOs are not
current gates. See [beta scope](beta-scope.md).

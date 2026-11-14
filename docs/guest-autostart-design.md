# Deferred automatic guest management

The full automatic lifecycle is parked outside the manual beta. It includes a
persistent registry, boot dispatch, QMP supervision, automatic stopped-disk
checkpointing, reconciliation, quarantine and coordinated managed recovery.
A manually started service continuing after SSH disconnects is supported and
does not require this subsystem.

[Preserved design](archive/pre-beta-20261114/guest-autostart-design.md) describes
the experimental implementation. Its TODOs and acceptance series are not current
release requirements. Shared modules remain packaged for the manual tools and
recovery. [Current boundary](guest-maintenance-admission.md), [beta scope](beta-scope.md).

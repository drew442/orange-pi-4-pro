# Native archive retention record — 2026-11-08

Four duplicate gzip archives were retired from the developer cache. The completed operation released **552,181,760 allocated bytes**; native free space afterward was **872,210,432 bytes**. Two canonical archives remain, and native plus builder full-pin checks passed.

The first plan attempt failed because a transient process was unavailable. Its private logs were preserved and it performed no unlinks. A second plan and apply completed successfully. The apply record is [apply.json](../out/native-archive-retention-20261108/apply.json); stderr is retained beside it. No physical device was written.

The candidate retirement code SHA-256 is `2c4e166579bd040c320d125214d6829030456affca83a2b426d3a78d0e37bb14`. Current native fixtures passed 25/25, Ubuntu Jammy fixtures passed 25/25, and the independent nsfs compatibility review passed. The review and logs are linked in the [machine-readable evidence](evidence/native-archive-retention-20261108.json).

The restore controller source and fixtures passed, but no actual restore was performed. This record covers developer-cache cleanup only; it makes no claim about physical boot, NVMe installation, KVM acceptance, or hardware recovery. The private control and event records remain under `/var/lib/a733-native-archive-retirement-20261108-01`; this document intentionally omits private request data, tokens, and full plan contents.

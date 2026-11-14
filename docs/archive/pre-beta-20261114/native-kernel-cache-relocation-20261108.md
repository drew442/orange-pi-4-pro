> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Historical kernel cache relocation

A historical generated kernel-object archive moved from codex to root-private
builder storage after both complete encoded hashes matched. No physical board
storage was changed. The old native path is now absent; other historical cache
archives and active build inputs remain unchanged.

- Former native path: `out/builder-retained-20261106/kernel-object-cache/kernel-object-cache.tar.gz`
- Retained builder path: `/var/lib/a733-native-kernel-cache-retained-20261108-01/kernel-object-cache.tar.gz`
- Bytes: **893430589**; SHA-256: `11a3282b5049e1587754109a0075a1af899245f0370c606af50105b538a4e5e3`.
- Builder parent is root0700; archive root0400. File and parent were synchronized.
- Native retirement recovered **893435904 allocated bytes**, leaving **1165426688 bytes free** at completion. Its unchanged floor is 268435456 bytes.

[Execution evidence](../../evidence/native-kernel-cache-relocation-20261108.json) records
source identity, held lock, repeated source hash and verified retained destination.
This is encoded-byte preservation; archive extraction/restoration was not performed.
The next NVMe job had already passed its unchanged admission guard before this
transfer. It continues sampling the unchanged runtime floors; remeasure before
any subsequent construction. No LV growth or guard reduction occurred.

To restore the former native path when needed, first provide at least the archive
size plus the native free-space floor. Copy from the recorded builder path over
SSH into a new private temporary file, verify the exact size/hash, set mode0400,
then publish at the absent original path. Do not overwrite a later existing cache.
The archive is a historical build cache, not a deployment or recovery image.

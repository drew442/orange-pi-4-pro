# Historical root image retention

The [retention record](evidence/historical-root-retention-20261106.json) maps
twelve historical generated builder files to two protected copies on codex.
Six 4 GiB A/B root images and their gzip files belong to the November 4 current
and guest-tools payloads and November 5 operator payload. Their original
manifests remain unchanged; the twelve original file paths are now absent.
The November 6 personalized deployment artifacts were excluded.

Copies reside under `/var/lib/a733-historical-root-retention-20261106/` on
codex, in `primary/` and `secondary/`, using each record's `retained_name`.
Both tiers are root-owned mode 0700; each file is root-owned mode 0600 with a
distinct inode. All 24 complete logical hashes passed preparation, independent
verification and the post-retirement check. Raw files remain sparse. The two
tiers share one native filesystem; they are not independent disaster backups.
Their total allocation is 7,051,231,232 bytes.

Independent Sol/high review corrected loop backing inode checks and per-thread
idle checks before retirement. All 30 disposable tests pass as root. The fixed
retirement procedure authenticated every remote file before its first unlink,
rechecked each target and active users, and recorded fsynced intent/completion
entries. All twelve removals completed. After remote descriptors closed,
builder free space was **6,765,948,928 bytes**, compared with 3,240,075,264 before
retirement. Later builds consume this space; retain all existing build guards.
No LVM resize or physical device operation occurred.

Raw records and reviewed code remain under
`out/historical-root-retention-20261106/` on codex. Root-private coordinator
snapshots remain beside the protected copies; the remote durable journal is
`/var/lib/a733-historical-root-retention-20261106/receipt.jsonl` on the builder.
The evidence pins the records and code hashes.

Before rerunning a historical command that requires one of the absent paths,
restore exactly its recorded file into the recorded builder parent. Authenticate
the complete source size/hash, require an absent destination and idle target,
and transfer through a new ordinary staging file. For raw images, require sparse
extent preservation and sufficient allocated space plus operating reserve; never
perform a dense 4 GiB copy silently. Fsync and fully hash the staged result
before publishing it without overwriting another file, then verify its final
path. Restoration of these actual files has not been exercised. The retained
gzip files provide another authenticated representation of each raw image.

This retention frees build workspace. It establishes no physical installation,
rollback, KVM guest execution or release acceptance.

## Later transport-envelope retirement

On November 9, the redundant `out/historical-root-retention-20261106/encoded-inputs.tar`
transport envelope was retired after all six compressed members matched both
held protected canonical copies. Its entire tar grammar, saved literal headers,
zero padding/footer and full SHA-256 are recorded in
[the reviewed actual retirement evidence](evidence/native-transport-envelope-retirement-20261109.json).
All canonical raw/gzip files and loose inbox files remain. This releases
860,504,064 allocated bytes; the new full-image candidate was then retained on
codex with 735,850,496 bytes free, above its unchanged 256 MiB floor.
The transport tar is not a build input. Reconstruct it only if needed using
recorded headers and canonical members, and verify its original size/hash; that
reconstruction has not been performed.

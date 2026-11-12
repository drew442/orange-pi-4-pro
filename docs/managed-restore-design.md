# Managed guest restoration transaction

Reviewed coordinator contract, 2026-11-12. Independent architecture review
resolved six findings; implementation and hardware acceptance remain pending.
This is not an available command or hardware result. The historical
decoder and retained archive reader already validate source artifacts; they do
not supply destination authority. See [the production plan](spi-production-readiness-plan.md).

## Authority and supported destination

The first implementation restores an existing registered managed guest on the
same physical STATE/p5 pair. The operator supplies the guest name, trusted whole
archive SHA-256 and manifest SHA-256. These are data inputs, not capabilities.
Require exact current policy, NVMe model/serial/bytes, STATE/p5 UUIDs, guest name,
UID/GID and supported shutdown/fixture profiles. Reject `firstkvm`. Reject
storage rebasing and identity changes explicitly; replacement-drive recovery
needs its own reviewed identity migration, rather than rewriting old evidence.

Use installed root tools in the native initial namespaces. Before accessing the
archive, observe the existing admission runtime, monotonically close it for
shutdown, stop managed supervisors, and acquire the actual readonly immediate
shutdown EX lease. This is a new shutdown closure or an observation of that
closure; it adopts no bootstrap/maintenance nonce and can never reopen admission.
Require the actual retained physical HostContext and a bounded VM-consumer
census. A failed bootstrap with an existing runtime can be recovered this way;
a missing or unsafe runtime must refuse rather than construct replacement
authority inside the restore command.

All guests remain stopped until a fresh host boot. This deliberately uses the
existing shutdown lease's no-reopen contract. A successful restore does not
queue guests or claim successful boot. Retain SSH/serial access for inspection.
The normal storage-stop helper must be able to acquire shutdown EX after the
restore command releases its lease; do not stop/unmount p5 underneath restoration.

Take target guest EX, retain protected payload/lifecycle parent descriptors and
stopped disk custody, then take registry EX last. Never require the damaged
target's current disk hash or ready cohort to pass: that is the recovery case.
Require every unrelated guest to have its current authenticated ready cohort,
and preserve its complete entry and files. Check the current target identity
against the registry, not untrusted damaged payload metadata. Refuse unexpected
symlinks, hardlinks, mounts, special files, foreign owners or unbounded records.
The restore-only custody implementation must not broaden ordinary maintenance's
requirement that every guest is ready.

Failed bootstrap may have created neither RAM accounts nor managed markers.
Use a dedicated restore-only verifier with actual typed shutdown EX custody,
HostContext, guest/disk locks and final registry lock. It authenticates current
disk bytes, immutable inputs, manifest/history and exact protected historical
records against the current registry pin and legal completed-cohort predicates.
For the restored target, bind these to the independently trusted archive. Missing
RAM accounts/markers are the only relaxed observations; unsafe persistent
metadata or unexplained unrelated disk bytes refuse. Do not fabricate ordinary
checkpoint custody or weaken normal Inspection. Restore completion establishes
a durable cohort, not boot readiness. Fresh boot must create accounts/markers
and pass the ordinary same-family ready authenticator before launch.

For an original damaged disk, observe and record its actual bytes with progress
without requiring those bytes to match the registry. Incomplete resume compares
that observation as well as inode/location metadata. An originally absent disk
has an explicit absence observation and no disk FD/lock to acquire.

Before replacing an old payload directory, perform a bounded all-task census
for disk/input FDs and mappings, cwd/root/executable references and the target
UID's remaining processes. Refuse consumers in addition to VM consumers; moving
an old disk under a private parent does not revoke existing descriptors.

## Publication and durable records

Use a root-owned 0700 transaction directory on STATE, immutable 0400 records,
and independently pinned stage/retained directory names on p5. A transaction
UUID is generated for a new explicit request. Never select a transaction by
"most recent" or adopt an unrelated staging directory.

1. Authenticate the full encoded archive before decoding. Retain the readonly
   source and shared flock. Validate all six members and legal historical
   lifecycle records. Stage decoded payloads privately on p5; publish nothing
   until complete payload hashes, gzip footer and EOF pass. Report byte progress.
   Use the source reader's existing full authentication/scan guarantees; optimize
   repeated scans only in a separately reviewed change.
2. Stage the exact historical head and required run/transition records in a
   private lifecycle directory. Preserve archive evidence byte for byte. Check
   ownership/modes, inode bindings, file fsync and directory fsync before the
   publication intent. Normal payload ownership remains root for immutable inputs
   and the assigned guest UID/GID for the writable raw disk.
3. Under fresh registry EX, derive a new registry from the current destination
   registry. Replace only the target manifest pin; preserve its current enabled
   flag and all unrelated entries. Its generation must be strictly greater than
   both the current destination and archived registry generations, within the
   schema limit. This makes old whole-registry equality irrelevant to the
   checkpoint verifier without fabricating historical completion evidence.
   Record both exact old/new registry bytes and hashes in the intent. Exhaustion
   refuses before target replacement.
4. Durably publish an immutable intent binding transaction/source hashes, storage,
   target identity, exact old/new registry bytes, old head observation (including
   absent/damaged state), staged payload/head hashes and retained directory inode
   identities. The intent explicitly authorizes resetting this target to the
   trusted archive cohort. Retain old payload and lifecycle directories under
   transaction-specific names inside a root-owned 0700 p5 transaction directory;
   the guest UID must not retain access to its old writable disk. Do not delete
   or overwrite those originals.
5. Rename the old payload directory to its recorded retained name, then publish
   the staged payload directory at the target name. Do the corresponding lifecycle
   directory replacement. Fsync each affected parent and record acknowledged
   transitions. Cross-filesystem atomicity is not assumed. Admission remains
   closed throughout gaps and partial publication; no launch is permitted.
6. Publish the exact new registry with old-hash CAS last. Verify every restored
   destination payload and record with the restore-only verifier described above,
   including actual metadata, hashes and completed-cohort semantics under retained
   locks. Durably publish data-commit completion referencing the intent and all
   final bytes. Finish descriptor/source/context cleanup before reporting command
   success. If cleanup fails after data commit, preserve the acknowledged commit
   while reporting command failure and cleanup uncertainty. Do not erase the
   commit or relabel it as incomplete data. Boot census checks data commit;
   ordinary fresh boot authentication remains independently mandatory.

Restoring the matching disk and all its lifecycle evidence together permits a
trusted historical sequence to replace a damaged/newer target. An old STATE
registry alone never permits that reset. Retained pre-restore directories and the
explicit reset intent provide provenance outside the restored historical chain.
No synthetic guest-owned shutdown proof, completion or checkpoint is generated.

## Interruption and explicit retry

The concrete lock schedule is global shutdown EX, unrelated readonly cohort
verification, target guest EX and old-disk EX, then a short registry EX section
to freeze the plan. Release registry EX before opening/locking any new disk.
Retain old disk custody through cleanup. Acquire staged/restored disk EX before
the final registry EX section. Global EX excludes cooperating registry writers;
each section still checks the exact recorded current registry bytes. Dedicated
rename-aware handles pin device/inode/type/owner/mode and verify the expected
named location for each phase. Ordinary RootDirectory/Inspection handles must
not be reused across renamed edges. Fsync moved directories and both parents
before acknowledging a rename, refreshing timestamps only after inode checks.

The durable intent precedes any replacement of canonical target paths. Harmless
private pre-intent stages are not restore authority and cannot be resumed as an
intent. Keep them under a reserved root-only namespace outside guest discovery;
bootstrap must explicitly ignore those stages while refusing unsafe metadata.
After a pre-intent failure an explicit new request may use a new UUID and stage;
the old private stage stays retained for separately verified cleanup. No guest
path, lifecycle head or registry may change before the intent exists. Run the
transaction census after acquiring physical context and before STATE.prepare,
account/marker preparation or admission opening.

An immutable intent permits only this cross-component order. Each old-object
observation is either its pinned inode or explicit absence; an absent original
needs no retention rename. New-object observations require the recorded staged
inode, all exact bytes and safe metadata. An acknowledged phase record may lag
one successful filesystem operation after an interruption.

| Phase | Canonical payload | Canonical lifecycle | Registry |
| --- | --- | --- | --- |
| Intent durable | Original or absent; new stage private | Original or absent; new stage private | Old |
| Old payload retained | Absent; old retained if originally present | Original or absent | Old |
| New payload published | Exact new payload; old retained if present | Original or absent | Old |
| Old lifecycle retained | Exact new payload | Absent; old retained if originally present | Old |
| New lifecycle published | Exact new payload | Exact new historical cohort | Old |
| Registry committed | Exact new payload | Exact new historical cohort | Exact new |
| Data commit durable | As above, fully verified and synced | As above, fully verified and synced | Exact new |

During intent/phase/completion record construction, publication may have happened
without acknowledgement. Resume first verifies the exact named record and its
parent durability; partial temporary records grant no phase authority. A state
combining a later registry with an earlier payload/lifecycle phase always refuses.

Any mutation/publication error leaves admission shutdown-closed and retains
staging, originals and receipts. Do not automatically retry a write or delete
an uncertain object. Readonly observation may classify the last operation.

An explicit resume names the transaction UUID and supplies the same source pins.
It reacquires fresh native shutdown EX and destination authority; an old receipt
cannot act as a lease. Revalidate every named inode and exact published record.
Accept only states reachable by the recorded ordered rename/publication steps:
old object at original name, old object retained with target absent, or exact
staged object at target with its recorded retained predecessor. Re-establish
file/parent durability before advancing. Conflicting duplicate names or unknown
bytes refuse. Registry must equal the recorded old or new bytes; preserve any
later legitimate mutation by refusing instead of replaying a stale intent.
Before resuming incomplete publication, compare exact payload bytes, lifecycle
records/head and inode locations, not merely the registry pin: a later run can
change disk/head without yet changing the registry. If any such change occurred,
resume refuses. Completed-repeat and bootstrap census validate immutable commit
provenance without requiring the guest to retain historical bytes after a later
legitimate run. A completed-repeat never writes or rechecks historical bytes as
if they were the current guest state.

Completed transactions are observation-only on repeat. Never restore them again
after a subsequent guest run or registry change. Before reboot, a readonly
transaction census must reject incomplete transactions, so bootstrap cannot
mistake a visible but incompletely durable restored cohort for completed recovery.
The preparer must perform that census before creating accounts or opening
admission. Recovery can resume with its shutdown lease even when bootstrap has
refused. A completed restore requires fresh boot preparation and actual KVM
validation before hardware acceptance.

## Required review and evidence

Independent review must resolve concrete lock ordering, directory publication,
readiness verification with increased registry generation, bootstrap transaction
census and all partial-construction/cleanup behavior before integration. Test
each rename/fsync/close boundary, old/new registry CAS, interrupted head/payload
replacement, stale resume, corrupted archive, unrelated guest preservation,
identity mismatch, generation exhaustion and an actual held SH lease refusal.
Use disposable kernel filesystems for cross-directory publication and retry.

Finally restore the disposable managed guest on the A733, reboot, prove nonroot
KVM CPU/disk/network with the expected archived counter, guest-owned shutdown,
checkpoint and restart. Combine qualifying host transitions with the final boot
series. Keep `firstkvm`, SD recovery, SPI backups and accepted endurance data.

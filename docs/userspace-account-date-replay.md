# Account dates and cross-day userspace reproduction

A fresh tracked pipeline from commit `443b9f2` configured all 220 locked packages
and completed each child phase, but its outer exact archive check correctly
failed. [The retained failure](evidence/userspace-repeat-attempt-20261107.json)
records unchanged host inventory, clean process/mount exit and resource minima.
The failed result must not seed the time-profile launcher as a passing baseline.

Of 15,876 BOM entries, only `etc/shadow` and `etc/shadow-` differ from the
historical normalized archive. Their 45 locked-account last-change dates changed
from Unix day 20763 (November 6) to 20764 (November 7). Password fields and every
other field are unchanged; metadata and the other 15,874 entries match. Earlier
same-day matches establish no cross-day guarantee. Historical hashes, receipts
and producer source locks remain unchanged.

The new internal [helper](../build/account-dates.py) has closed baseline/time
policies taken from the authenticated historical archives. It changes only those
locked-account dates in a private generic build clone, preserving other bytes,
row order, inode, numeric ownership, permissions, timestamps and attributes.
It rejects malformed accounts, unsupported identities and unsafe files. It has
no CLI and is not an installed account-repair tool. Both files are prevalidated;
an I/O error may leave a partial update, so callers must discard the clone.

All 17 focused parser/filesystem regressions pass under root on codex and
Jammy/Python 3.10, and independent Sol/high review found no blocking issue.
[Validation evidence](evidence/account-date-policy-20261107.json) records the
full-suite capacity limits, affected-module reruns and successful shell/assembly
checks. Both historical archive account sets
and the fixed time account identity were separately inspected. **The helper is
wired into a [separate replay driver](account-date-replay-driver.md); no corrected
fresh archive is claimed until actual execution and complete comparison pass.**

A global `SOURCE_DATE_EPOCH` is unsuitable for preserving these historical
bytes: pinned Python 3.11 `py_compile` selects hash-based bytecode when that
variable is present, while the archives contain timestamp bytecode. The selected
approach is a separate authenticated replay adapter/driver using the unchanged
export cores. It must normalize only after configure/clone authentication and
before final BOM/archive creation, record the transformation honestly, retain
all historical closure checks and require exact archive/raw-tar/BOM hashes.
The time launcher also needs a strict validator for the new replay receipt and
source manifest; it must not accept the old failed receipt. Integration,
independent review and actual fresh baseline/time reproduction remain next work.

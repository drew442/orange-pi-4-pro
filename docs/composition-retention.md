# Verified workspace retention before composition

The [November 7 retention evidence](evidence/composition-retention-20261107.json)
records complete archives on **both build and codex** before retiring three
terminal generated work directories. All original phase records outside `work`
remain. Current production roots/state, operator inputs, successful firmware
candidates and original source caches are unchanged. No Orange Pi storage was
accessed. The independent reviewer approved the exact retirement script before
execution.

| Retired builder path under `/var/lib/a733-clean-replay-20261106/` | Members | Unique allocated bytes | Archive bytes |
| --- | ---: | ---: | ---: |
| `sd-run-03/work` | 2,311 | 796,688,384 | 229,482,716 |
| `full-root-metadata-fixture-02/work` | 15,908 | 561,053,696 | 170,528,925 |
| `uboot-nvme-component-first-01/work` | 24,669 | 1,126,400,000 | 372,738,635 |

Archives and source inventories are retained on the builder in
`/var/lib/a733-composition-retention-20261107/` and in a protected codex mirror
at `/var/lib/a733-composition-retention-20261107/mirror/`. Each archive is
`TAG.tar.gz`; each inventory is `TAG.inventory.json`. The evidence pins every
archive, inventory, controller and journal. The old SD replay is superseded by
the formal replay; the root fixture is historical; the U-Boot attempt failed
before compilation. These remain useful historical evidence, with archived
work paths rather than live expanded trees.

Before retirement, GNU tar compared every archived member against its source.
Independent native checks authenticated archive hashes and checked exact member
coverage, modes, numeric ownership, nanosecond mtime, symlink targets, hardlink
topology and all raw xattrs. There are three contained hardlink groups and no raw
xattrs or unaccounted ACLs in these actual trees. Every copy was fsynced, with
root-owned protected retention directories. Source inventories were unchanged.

The controller then revalidated source identities, archives and protected phase
parents, checked all mount namespaces, loop backings and process references,
and wrote a durable intent journal. It renamed each exact `work` directory to a
private quarantine through held parent descriptors, fsynced the parent and
deleted only that verified quarantine. All three removals completed. An
interrupted attempt must be inspected using `retirement.jsonl`; never blindly
rerun it or overlay a retained phase.

Free builder space rose from **21,664,014,336** to **24,148,152,320 bytes** during
retirement. The latter is only **525,832,192 bytes** above the unchanged 22 GiB
composition guard. Archive creation had already consumed its retained disk space.
Recheck after bootstrap and before composition; retirement is not admission for
future work. Codex retains a separate 256 MiB minimum free-space guard.

## Recovery

Full restoration of these three archives has not been executed. Before using an
archive, verify its exact evidence hash and allocate enough fixed storage for
the expanded tree plus working reserve. Use a new, empty, private destination:

```sh
restore_path=/var/lib/a733-restoration-sd-run-03-new
sudo test ! -e "$restore_path" && sudo mkdir -m700 "$restore_path"
sudo tar --extract --gzip --sparse --numeric-owner --same-owner \
  --same-permissions --acls --xattrs --xattrs-include='*' \
  --file /var/lib/a733-composition-retention-20261107/sd-run-03.tar.gz \
  --directory "$restore_path"
```

The restored subtree is `work/`. Compare its complete namespace, file contents,
numeric ownership, permissions, mtime, links and raw attributes against the
authenticated archive/inventory before using it. Inode numbers, ctime and atime
cannot be reproduced from these inventories. Restoring a historical workspace
does not establish a successful rebuild or physical deployment. Start new build
attempts with fresh tags and committed sources. See GNU tar's
[extended attributes](https://www.gnu.org/software/tar/manual/html_node/Extended-File-Attributes.html)
and [sparse files](https://www.gnu.org/software/tar/manual/html_node/sparse.html)
documentation for the metadata options.

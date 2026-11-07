# Repair management after the first installed boot

The November 7 serial logs show a successful NVMe write/readback and installed
root A reaching systemd. The YT8531 links at 1 Gbps, but management never gets an
IPv4 address. The installed machine-id was created with mode `0600` and bound
onto `/etc/machine-id`. The unprivileged systemd-networkd service needs to read
that file for its default DHCP identity. The source correction seeds it as
`0644`; updated management preparation also repairs the earlier mode. The
[diagnosis and software proof](evidence/installed-host-fix-20261107.json) records
the actual deployed metadata and pinned ARM library reproduction.

There is no root password. SSH requires your public keys; the normal serial
login prompt cannot authenticate the locked root account. Use the existing
installer's physical root shell for this repair. **Do not reinstall NVMe.**

1. Power off the board. Write the existing installer SD image listed in
   [the deployment guide](deploy-personalized-20261106.md#two-sd-images-in-this-order)
   to the same identified SD card, then boot it with serial capture running.
2. At its `/ #` prompt, paste the block below. It checks the known idle NVMe and
   state UUID, then changes only the machine-id permission on NVMe state. It
   does not download an image, hash/read back the disk, alter root slots or
   write SPI.

```sh
sh -eu <<'A733_REPAIR'
/usr/bin/python3 -I -B /usr/lib/a733-installer/plan-nvme.py \
  --target /dev/nvme0n1 --expected-serial 134807190FC700149277 \
  --expected-bytes 256060514304 > /run/a733-repair-plan.json
/usr/bin/python3 -I -B - <<'PY'
import json
p = json.load(open('/run/a733-repair-plan.json'))
assert p['target']['model'] == 'SPCC M.2 PCIe SSD', 'wrong NVMe model'
print('Repair target: SPCC M.2 PCIe SSD, serial 134807190FC700149277; machine-id permissions only', flush=True)
PY
test "$(blkid -p -s UUID -o value /dev/nvme0n1p4)" = 00fd936c-49fb-4fea-87c8-928b9c1599e0
mkdir -m 700 /run/a733-repair-state
mount -t ext4 -o rw,nodev,nosuid,noexec /dev/nvme0n1p4 /run/a733-repair-state
trap 'umount /run/a733-repair-state' EXIT
/usr/bin/python3 -I -B - <<'PY'
import os, pathlib, re, stat
root = pathlib.Path('/run/a733-repair-state')
for p in (root, root / 'identity'):
    s = p.lstat()
    assert stat.S_ISDIR(s.st_mode) and s.st_uid == s.st_gid == 0
    assert not s.st_mode & 0o7022
assert (root / '.a733-state').read_bytes() == b'a733-state-v1\n00fd936c-49fb-4fea-87c8-928b9c1599e0\n'
p = root / 'identity/machine-id'
fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
try:
    s = os.fstat(fd)
    assert stat.S_ISREG(s.st_mode) and s.st_uid == s.st_gid == 0 and s.st_nlink == 1
    assert stat.S_IMODE(s.st_mode) in (0o400, 0o444, 0o600, 0o644)
    value = os.read(fd, 34)
    assert re.fullmatch(b'[0-9a-f]{32}\n', value) and value != b'0' * 32 + b'\n'
    os.fchmod(fd, 0o644)
    os.fsync(fd)
    print('Machine-id permissions: %04o -> 0644; contents preserved' % stat.S_IMODE(s.st_mode), flush=True)
finally:
    os.close(fd)
PY
umount /run/a733-repair-state
trap - EXIT
printf '%s\n' 'Repair complete. No NVMe reinstall is needed.'
A733_REPAIR
```

3. If the block succeeds, run `poweroff -f`. With power disconnected, write the
   same matching host SD image from the deployment guide and boot it. SD
   readback hashes are optional troubleshooting checks; no NVMe installation is
   repeated. Keep Ethernet connected.
4. Save the complete host serial log. Look for the management IPv4 address and
   `A733 SSH host key` fingerprint. Compare that fingerprint before connecting
   from codex with `ssh root@ADDRESS`, using either authorized key. Return the
   log and SSH result. If DHCP still fails, stop here and return the log; this
   repair has not yet been tested on the board.

This is a permission repair, not an installed-network acceptance result. Actual
SSH with both keys and [KVM guest execution](kvm-first-guest.md) remain required.

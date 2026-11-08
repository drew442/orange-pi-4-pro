"""Small host fixtures: archive and policy safety, never hardware acceptance."""
import base64
import gzip
import importlib.util
import io
from pathlib import Path
import stat
import struct
import tarfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('derive_ssh_test', REPO / 'build/derive-ssh-installer.py')
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)
F = stat.S_IFREG
DIR = stat.S_IFDIR


def fixture(extra=None):
    entries = {'init': (F | 0o755, b'old init'), 'etc/passwd': (F | 0o644, b'root'),
               'etc/group': (F | 0o644, b'root'), 'etc': (DIR | 0o755, b''),
               'kept': (F | 0o640, b'unchanged')}
    entries.update(extra or {})
    return D.INIT.newc(entries)


def ssh_field(data):
    return struct.pack('>I', len(data)) + data


def key_fixture():
    public = b'p' * 32
    blob = ssh_field(b'ssh-ed25519') + ssh_field(public)
    secret = b'12341234' + ssh_field(b'ssh-ed25519') + ssh_field(public)
    secret += ssh_field(b's' * 32 + public) + ssh_field(b'private comment')
    secret += bytes(range(1, (-len(secret) % 8) + 1))
    binary = b'openssh-key-v1\0' + ssh_field(b'none') + ssh_field(b'none') + ssh_field(b'')
    binary += struct.pack('>I', 1) + ssh_field(blob) + ssh_field(secret)
    private = b'-----BEGIN OPENSSH PRIVATE KEY-----\n' + base64.b64encode(binary) + b'\n-----END OPENSSH PRIVATE KEY-----\n'
    canonical, fingerprint = D.COMMON.validate_public_key(b'ssh-ed25519 ' + base64.b64encode(blob))
    return private, canonical, fingerprint


class DerivationTests(unittest.TestCase):
    def test_preserves_raw_records_and_permits_only_explicit_mutations(self):
        raw = fixture()
        # mtime survives byte-for-byte, independent of writer canonicalization.
        before_items, _ = D.parse(raw)
        item = next(i for i in before_items if i['name'] == 'kept')
        changed = bytearray(raw)
        changed[item['start'] + 46:item['start'] + 54] = b'12345678'
        raw = bytes(changed)
        updated, final, mutations, preserved = D.merge(raw, {'init': (F | 0o755, b'new'), 'root/.ssh': (DIR | 0o700, b'')})
        self.assertEqual(set(mutations), {'init'})
        self.assertIn('kept', preserved)
        self.assertEqual(final['root/.ssh'][0], DIR | 0o700)
        after = next(i for i in D.parse(updated)[0] if i['name'] == 'kept')
        self.assertEqual(raw[item['start']:item['end']], updated[after['start']:after['end']])
        with self.assertRaisesRegex(ValueError, 'preserved'):
            D.merge(raw, {'kept': (F | 0o600, b'replaced')})

    def test_ancestor_and_symlink_escape_rejected(self):
        for entries in ({'kept/child': (F | 0o600, b'x')}, {'alias': (stat.S_IFLNK | 0o777, b'../../etc')}):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                D.parse(fixture(entries))
        with self.assertRaises(ValueError):
            D.merge(fixture(), {'kept/child': (F | 0o600, b'x')})

    def test_cpio_duplicate_truncation_trailing_and_unsupported_modes(self):
        raw = fixture()
        for malformed in (raw[:-1], raw + b'x', D.single_record('kept', (F | 0o644, b'x')) + raw,
                          fixture({'device': (stat.S_IFBLK | 0o600, b'')})):
            with self.subTest(length=len(malformed)), self.assertRaises(ValueError):
                D.parse(malformed)
        # Non-root owner rejected.
        changed = bytearray(raw)
        changed[22:30] = b'00000001'
        with self.assertRaisesRegex(ValueError, 'root-owned'):
            D.parse(bytes(changed))

    def test_gzip_expansion_bound(self):
        self.assertEqual(D.unpack(gzip.compress(b'abc'), 3), b'abc')
        with self.assertRaisesRegex(ValueError, 'budget'):
            D.unpack(gzip.compress(b'x' * 10000), 128)
        with self.assertRaises((EOFError, OSError)):
            D.unpack(gzip.compress(b'abc')[:-4])

    def test_base_manifest_requires_full_entry_identity(self):
        import json
        raw = fixture({'etc/shadow': (F | 0o600, b'root:!:1:0:99999:7:::\n')})
        _, entries = D.parse(raw)
        manifest = {'schema': 1, 'files': {'initramfs.cpio.gz': D.BASE},
                    'entries': {n: {'mode': m, **D.RUNTIME.record(v)} for n, (m, v) in entries.items()}}
        with self.assertRaisesRegex(ValueError, 'maintenance'):
            D.base_manifest(json.dumps(manifest).encode(), entries)
        manifest['entries']['kept']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'manifest'):
            D.base_manifest(json.dumps(manifest).encode(), entries)

    def test_public_key_and_identity_pins_fail_closed(self):
        private, public, fingerprint = key_fixture()
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            D.host_public(private)
        with patch.object(D, 'HOST_FINGERPRINT', fingerprint):
            self.assertEqual(D.host_public(private), public)
        with self.assertRaisesRegex(ValueError, 'both pinned'):
            D.operator_keys(public)
        with patch.object(D, 'HOST_FINGERPRINT', fingerprint):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w') as archive:
                for name, data, mode in [(D.IDENTITY_MEMBER, private, 0o644), (D.IDENTITY_MEMBER + '.pub', public, 0o644)]:
                    info = tarfile.TarInfo(name)
                    info.size, info.mode = len(data), mode
                    archive.addfile(info, io.BytesIO(data))
            with self.assertRaisesRegex(ValueError, '0600'):
                D.identity(stream.getvalue())

    def test_policy_and_init_requirements(self):
        lines = (REPO / 'configs/installer-sshd_config').read_text().splitlines()
        policy = dict(line.split(None, 1) for line in lines)
        for name, value in {'AddressFamily': 'inet', 'UsePAM': 'yes', 'AuthenticationMethods': 'publickey',
                            'PasswordAuthentication': 'no', 'KbdInteractiveAuthentication': 'no',
                            'PermitRootLogin': 'prohibit-password', 'StrictModes': 'yes',
                            'DisableForwarding': 'yes', 'AllowUsers': 'root'}.items():
            self.assertEqual(policy[name], value)
        self.assertNotIn('Match', policy)
        init = (REPO / 'configs/installer-ssh-init').read_text()
        self.assertIn('10:f4:ae:90:5e:57', init)
        self.assertIn('mount -t devpts', init)
        self.assertIn('sshd -t -f', init)
        self.assertIn('ssh_network_ready', init)
        self.assertIn('/dev/console', init)
        commands = [line.strip() for line in init.splitlines() if line.strip() and not line.startswith('#')]
        for command in commands:
            self.assertFalse(command.startswith(('swapon ', 'mkfs', 'dd ', 'reboot', 'mount /dev/')))
            self.assertNotIn('install-nvme.py --', command)
        self.assertEqual((REPO / 'configs/installer-sshd.pam').read_text(),
                         'auth required pam_deny.so\naccount required pam_unix.so\npassword required pam_deny.so\nsession required pam_permit.so\n')


if __name__ == '__main__':
    unittest.main()

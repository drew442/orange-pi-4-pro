"""Management fixtures only: never mount, start listeners or touch host config."""
import base64
import copy
import hashlib
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('host_management', Path(__file__).resolve().parents[1] / 'configs/host-management.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
CONFIG = {'schema': 1, 'hostname': 'a733-host', 'management_mac': '02:73:30:00:00:01',
          'management_cidr': '192.0.2.0/24', 'dhcp': True}
BLOB = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + b'k' * 32
PUBLIC = b'ssh-ed25519 ' + base64.b64encode(BLOB) + b'\n'
MOUNTS = '''20 1 259:2 / / ro,nodev - ext4 /dev/nvme0n1p2 ro
21 20 0:2 / /run rw,nosuid,nodev - tmpfs tmpfs rw
22 21 259:4 / /run/a733-state rw,nodev,nosuid - ext4 /dev/nvme0n1p4 rw
23 20 0:3 / /etc rw - overlay overlay rw,lowerdir=/newroot/etc,upperdir=/newroot/run/etc-upper,workdir=/newroot/run/etc-work
24 20 259:4 /var /var rw,nodev,nosuid - ext4 /dev/nvme0n1p4 rw
25 20 259:4 /root /root rw,nodev,nosuid - ext4 /dev/nvme0n1p4 rw
26 20 259:4 /home /home rw,nodev,nosuid - ext4 /dev/nvme0n1p4 rw
27 20 259:4 /identity/machine-id /etc/machine-id rw,nodev,nosuid - ext4 /dev/nvme0n1p4 rw
'''

MOUNTS += ''.join(f'{40+i} 20 259:2 /{M.PACKAGE.ROOT}/{name} /{target} ro,nodev,nosuid - ext4 /dev/nvme0n1p2 ro\n' for i,(name,(target,kind)) in enumerate(M.PACKAGE.COMPONENTS.items()))

class ValidationTests(unittest.TestCase):
    def test_strict_configuration(self):
        self.assertEqual(M.CONFIG.validate_config(json.dumps(CONFIG)), CONFIG)
        for field, value in [('hostname', 'x\nListenAddress 0.0.0.0'), ('management_mac', 'ff:ff:ff:ff:ff:ff'),
                             ('management_cidr', '192.0.2.1/24'),
                             ('schema', True), ('dhcp', 1)]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                M.CONFIG.validate_config(json.dumps({**CONFIG, field: value}))
        with self.assertRaises(ValueError):
            M.CONFIG.validate_config(json.dumps(CONFIG)[:-1] + ',"hostname":"dup"}')

    def test_public_key_wire_format(self):
        self.assertEqual(M.CONFIG.validate_public_key(PUBLIC)[0], PUBLIC)
        for data in [b'command="sh" ' + PUBLIC, PUBLIC + PUBLIC, PUBLIC.replace(b'ssh-ed25519', b'ssh-rsa', 1),
                     b'ssh-ed25519 ' + base64.b64encode(BLOB[:-1])]:
            with self.assertRaises(ValueError):
                M.CONFIG.validate_public_key(data)

    def test_mount_guard_rejects_builder_sd_other_namespace_writable_and_bad_binds(self):
        M.validate_mounts(M.mount_table(MOUNTS))
        for old, new in [('/dev/nvme0n1p2', '/dev/sda2'), ('/dev/nvme0n1p4', '/dev/nvme1n1p4'),
                         ('/ ro,nodev', '/ rw,nodev'), ('/run rw,nosuid,nodev', '/run rw,nosuid'),
                         ('/root /root', '/elsewhere /root'), ('/etc rw - overlay', '/etc rw - ext4')]:
            with self.subTest(new=new), self.assertRaises(ValueError):
                M.validate_mounts(M.mount_table(MOUNTS.replace(old, new)))
        with self.assertRaises(ValueError):
            M.validate_mounts(M.mount_table(MOUNTS + MOUNTS.splitlines()[0] + '\n'))

    def test_ram_overlay_and_nested_mount_rejection(self):
        with self.assertRaises(ValueError):
            M.validate_mounts(M.mount_table(MOUNTS.replace('upperdir=/newroot/run/etc-upper', 'upperdir=/run/a733-state/etc')))
        for target in ['/etc/systemd', '/run/a733-management', '/run/a733-management/config',
                       '/run/a733-state/identity/ssh', '/root/.ssh/authorized_keys']:
            # An exact management runtime mount is forbidden too.
            suffix = f'30 20 179:1 / {target} rw - ext4 /dev/mmcblk0p1 rw\n'
            with self.subTest(target=target), self.assertRaises(ValueError):
                M.validate_mounts(M.mount_table(MOUNTS + suffix))

    def test_all_package_views_require_selected_readonly_origin(self):
        M.validate_mounts(M.mount_table(MOUNTS.replace('259:2', '259:3').replace('nvme0n1p2', 'nvme0n1p3')))
        for name, (target, kind) in M.PACKAGE.COMPONENTS.items():
            original = next(line for line in MOUNTS.splitlines(True) if f' /{target} ' in line)
            changes = ('', original + original,
                       original.replace('ro,nodev,nosuid', 'rw,nodev,nosuid'),
                       original.replace('259:2', '179:1'),
                       original.replace('nvme0n1p2', 'nvme0n1p3'),
                       original.replace('/' + M.PACKAGE.ROOT + '/' + name, '/wrong-subdirectory'))
            for changed in changes:
                with self.subTest(name=name, change=changed), self.assertRaises(ValueError):
                    M.validate_mounts(M.mount_table(MOUNTS.replace(original, changed)))
            for foreign in ('/' + target + '/foreign', '/' + target.rsplit('/', 1)[0]):
                with self.subTest(foreign=foreign), self.assertRaises(ValueError):
                    M.validate_mounts(M.mount_table(MOUNTS + f'90 20 179:1 / {foreign} rw - ext4 /dev/mmcblk0p1 rw\n'))
        for foreign in ('/usr', '/usr/lib', '/usr/lib/orangepi-hypervisor', '/' + M.PACKAGE.ROOT):
            with self.subTest(foreign=foreign), self.assertRaises(ValueError):
                M.validate_mounts(M.mount_table(MOUNTS + f'90 20 179:1 / {foreign} rw - ext4 /dev/mmcblk0p1 rw\n'))

    def test_ssh_never_wildcard_and_key_only(self):
        rendered = M.ssh_config('192.0.2.20', CONFIG)
        self.assertEqual([x for x in rendered.splitlines() if x.startswith('ListenAddress')], ['ListenAddress 192.0.2.20'])
        for line in ['AddressFamily inet', 'AuthenticationMethods publickey', 'UsePAM yes',
                     'AllowUsers root@192.0.2.0/24', 'PasswordAuthentication no', 'DisableForwarding yes']:
            self.assertIn(line + '\n', rendered)
        for address in ['0.0.0.0', '127.0.0.1', '169.254.1.2', '224.1.1.1', '::1', '192.0.2.20\nPort 23']:
            with self.assertRaises(ValueError):
                M.ssh_config(address, CONFIG)

    def test_prepare_guard_precedes_every_write(self):
        with patch.object(M, 'runtime_guard', side_effect=ValueError('builder')), patch.object(M, 'atomic_write') as write, patch.object(M, 'ram_link') as link:
            with self.assertRaises(ValueError):
                M.prepare()
            write.assert_not_called()
            link.assert_not_called()

    def test_identity_output_reaches_journal_and_console(self):
        unit = (Path(__file__).resolve().parents[1] / 'configs/a733-management-ssh.service').read_text()
        self.assertIn('\nStandardOutput=journal+console\n', unit)

    def test_prepare_only_ram_paths_and_fixed_masks(self):
        with patch.object(M, 'runtime_guard', return_value=CONFIG), patch.object(M, 'prepare_clock_state'), patch.object(M, 'ram_directory'), patch.object(M, 'atomic_write') as write, patch.object(M, 'ram_link') as link, patch.object(M.subprocess, 'run') as command:
            M.prepare()
            for call in write.call_args_list:
                self.assertTrue(str(call.args[0]).startswith('/etc/'))
            links = dict(call.args for call in link.call_args_list)
            for name in ['ssh.service', 'sshd.service', 'ssh.socket', 'sshd.socket']:
                self.assertEqual(links['/etc/systemd/system/' + name], '/dev/null')
            for name in ('apt-daily.service','apt-daily.timer','apt-daily-upgrade.service','apt-daily-upgrade.timer','dpkg-db-backup.service','dpkg-db-backup.timer','systemd-journal-catalog-update.service'):
                self.assertEqual(links['/etc/systemd/system/' + name], '/dev/null')
            self.assertIn('a733-management-ssh.service', ' '.join(links))
            contents = dict((str(c.args[0]), c.args[1]) for c in write.call_args_list)
            self.assertIn('SystemMaxUse=128M', contents['/etc/systemd/journald.conf.d/90-a733-state.conf'])
            self.assertIn('Type=ether', contents['/etc/systemd/network/00-a733-management.network'])
            self.assertIn('IPv6AcceptRA=no', contents['/etc/systemd/network/00-a733-management.network'])
            command.assert_not_called()


class TimesyncGuardTests(unittest.TestCase):
    identity = '64504c34-85b4-4035-9618-d86b298f8349'
    helper = b'''def directory_ready(root, identity):
    return {'state_uuid': identity, 'directory': {'uid': 996}}
def audit(root):
    return {'state_uuid': '64504c34-85b4-4035-9618-d86b298f8349',
            'metadata': {'timesync': {}}}
'''

    def metadata(self):
        return {'sources': {'userspace_profile': {'profile': 'timesync-20261106'},
                'timesync_state': {'schema': 1, 'identity': 996,
                  'directory': 'var/lib/systemd/timesync',
                  'helper': {'bytes': len(self.helper),
                             'sha256': hashlib.sha256(self.helper).hexdigest()}}}}

    def test_baseline_never_requires_helper_or_state(self):
        values = ({}, {'sources': {}}, *({'sources': {'userspace_profile': {'profile': p}}}
                  for p in ('baseline-20261104', 'normalized-20261106')))
        with patch.object(M, 'read_secure', side_effect=AssertionError('helper read')):
            for value in values:
                self.assertIsNone(M.timesync_guard(value, 'ignored', boot=True))
                self.assertIsNone(M.timesync_guard(value, 'ignored'))

    def test_closed_profile_policy_rejects_unknown_or_malformed_features(self):
        mutations = []
        for profile in ('future', None, True):
            value = self.metadata()
            value['sources']['userspace_profile']['profile'] = profile
            mutations.append(value)
        for field, invalid in (('schema', True), ('identity', True), ('identity', 995),
                               ('directory', '/var/lib/systemd/timesync'),
                               ('helper', {'bytes': True, 'sha256': '0' * 64}),
                               ('helper', {'bytes': 1, 'sha256': 'A' * 64}),
                               ('helper', {'bytes': 1, 'sha256': '0' * 64, 'extra': 0})):
            value = self.metadata()
            value['sources']['timesync_state'][field] = invalid
            mutations.append(value)
        for key in self.metadata()['sources']['timesync_state']:
            value = self.metadata()
            del value['sources']['timesync_state'][key]
            mutations.append(value)
        value = self.metadata()
        value['sources']['timesync_state']['extra'] = 1
        mutations.append(value)
        for profile in ('baseline-20261104', 'normalized-20261106'):
            value = self.metadata()
            value['sources']['userspace_profile']['profile'] = profile
            mutations.append(value)
        value = self.metadata()
        del value['sources']['userspace_profile']
        mutations.append(value)
        for value in mutations:
            with self.subTest(value=value), patch.object(M, 'read_secure') as read:
                with self.assertRaises(ValueError):
                    M.timesync_guard(value, self.identity)
                read.assert_not_called()

    def test_boot_authenticates_helper_before_execution_and_audits_clock(self):
        metadata = self.metadata()
        with patch.object(M, 'read_secure', return_value=self.helper) as read:
            report = M.timesync_guard(metadata, self.identity, boot=True)
        self.assertEqual(report, {'state_uuid': self.identity, 'directory': {'uid': 996}})
        self.assertEqual(read.call_args.args[0], Path(M.__file__).parent.parent / 'build/timesync-state.py')
        poisoned = b"raise AssertionError('must not execute')\n"
        with patch.object(M, 'read_secure', return_value=poisoned), self.assertRaisesRegex(ValueError, 'helper differs'):
            M.timesync_guard(metadata, self.identity, boot=True)
        for altered in (self.helper.replace(b"'timesync': {}", b"'other': {}"),
                        self.helper.replace(self.identity.encode(), b'14504c34-85b4-4035-9618-d86b298f8349')):
            value = copy.deepcopy(metadata)
            value['sources']['timesync_state']['helper'] = {'bytes': len(altered), 'sha256': hashlib.sha256(altered).hexdigest()}
            with patch.object(M, 'read_secure', return_value=altered), self.assertRaises(ValueError):
                M.timesync_guard(value, self.identity, boot=True)

    def test_live_uses_current_trusted_checker_without_candidate_pin_or_full_audit(self):
        helper = self.helper.replace(b'def audit(root):', b"def audit(root):\n    raise AssertionError('live audit')")
        with patch.object(M, 'read_secure', return_value=helper):
            self.assertEqual(M.timesync_guard(self.metadata(), self.identity),
                             {'state_uuid': self.identity, 'directory': {'uid': 996}})
        with patch.object(M, '__file__', '/usr/lib/orangepi-hypervisor/host-management.py'), \
                patch.object(M, 'read_secure', return_value=helper) as read:
            M.timesync_guard(self.metadata(), self.identity)
            self.assertEqual(read.call_args.args[0], Path('/usr/lib/a733-tools/build/timesync-state.py'))

    def test_prepare_clock_check_precedes_ram_writes(self):
        with patch.object(M, 'runtime_guard', return_value=CONFIG), \
                patch.object(M, 'prepare_clock_state', side_effect=ValueError('clock')), \
                patch.object(M, 'ram_directory') as directory, patch.object(M, 'atomic_write') as write, \
                patch.object(M, 'ram_link') as link:
            with self.assertRaisesRegex(ValueError, 'clock'):
                M.prepare()
            directory.assert_not_called()
            write.assert_not_called()
            link.assert_not_called()

    def test_prepare_reads_selected_metadata_and_canonical_marker(self):
        metadata = self.metadata()
        marker = f'a733-state-v1\n{self.identity}\n'.encode()
        with patch.object(M, 'read_secure', side_effect=[json.dumps(metadata).encode(), marker]), \
                patch.object(M, 'timesync_guard') as guard:
            M.prepare_clock_state()
            guard.assert_called_once_with(metadata, self.identity, boot=True)
        with patch.object(M, 'read_secure', return_value=b'{"sources":{}}') as read:
            self.assertIsNone(M.prepare_clock_state())
            self.assertEqual(read.call_count, 1)
        for bad in (marker.upper(), marker[:-1], marker + b'\n'):
            with patch.object(M, 'read_secure', side_effect=[json.dumps(metadata).encode(), bad]), \
                    patch.object(M, 'timesync_guard') as guard, self.assertRaises(ValueError):
                M.prepare_clock_state()
            guard.assert_not_called()

    def test_runtime_builder_guard_has_no_mutations(self):
        with patch.object(M.os, 'geteuid', return_value=0), patch.object(M.Path, 'read_text', return_value=MOUNTS.replace('/dev/nvme0n1p2', '/dev/vda2')), patch.object(M.os, 'open') as opened, patch.object(M.subprocess, 'run') as command:
            with self.assertRaises(ValueError):
                M.runtime_guard()
            opened.assert_not_called()
            command.assert_not_called()


class AdministrationBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.keys = []
        cls.fingerprints = []
        for algorithm in ('rsa', 'ed25519'):
            path = Path(cls.temporary.name) / algorithm
            args = ['ssh-keygen', '-q', '-t', algorithm, '-N', '', '-f', str(path)]
            if algorithm == 'rsa':
                args.extend(['-b', '3072'])
            subprocess.run(args, check=True, capture_output=True)
            cls.keys.append(path.with_suffix('.pub').read_bytes())
            result = subprocess.run(['ssh-keygen', '-lf', str(path.with_suffix('.pub'))],
                                    check=True, capture_output=True, text=True)
            cls.fingerprints.append(result.stdout.split()[1])

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_real_rsa3072_ed25519_ordered_bundle(self):
        canonical, fingerprints = M.CONFIG.validate_public_keys(b''.join(self.keys))
        self.assertEqual(fingerprints, self.fingerprints)
        self.assertEqual(canonical, b''.join(M.CONFIG.validate_public_key(key)[0] for key in self.keys))
        spec = importlib.util.spec_from_file_location('independent_state_verifier',
            Path(__file__).with_name('verify_host_state_image.py'))
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        self.assertEqual(verifier.administration_fingerprints(canonical), fingerprints)
        for invalid in (canonical + canonical, canonical.replace(b'\n', b' comment\n', 1),
                        canonical.rstrip(b'\n')):
            with self.assertRaises(ValueError):
                verifier.administration_fingerprints(invalid)
        self.assertEqual(M.CONFIG.validate_public_keys(canonical), (canonical, fingerprints))
        with self.assertRaises(ValueError):
            M.CONFIG.validate_public_key(canonical)

    def test_bundle_rejects_duplicates_blanks_options_controls_and_bounds(self):
        for data in (b'', b'\n', PUBLIC + b'\n', PUBLIC + PUBLIC,
                     b'command="id" ' + PUBLIC, PUBLIC.replace(b'\n', b'\tcomment\n'),
                     PUBLIC + b'\x7f', b'x' * 16385,
                     b''.join(b'ssh-ed25519 ' + base64.b64encode(BLOB[:-1] + bytes([i])) + b'\n'
                              for i in range(17))):
            with self.subTest(data=data[:80]), self.assertRaises(ValueError):
                M.CONFIG.validate_public_keys(data)
        keys = b''.join(b'ssh-ed25519 ' + base64.b64encode(BLOB[:-1] + bytes([i])) + b'\n'
                        for i in range(16))
        self.assertEqual(len(M.CONFIG.validate_public_keys(keys)[1]), 16)

    def test_rsa_wire_format_and_mpint_rejections(self):
        def field(value):
            return struct.pack('>I', len(value)) + value
        def key(exponent, modulus, suffix=b''):
            blob = field(b'ssh-rsa') + field(exponent) + field(modulus) + suffix
            return b'ssh-rsa ' + base64.b64encode(blob) + b'\n'
        modulus = b'\x00\x80' + b'\x01' * 255
        self.assertEqual(M.CONFIG.validate_public_key(key(b'\x01\x00\x01', modulus))[0],
                         key(b'\x01\x00\x01', modulus))
        for exponent, number, suffix in ((b'', modulus, b''), (b'\x00\x03', modulus, b''),
                (b'\x80', modulus, b''), (b'\x02', modulus, b''), (b'\x01', modulus, b''),
                (b'\x01' * 5, modulus, b''), (b'\x03', b'\x80' + b'\x01' * 255, b''),
                (b'\x03', b'\x00' + modulus, b''), (b'\x03', b'\x7f' * 256, b''),
                (b'\x03', modulus[:-1] + b'\x02', b''), (b'\x03', modulus, b'x'),
                (b'\x03', b'\x00\x80' + b'\x01' * 2048, b'')):
            with self.subTest(exponent=exponent, number_bits=len(number)), self.assertRaises(ValueError):
                M.CONFIG.validate_public_key(key(exponent, number, suffix))
        with self.assertRaises(ValueError):
            M.CONFIG.validate_public_key(b'ssh-rsa AAAAB3NzaC1yc2EAAAAD')

    def test_any_ipv4_source_preserves_bound_listener_and_modern_algorithms(self):
        config = {**CONFIG, 'management_cidr': '0.0.0.0/0'}
        self.assertEqual(M.CONFIG.validate_config(json.dumps(config)), config)
        rendered = M.ssh_config('192.0.2.20', config)
        self.assertIn('AllowUsers root@0.0.0.0/0\n', rendered)
        self.assertIn('ListenAddress 192.0.2.20\n', rendered)
        self.assertIn('AddressFamily inet\n', rendered)
        self.assertNotIn('ssh-rsa', rendered)

    def test_host_identity_rejects_valid_rsa(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / M.KEY_NAME
            path.write_bytes(b'fixture-private')
            with patch.object(M, 'secure_path'), patch.object(M, 'read_secure', return_value=b'fixture-private'), \
                    patch.object(M.subprocess, 'run', return_value=Mock(stdout=self.keys[0])):
                with self.assertRaisesRegex(ValueError, 'Ed25519'):
                    M.ensure_host_key(Path(temp))
        with patch.object(M, 'read_secure', return_value=self.keys[0]), redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(ValueError, 'Ed25519'):
                M.log_host_identity('192.0.2.20', Path('/fixture') / M.KEY_NAME)
            self.assertEqual(output.getvalue(), '')


class AddressTests(unittest.TestCase):
    def address(self, addresses):
        result = Mock(stdout=json.dumps([{'ifname': 'end0', 'addr_info': addresses}]).encode())
        with patch.object(M, 'discover_interface', return_value='end0'), patch.object(M.subprocess, 'run', return_value=result) as command:
            value = M.current_address(CONFIG)
            self.assertEqual(command.call_args.args[0], ['/usr/sbin/ip', '-j', '-4', 'addr', 'show', 'dev', 'end0'])
            return value

    def test_one_usable_ipv4_only(self):
        row = {'family': 'inet', 'scope': 'global', 'local': '192.0.2.20'}
        self.assertEqual(self.address([row]), '192.0.2.20')
        self.assertIsNone(self.address([{**row, 'tentative': True}]))
        self.assertIsNone(self.address([{**row, 'flags': ['deprecated']}]))
        self.assertIsNone(self.address([{**row, 'preferred_life_time': 0}]))
        self.assertIsNone(self.address([{**row, 'local': '169.254.1.1'}]))
        with self.assertRaises(ValueError):
            self.address([row, {**row, 'local': '192.0.2.21'}])

    def test_missing_interface_never_queries_ip(self):
        with patch.object(M, 'discover_interface', return_value=None), patch.object(M.subprocess, 'run') as run:
            self.assertIsNone(M.current_address(CONFIG))
            run.assert_not_called()


class KeyTests(unittest.TestCase):
    def fixture_secure(self, path, mode=None, directory=False):
        # Real file I/O is confined to a private temporary fixture; bypass only
        # production uid/ancestor requirements so the tests work unprivileged.
        info = Path(path).lstat()
        self.assertFalse(Path(path).is_symlink())
        if mode is not None:
            self.assertEqual(info.st_mode & 0o777, mode)
        return info

    def test_generate_once_reuse_and_recover_public(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(M, 'secure_path', side_effect=self.fixture_secure):
            directory = Path(temp)
            def command(args, **_kwargs):
                if '-y' in args:
                    return Mock(stdout=PUBLIC)
                candidate = Path(args[-1])
                candidate.write_bytes(b'fixture-private')
                candidate.chmod(0o600)
                candidate.with_name(candidate.name + '.pub').write_bytes(PUBLIC)
                return Mock()
            with patch.object(M.subprocess, 'run', side_effect=command) as run:
                key = M.ensure_host_key(directory)
                original = key.read_bytes()
                expected = f'A733 SSH host key for 192.0.2.20: ED25519 {M.CONFIG.validate_public_key(PUBLIC)[1]}\n'
                with redirect_stdout(io.StringIO()) as output:
                    M.log_host_identity('192.0.2.20', key)
                self.assertEqual(output.getvalue(), expected)
                self.assertNotIn(original.decode(), output.getvalue())
                self.assertNotIn(PUBLIC.decode().strip(), output.getvalue())
                key.with_name(key.name + '.pub').unlink()
                M.ensure_host_key(directory)
                with redirect_stdout(io.StringIO()) as output:
                    M.log_host_identity('192.0.2.20', key)
                self.assertEqual(output.getvalue(), expected)
                self.assertEqual(key.read_bytes(), original)
                self.assertEqual(sum('-t' in c.args[0] for c in run.call_args_list), 1)
                self.assertEqual(key.stat().st_nlink, 1)
                self.assertEqual(key.with_name(key.name + '.pub').read_bytes(), PUBLIC)

    def test_invalid_public_identity_never_logged(self):
        key = Path('/run/a733-state/identity/ssh') / M.KEY_NAME
        for value in (b'not-a-key', PUBLIC + PUBLIC, b'-----BEGIN OPENSSH PRIVATE KEY-----\n'):
            with self.subTest(value=value), patch.object(M, 'read_secure', return_value=value) as read, \
                    redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(ValueError):
                    M.log_host_identity('192.0.2.20', key)
                read.assert_called_once_with(key.with_name(key.name + '.pub'), 16384)
                self.assertEqual(output.getvalue(), '')
        with patch.object(M, 'read_secure', side_effect=ValueError('unsafe metadata')), \
                redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(ValueError):
                M.log_host_identity('192.0.2.20', key)
            self.assertEqual(output.getvalue(), '')
        with patch.object(M, 'read_secure') as read, redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(ValueError):
                M.log_host_identity('192.0.2.20\nready', key)
            read.assert_not_called()
            self.assertEqual(output.getvalue(), '')

    def test_public_private_mismatch_never_replaces_identity(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(M, 'secure_path', side_effect=self.fixture_secure):
            key = Path(temp) / M.KEY_NAME
            key.write_bytes(b'keep-identity')
            key.chmod(0o600)
            public = key.with_name(key.name + '.pub')
            public.write_bytes(b'ssh-ed25519 ' + base64.b64encode(BLOB[:-32] + b'l' * 32) + b'\n')
            with patch.object(M.subprocess, 'run', return_value=Mock(stdout=PUBLIC)), \
                    redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(ValueError):
                    M.ensure_host_key(Path(temp))
                self.assertEqual(output.getvalue(), '')
            self.assertEqual(key.read_bytes(), b'keep-identity')

    def test_identity_log_flushes_before_returning(self):
        with patch.object(M, 'read_secure', return_value=PUBLIC), patch('builtins.print') as printed:
            M.log_host_identity('192.0.2.20', Path('/fixture') / M.KEY_NAME)
        printed.assert_called_once_with(
            f'A733 SSH host key for 192.0.2.20: ED25519 {M.CONFIG.validate_public_key(PUBLIC)[1]}', flush=True)

    def test_atomic_publication_cannot_replace_existing_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, destination = directory / 'new', directory / 'existing'
            source.write_bytes(b'new-identity')
            destination.write_bytes(b'original-identity')
            with self.assertRaises(FileExistsError):
                M.publish_private_key(source, destination)
            self.assertEqual(source.read_bytes(), b'new-identity')
            self.assertEqual(destination.read_bytes(), b'original-identity')
            self.assertEqual(destination.stat().st_nlink, 1)

    def test_publication_before_sync_failure_is_reusable_single_link(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(M, 'secure_path', side_effect=self.fixture_secure):
            directory = Path(temp)
            def command(args, **_kwargs):
                if '-y' in args:
                    return Mock(stdout=PUBLIC)
                candidate = Path(args[-1])
                candidate.write_bytes(b'fixture-private')
                candidate.chmod(0o600)
                return Mock()
            with patch.object(M.subprocess, 'run', side_effect=command), patch.object(M, 'fsync_directory', side_effect=OSError('fixture interruption after rename')):
                with self.assertRaises(OSError):
                    M.ensure_host_key(directory)
            key = directory / M.KEY_NAME
            self.assertEqual(key.stat().st_nlink, 1)
            self.assertEqual(key.read_bytes(), b'fixture-private')
            with patch.object(M.subprocess, 'run', side_effect=command) as run:
                self.assertEqual(M.ensure_host_key(directory), key)
                self.assertEqual(key.stat().st_nlink, 1)
                self.assertEqual(sum('-t' in c.args[0] for c in run.call_args_list), 0)
                self.assertEqual(key.with_name(key.name + '.pub').read_bytes(), PUBLIC)

    def test_unsupported_publication_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, destination = directory / 'new', directory / 'target'
            source.write_bytes(b'new-identity')
            with patch.object(M.ctypes, 'CDLL', return_value=object()):
                with self.assertRaises(OSError):
                    M.publish_private_key(source, destination)
            self.assertEqual(source.read_bytes(), b'new-identity')
            self.assertFalse(destination.exists())

    def test_generation_failure_leaves_no_private_key(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(M, 'secure_path', side_effect=self.fixture_secure), patch.object(M.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['ssh-keygen'])):
            with self.assertRaises(subprocess.CalledProcessError):
                M.ensure_host_key(Path(temp))
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_existing_private_key_never_replaced_on_derivation_failure(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(M, 'secure_path', side_effect=self.fixture_secure):
            key = Path(temp) / M.KEY_NAME
            key.write_bytes(b'keep-identity')
            key.chmod(0o600)
            with patch.object(M.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['ssh-keygen'])):
                with self.assertRaises(subprocess.CalledProcessError):
                    M.ensure_host_key(Path(temp))
            self.assertEqual(key.read_bytes(), b'keep-identity')


class SupervisorTests(unittest.TestCase):
    def run_loop(self, addresses, stopped, clock=None):
        events = []
        children = []
        def spawn(_args):
            child = Mock()
            child.poll.return_value = None
            child.terminate.side_effect = lambda: events.append('stop')
            children.append(child)
            events.append('start')
            return child
        contexts = [patch.object(M, 'runtime_guard', return_value=CONFIG),
                    patch.object(M, 'current_address', side_effect=addresses),
                    patch.object(M, 'ensure_host_key'), patch.object(M, 'atomic_write'),
                    patch.object(M, 'log_host_identity', side_effect=lambda address, key: events.append('identity ' + address)),
                    patch.object(M.subprocess, 'Popen', side_effect=spawn),
                    patch.object(M.time, 'sleep'), patch.object(M.time, 'monotonic', side_effect=clock or [0] * 30)]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for context in contexts:
                stack.enter_context(context)
            M.supervise(CONFIG, Mock(side_effect=stopped))
        return events, children

    def test_dhcp_change_stops_before_restart_and_term_cleanup(self):
        events, _ = self.run_loop(['192.0.2.20', '192.0.2.21'], [False, False, True])
        self.assertEqual(events, ['identity 192.0.2.20', 'start', 'stop', 'identity 192.0.2.21', 'start', 'stop'])

    def test_dhcp_loss_stops_listener_until_address_returns(self):
        events, _ = self.run_loop(['192.0.2.20', None, '192.0.2.21'], [False, False, False, True])
        self.assertEqual(events, ['identity 192.0.2.20', 'start', 'stop', 'identity 192.0.2.21', 'start', 'stop'])

    def test_missing_ip_is_bounded(self):
        with self.assertRaises(ValueError):
            self.run_loop([None], [False], clock=[0, 61])

    def test_invalid_identity_stops_before_listener(self):
        for failing in ('ensure_host_key', 'log_host_identity'):
            with self.subTest(failing=failing), patch.object(M, 'runtime_guard', return_value=CONFIG), \
                    patch.object(M, 'current_address', return_value='192.0.2.20'), \
                    patch.object(M, 'ensure_host_key'), patch.object(M, 'atomic_write'), \
                    patch.object(M, 'log_host_identity'), patch.object(M.subprocess, 'Popen') as spawn, \
                    patch.object(M, failing, side_effect=ValueError('invalid identity')):
                with self.assertRaises(ValueError):
                    M.supervise(CONFIG, lambda: False)
                spawn.assert_not_called()

    def test_guard_failure_terminates_existing_listener(self):
        child = Mock()
        child.poll.return_value = None
        with patch.object(M, 'runtime_guard', side_effect=[CONFIG, ValueError('lost state')]), patch.object(M, 'current_address', return_value='192.0.2.20'), patch.object(M, 'ensure_host_key'), patch.object(M, 'atomic_write'), patch.object(M, 'log_host_identity'), patch.object(M.subprocess, 'Popen', return_value=child), patch.object(M.time, 'sleep'):
            with self.assertRaises(ValueError):
                M.supervise(CONFIG, lambda: False)
        child.terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()

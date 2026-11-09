import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load('endurance_collector', 'scripts/collect-kvm-endurance.py')
L = load('endurance_load', 'scripts/endurance-cpu-load.py')
BOOT = '11111111-1111-4111-8111-111111111111'
RUN = '22222222-2222-4222-8222-222222222222'


def fixture():
    status = 'Uid: 60002 60002 60002 60002\nGid: 60002 60002 60002 60002\nNoNewPrivs: 1\n'
    status += ''.join(key + ': 0000000000000000\n' for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'))
    return {'boot_id': BOOT, 'unit': {'MemoryMax': '536870912'}, 'blocks': {'mmcblk1': {'identity': dict(C.SD_IDENTITY), 'mounted': False, 'stat': [0] * 17}},
            'network': [{'rx_bytes': 100, 'tx_bytes': 200}],
            'thermal': [{'temp': 40000, 'critical': [100000]}], 'nvme_errors': [],
            'endpoint_state': 'running', 'process': {'status': status,
            'exe': '/usr/bin/qemu-system-aarch64', 'argv': ['qemu-system-aarch64', '-accel', 'kvm', '-cpu', 'host',
            '-machine', 'virt,gic-version=host', '-m', '256M', '-smp', '4'],
            'fds': ['anon_inode:kvm-vm', 'anon_inode:kvm-arm-vgic-v3'] + ['anon_inode:kvm-vcpu:' + str(i) for i in range(4)]},
            'guest': {'schema': 1, 'profile': C.PROFILE, 'mode': 'smoke', 'run_id': RUN,
            'required_seconds': 60, 'state': 'running', 'elapsed_seconds': 30, 'cycles': 1,
            'disk_counter': 1, 'cpu_workers': 4, 'cpu_workers_alive': 4, 'disk_sha256': C.ZERO_DISK_SHA},
            'payload_bytes': C.PAYLOAD_SIZE, 'payload_sha256': C.ZERO_PAYLOAD_SHA}


class ProtocolTests(unittest.TestCase):
    def test_payload_transfer_is_hashed_without_file(self):
        meta = json.dumps({'hello': 'world'}).encode()
        raw = b'ENDURANCE1 1048576 ' + str(len(meta)).encode() + b'\n' + bytes(C.PAYLOAD_SIZE) + meta
        data = C.decode_frame(raw)
        self.assertEqual(data['payload_sha256'], hashlib.sha256(bytes(C.PAYLOAD_SIZE)).hexdigest())

    def test_bounds_and_malformed_frames(self):
        for raw in (b'', b'BAD 0 2\n{}', b'ENDURANCE1 -1 2\n{}', b'ENDURANCE1 0 65537\n{}',
                    b'ENDURANCE1 0 2\n{}x', b'ENDURANCE1 0 3\n{}', b'x' * (C.MAX_FRAME + 1)):
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                C.decode_frame(raw)

    def test_nonfinite_and_nonobject(self):
        for body in (b'{"x":NaN}', b'{"x":Infinity}', b'[]'):
            with self.assertRaises(ValueError):
                C.decode_frame(b'ENDURANCE1 0 ' + str(len(body)).encode() + b'\n' + body)

    def test_timeout_kills_source(self):
        with self.assertRaisesRegex(ValueError, 'timeout'):
            C.bounded_ssh([sys.executable, '-c', 'import time; time.sleep(2)'], '', timeout=0.03)

    def test_source_exit_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'failed'):
            C.bounded_ssh([sys.executable, '-c', 'raise SystemExit(7)'], '')


class ValidationTests(unittest.TestCase):
    def test_readonly_evidence_never_hardware_acceptance(self):
        self.assertEqual(C.validate(fixture(), BOOT, 'smoke'), 'readonly_evidence')

    def test_exact_memory_profile_required(self):
        for memory in ('512', '512M', '1024'):
            data = fixture()
            argv = data['process']['argv']
            argv[argv.index('-m') + 1] = memory
            with self.subTest(memory=memory), self.assertRaisesRegex(ValueError, 'limits'):
                C.validate(data, BOOT, 'smoke')
        for memory_max in (None, 'infinity', 'max', '268435456', '1073741824', 536870912):
            data = fixture()
            if memory_max is None:
                data['unit'] = {}
            else:
                data['unit']['MemoryMax'] = memory_max
            with self.subTest(memory_max=memory_max), self.assertRaisesRegex(ValueError, 'MemoryMax'):
                C.validate(data, BOOT, 'smoke')

    def test_nvme_baseline_error_explicitly_requires_coordinator(self):
        clean = fixture()
        C.validate(clean, BOOT, 'smoke')
        self.assertEqual(clean['nvme_kernel_error_assessment'], 'no_errors_observed')
        data = fixture()
        data['nvme_errors'] = ['nvme I/O error']
        self.assertEqual(C.validate(data, BOOT, 'smoke'), 'readonly_evidence')
        self.assertEqual(data['nvme_kernel_error_assessment'], 'coordinator_review_required')
        self.assertEqual(C.validate(data, BOOT, 'smoke', data), 'readonly_evidence')
        changed = fixture()
        changed['nvme_errors'] = ['nvme I/O error', 'nvme reset']
        with self.assertRaisesRegex(ValueError, 'NVMe'):
            C.validate(changed, BOOT, 'smoke', data)

    def test_progress_short_stall_then_advance(self):
        watch = C.ProgressWatchdog()
        data = fixture()
        watch.check(data, 100)
        watch.check(data, 220)
        data['guest']['cycles'] += 1
        data['guest']['elapsed_seconds'] += 30
        # The per-run disk counter correctly stays unchanged.
        watch.check(data, 221)
        self.assertEqual(data['collector_progress']['seconds_since_advance'],
                         {'elapsed_seconds': 0, 'cycles': 0})

    def test_progress_frozen_clock_or_workload_rejected_independently(self):
        for frozen in ('both', 'elapsed_seconds', 'cycles'):
            watch = C.ProgressWatchdog()
            data = fixture()
            watch.check(data, 100)
            for now in (130, 160, 190, 220):
                for key in ('elapsed_seconds', 'cycles'):
                    if frozen not in ('both', key):
                        data['guest'][key] += 1
                watch.check(data, now)
            with self.subTest(frozen=frozen), self.assertRaisesRegex(ValueError, 'stalled'):
                watch.check(data, 220.01)
            self.assertGreater(data['collector_progress']['seconds_since_advance'][
                'cycles' if frozen == 'both' else frozen], C.PROGRESS_TIMEOUT)

    def test_smoke_cannot_substitute_release(self):
        with self.assertRaises(ValueError):
            C.validate(fixture(), BOOT, 'release')

    def test_release_duration_is_exact_and_legacy_evidence_needs_separate_review(self):
        self.assertEqual(C.DURATIONS, {'smoke': 60, 'release': 28800})
        for seconds in (28799, 28800, 28801, 86400):
            data = fixture()
            data['guest'].update(mode='release', required_seconds=seconds)
            with self.subTest(seconds=seconds):
                if seconds == 28800:
                    self.assertEqual(C.validate(data, BOOT, 'release'), 'readonly_evidence')
                else:
                    with self.assertRaisesRegex(ValueError, 'duration'):
                        C.validate(data, BOOT, 'release')

    def test_changed_ids_and_counters(self):
        original = fixture()
        for key, value in [('run_id', BOOT), ('elapsed_seconds', 0), ('cycles', 0), ('disk_counter', 0)]:
            data = fixture()
            data['guest'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                C.validate(data, BOOT, 'smoke', original, original)
        data = fixture()
        data['boot_id'] = RUN
        with self.assertRaisesRegex(ValueError, 'boot'):
            C.validate(data, BOOT, 'smoke')

    def test_missing_source_fields_fail_closed(self):
        for key in ('blocks', 'network', 'thermal', 'process', 'guest'):
            data = fixture()
            del data[key]
            with self.subTest(key=key), self.assertRaises((KeyError, ValueError)):
                C.validate(data, BOOT, 'smoke')

    def test_only_known_mmcblk1_identity_accepted(self):
        data = fixture()
        self.assertEqual(C.validate(data, BOOT, 'smoke'), 'readonly_evidence')
        for field, wrong in (('cid', '0' * 32), ('model', 'UNKNOWN'),
                             ('size_bytes', 1977614336 + 512), ('major_minor', '179:32'),
                             ('device', 'mmcblk2')):
            data = fixture()
            data['blocks']['mmcblk1']['identity'][field] = wrong
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'identity'):
                C.validate(data, BOOT, 'smoke')
        data = fixture()
        del data['blocks']['mmcblk1']['identity']
        with self.assertRaisesRegex(ValueError, 'identity'):
            C.validate(data, BOOT, 'smoke')

    def test_remote_reads_and_checks_sysfs_identity(self):
        tree = ast.parse(C.REMOTE)
        helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == 'checked_sd_identity')
        base = '/sys/class/block/mmcblk1'
        values = {base + '/dev': '179:0', base + '/device/cid': C.SD_IDENTITY['cid'],
                  base + '/device/name': 'SU02G', base + '/size': str(1977614336 // 512)}
        namespace = {'P': Path, 'EXPECTED_SD': C.SD_IDENTITY, 'read': values.__getitem__}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), '<SD helper>', 'exec'), namespace)
        check = namespace['checked_sd_identity']
        self.assertEqual(check(base), C.SD_IDENTITY)
        for suffix, wrong in (('/device/cid', '0' * 32), ('/device/name', 'UNKNOWN'),
                              ('/size', '1'), ('/dev', '179:32')):
            with self.subTest(suffix=suffix), patch.dict(values, {base + suffix: wrong}):
                with self.assertRaisesRegex(ValueError, 'identity'):
                    check(base)
        self.assertLess(C.REMOTE.index('identity = checked_sd_identity(base)'),
                        C.REMOTE.index("read(base + '/stat')"))

    def test_sd_writes_mounts_and_thermals(self):
        for index in (4, 5, 6, 7, 11, 12, 15):
            data = fixture()
            data['blocks']['mmcblk1']['stat'][index] = 1
            with self.subTest(index=index), self.assertRaises(ValueError):
                C.validate(data, BOOT, 'smoke')
        for patch_data in ('mounted', 'hot', 'no_trip', 'nvme_error', 'hash', 'workers'):
            data = fixture()
            if patch_data == 'mounted':
                data['blocks']['mmcblk1']['mounted'] = True
            elif patch_data == 'hot':
                data['thermal'][0]['temp'] = 100000
            elif patch_data == 'no_trip':
                data['thermal'][0]['critical'] = []
            elif patch_data == 'nvme_error':
                data['nvme_errors'] = ['nvme timeout']
            elif patch_data == 'hash':
                data['payload_sha256'] = '0' * 64
            else:
                data['guest']['cpu_workers_alive'] = 3
            with self.subTest(kind=patch_data), self.assertRaises(ValueError):
                C.validate(data, BOOT, 'smoke', fixture())

    def test_actual_kvm_credentials_and_fds_required(self):
        for kind in ('root', 'cap', 'nnp', 'tcg', 'fd', 'vcpu', 'cpu', 'machine', 'memory'):
            data = fixture()
            proc = data['process']
            if kind == 'root':
                proc['status'] = proc['status'].replace('60002', '0')
            elif kind == 'cap':
                proc['status'] = proc['status'].replace('CapEff: 0000000000000000', 'CapEff: 1')
            elif kind == 'nnp':
                proc['status'] = proc['status'].replace('NoNewPrivs: 1', 'NoNewPrivs: 0')
            elif kind in ('fd', 'vcpu'):
                proc['fds'].pop(1 if kind == 'fd' else -1)
            else:
                index = {'tcg': '-accel', 'cpu': '-cpu', 'machine': '-machine', 'memory': '-m'}[kind]
                proc['argv'][proc['argv'].index(index) + 1] = {'tcg': 'tcg', 'cpu': 'max', 'machine': 'virt', 'memory': '1024'}[kind]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                C.validate(data, BOOT, 'smoke')

    def test_inactive_unit_only_pending(self):
        data = fixture()
        data['endpoint_state'] = 'pending_external_completion'
        del data['guest']
        del data['process']
        self.assertEqual(C.validate(data, BOOT, 'smoke'), 'pending_external_completion')

    def test_remote_source_compiles_and_has_no_guest_control(self):
        compile(C.REMOTE, '<remote>', 'exec')
        for operation in ('reboot', 'poweroff', 'systemctl start', 'systemctl stop', 'mount(', 'ioctl'):
            self.assertNotIn(operation, C.REMOTE.replace('SMART ioctl not implemented', ''))
        self.assertIn('StrictHostKeyChecking=yes', (ROOT / 'scripts/collect-kvm-endurance.py').read_text())


class RecordingTests(unittest.TestCase):
    def collect(self, probes):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            key = parent / 'test-key'
            key.write_text('fixture only')
            output = parent / 'evidence'
            argv = ['collect', '--host', '192.0.2.1', '--key', str(key), '--boot-id', BOOT,
                    '--mode', 'release', '--output', str(output)]
            with patch.object(sys, 'argv', argv), patch.object(C, 'bounded_ssh', side_effect=probes), \
                    patch.object(C.time, 'sleep'), patch.object(C.time, 'monotonic', return_value=100), \
                    patch.object(C.shutil, 'disk_usage', return_value=type('Space', (), {'free': 1 << 30})()):
                code = C.main()
            records = [json.loads(line) for line in (output / 'samples.jsonl').read_text().splitlines()]
            return code, records

    def test_failed_validation_retains_exact_bounded_snapshot(self):
        data = fixture()
        data['unit']['MemoryMax'] = 'infinity'
        code, records = self.collect([data])
        self.assertEqual(code, 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['result'], 'failed')
        self.assertEqual(records[0]['sample']['unit']['MemoryMax'], 'infinity')
        self.assertFalse(records[0]['hardware_acceptance'])

    def test_source_failure_does_not_relabel_previous_sample(self):
        data = fixture()
        data['guest']['mode'] = 'release'
        data['guest']['required_seconds'] = 28800
        code, records = self.collect([data, ValueError('transport failed')])
        self.assertEqual(code, 1)
        self.assertEqual([record['result'] for record in records], ['readonly_evidence', 'failed'])
        self.assertNotIn('sample', records[-1])

    def test_release_sample_bound_is_eight_hours_plus_three_samples(self):
        data = fixture()
        data['guest'].update(mode='release', required_seconds=28800)
        # Frozen local time exercises the independent finite sample bound.
        code, records = self.collect([data] * 963)
        self.assertEqual(code, 2)
        self.assertEqual(len(records), 964)
        self.assertEqual(records[-1]['reason'], 'sample bound reached')
        self.assertTrue(all(record['hardware_acceptance'] is False for record in records))


class CpuTests(unittest.TestCase):
    def test_fixed_duration_profiles(self):
        self.assertEqual(L.DURATIONS, {'smoke': 60, 'release': 28800})

    def test_root_refused_before_fork(self):
        with patch.object(L.os, 'getuid', return_value=0), patch.object(L.os, 'fork') as fork:
            with self.assertRaisesRegex(ValueError, 'unprivileged'):
                L.run('smoke')
            fork.assert_not_called()

    def test_duration_cleanup_reaps_exactly_four_workers(self):
        with patch.dict(L.DURATIONS, {'smoke': 0}), patch.object(L.os, 'getuid', return_value=60003), \
                patch.object(L.os, 'geteuid', return_value=60003), patch.object(L.os, 'fork', side_effect=[11, 12, 13, 14]), \
                patch.object(L.os, 'kill') as kill, patch.object(L.os, 'waitpid', side_effect=lambda pid, flags: (pid, 0)) as wait, \
                patch.object(L.signal, 'signal'):
            self.assertEqual(L.run('smoke'), 0)
            self.assertEqual(kill.call_count, 4)
            self.assertEqual(wait.call_count, 4)

    def test_early_child_exit_fails_and_stops_remaining_workers(self):
        with patch.object(L.os, 'getuid', return_value=60003), patch.object(L.os, 'geteuid', return_value=60003), \
                patch.object(L.os, 'fork', side_effect=[11, 12, 13, 14]), \
                patch.object(L.os, 'waitpid', side_effect=lambda pid, flags: (pid, 256)), \
                patch.object(L.signal, 'signal'):
            self.assertEqual(L.run('smoke'), 1)

    def test_worker_hashes_one_mib_in_memory_and_nices(self):
        class Done(Exception):
            pass
        with patch.object(L.os, 'nice') as nice, patch.object(L.signal, 'signal'), \
                patch.object(L.hashlib, 'sha256', side_effect=Done) as digest:
            with self.assertRaises(Done):
                L.worker()
            nice.assert_called_once_with(19)
            self.assertEqual(len(digest.call_args.args[0]), 1024 * 1024)


if __name__ == '__main__':
    unittest.main()

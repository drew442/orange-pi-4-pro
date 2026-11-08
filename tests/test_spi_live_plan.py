"""Host-only rejection tests; these never open an MTD device."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'spi_live_plan', Path(__file__).resolve().parents[1] / 'scripts/plan-spi-nvme.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


class LivePlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = b'\xff' * P.SIZE
        candidate = bytearray(cls.baseline)
        for start, end in P.RANGES:
            candidate[start:end] = b'\x00' * (end-start)
        cls.candidate = bytes(candidate)
        cls.manifest = dict(schema=1, stage='assembled-file-only', success=True,
            erase_size=P.ERASE, baseline=dict(bytes=P.SIZE, sha256=P.sha(cls.baseline)),
            candidate=dict(bytes=P.SIZE, sha256=P.sha(cls.candidate)),
            spi_identity=dict(device='/dev/mtd0', dev='90:0'),
            affected_erase_units=list(range(26)), affected_erase_end=26*P.ERASE,
            physical_writes=False, recovery_proven=False,
            hardware_acceptance=False, deployment_authorized=False)
        cls.manifest['erase_units'] = [dict(index=i, offset=i*P.ERASE,
            end=(i+1)*P.ERASE, original_sha256=P.sha(cls.baseline[i*P.ERASE:(i+1)*P.ERASE]),
            candidate_sha256=P.sha(cls.candidate[i*P.ERASE:(i+1)*P.ERASE]), changed=i < 26)
            for i in range(256)]
        def ranges(pairs):
            return [dict(offset=s, end=e, bytes=e-s, sha256=P.sha(cls.candidate[s:e]))
                    for s, e in pairs]
        cls.manifest['replacement_ranges'] = ranges(P.RANGES)
        cls.manifest['preserved_ranges'] = ranges([(237568,262144),(1654784,P.SIZE)])

    def pins(self, manifest=None):
        data = json.dumps(self.manifest if manifest is None else manifest).encode()
        stack = contextlib.ExitStack()
        stack.enter_context(patch.multiple(P, MANIFEST_SHA=P.sha(data),
            CANDIDATE_SHA=P.sha(self.candidate), BASELINE_SHA=P.sha(self.baseline)))
        self.addCleanup(stack.close)
        return data

    def read(self, fd, size, offset):
        return self.baseline[offset:offset+size]

    def test_complete_candidate_and_two_read_maps(self):
        m = P.validate_manifest(self.pins(), self.candidate)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(P.read_live(0, m, self.candidate, 1, self.read),
                             P.read_live(0, m, self.candidate, 2, self.read))
        self.assertEqual(sum(u['changed'] for u in m['erase_units']), 26)

    def test_malformed_or_incomplete_unit_map(self):
        for mutate in (lambda m:m['erase_units'].pop(),
                       lambda m:m['erase_units'][0].update(original_sha256='no'),
                       lambda m:m['erase_units'][0].update(index=1),
                       lambda m:m['erase_units'][26].update(changed=True),
                       lambda m:m['preserved_ranges'].pop()):
            with self.subTest(mutate=mutate):
                m = copy.deepcopy(self.manifest)
                mutate(m)
                with self.assertRaises(ValueError):
                    P.validate_manifest(self.pins(m), self.candidate)

    def test_full_manifest_and_candidate_pins(self):
        data = self.pins()
        for bad_data, candidate in ((data+b' ',self.candidate),
                                   (data, b'X'+self.candidate[1:])):
            with self.assertRaises(ValueError):
                P.validate_manifest(bad_data, candidate)

    def test_short_read_changed_live_and_extra_byte(self):
        self.pins()
        for reader in (lambda fd,n,o:b'',
                       lambda fd,n,o:b'X'+self.read(fd,n,o)[1:],
                       lambda fd,n,o:b'X' if o == P.SIZE else self.read(fd,n,o)):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(ValueError):
                P.read_live(0,self.manifest,self.candidate,1,reader)

    def comparison(self, identities=None, boots=None, reads=None):
        fd = os.open('/dev/null', os.O_RDONLY)
        info = os.fstat(fd)
        library = types.SimpleNamespace(
            open_source=lambda device:(os.dup(fd), self.manifest['spi_identity']),
            identity=lambda f,d:self.manifest['spi_identity'])
        self.addCleanup(os.close, fd)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(P.os, 'stat', return_value=info))
        stack.enter_context(patch.object(P,'boot_id', side_effect=boots or ['a','a']))
        if identities:
            library.identity = unittest.mock.Mock(side_effect=identities)
        if reads:
            stack.enter_context(patch.object(P,'read_live', side_effect=reads))
        else:
            stack.enter_context(patch.object(P,'read_live',return_value=('hash',['unit'])))
        return library

    def test_identity_boot_and_repeat_drift(self):
        for options in (dict(identities=[{}]), dict(boots=['a','b']),
                        dict(reads=[('a',[]),('b',[])])):
            library = self.comparison(**options)
            with self.assertRaises(ValueError):
                P.compare_live(library,'/dev/mtd0',self.manifest,self.candidate)
            self.doCleanups()

    def test_device_path_rebinding(self):
        library = self.comparison()
        with patch.object(P.os,'stat',return_value=types.SimpleNamespace(st_mode=0)):
            with self.assertRaises(ValueError):
                P.compare_live(library,'/dev/mtd0',self.manifest,self.candidate)

    def test_library_pin_before_execution(self):
        held = types.SimpleNamespace(data=b'raise RuntimeError("executed")')
        with self.assertRaisesRegex(ValueError,'source pin'):
            P.load_library(held)

    @unittest.skipUnless(os.geteuid() == 0, 'root-owned private path checks require root')
    def test_held_paths_mutability_aliases_drift_and_readonly_opens(self):
        with tempfile.TemporaryDirectory(prefix='spi-plan-test-',dir='/run') as directory:
            root = Path(directory)
            path = root/'input'
            path.write_bytes(b'input')
            path.chmod(0o400)
            original_open = os.open
            calls = []
            def audited_open(path, flags, *args, **kwargs):
                calls.append(flags)
                self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
                self.assertFalse(flags & (os.O_CREAT|os.O_TRUNC|os.O_APPEND))
                return original_open(path,flags,*args,**kwargs)
            with patch.object(P.os,'open',side_effect=audited_open):
                held = P.HeldFile(str(path),32)
                try:
                    held.check()
                    path.chmod(0o600)
                    path.write_bytes(b'other')
                    path.chmod(0o400)
                    with self.assertRaises(ValueError): held.check()
                finally: held.close()
            self.assertTrue(calls)
            for mode in (0o600,0o440):
                path.chmod(mode)
                with self.assertRaises(ValueError): P.HeldFile(str(path),32)
            path.chmod(0o400)
            alias = root/'alias'
            alias.symlink_to(path)
            with self.assertRaises(OSError): P.HeldFile(str(alias),32)
            alias.unlink()
            os.link(path,alias)
            with self.assertRaises(ValueError): P.HeldFile(str(path),32)
            alias.unlink()
            held = P.HeldFile(str(path),32)
            try:
                path.rename(root/'old')
                path.write_bytes(b'input')
                path.chmod(0o400)
                with self.assertRaises(ValueError): held.check()
            finally: held.close()
            with self.assertRaises(ValueError): P.HeldFile(str(root/'..'/'input'),32)

    def test_source_or_input_mutation_suppresses_success_output(self):
        data = self.pins()
        for failed_index in (0, 1, 2):
            held = [types.SimpleNamespace(fd=i, data=payload,
                        close=unittest.mock.Mock(), check=unittest.mock.Mock())
                    for i, payload in enumerate((b'library', data, self.candidate))]
            held[failed_index].check.side_effect = ValueError('mutation')
            output = io.StringIO()
            with patch.object(P, 'HeldFile', side_effect=held), \
                    patch.object(P, 'load_library', return_value=object()), \
                    patch.object(P, 'compare_live', return_value={}), \
                    patch.object(P.os, 'geteuid', return_value=0), \
                    patch.object(P.os, 'fstat', side_effect=lambda fd:
                        types.SimpleNamespace(st_dev=1, st_ino=fd)), \
                    contextlib.redirect_stdout(output), self.assertRaises(ValueError):
                P.main(['--device','/dev/mtd0','--candidate','/candidate',
                        '--manifest','/manifest'])
            self.assertEqual(output.getvalue(), '')
            self.assertTrue(all(item.close.called for item in held))

    def test_cli_no_json_on_failure(self):
        output = io.StringIO()
        with patch.object(P,'HeldFile',side_effect=ValueError('unsafe')), \
                patch.object(P.os,'geteuid',return_value=0), \
                contextlib.redirect_stdout(output), self.assertRaises(ValueError):
            P.main(['--device','/dev/mtd0','--candidate','/bad','--manifest','/bad'])
        self.assertEqual(output.getvalue(),'')


if __name__ == '__main__':
    unittest.main()

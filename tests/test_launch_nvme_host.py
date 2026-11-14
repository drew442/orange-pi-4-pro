"""Bounded admission/provenance tests; no production image or hardware execution."""
import ast
import contextlib
import io
import copy
import gzip
import hashlib
import importlib.util
import inspect
import stat
import struct
import shutil
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
import zlib
from unittest import mock

REPO = Path(__file__).resolve().parents[1]


def simulated_flags(**overrides):
    # Preserve every actual interpreter flag used by argparse/subprocess.
    values = {name: getattr(sys.flags, name) for name in dir(sys.flags) if not name.startswith("_")}
    return types.SimpleNamespace(**(values | overrides))


def load(path):
    spec = importlib.util.spec_from_file_location(path.replace('/', '_'), REPO / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


n = load('build/launch-nvme-host.py')
r = load('build/launch-host-roots.py')
s = load('build/launch-host-state.py')
v = load('build/verify-host-roots.py')
c = load('build/launch-uboot-nvme-candidate.py')
CONTENTS = {path: (REPO / path).read_bytes() for path in
            subprocess.check_output(['git', '-C', str(REPO), 'ls-files']).decode().splitlines()}
POLICY = n.contract(r, REPO, CONTENTS)


class NVMeLauncherTests(unittest.TestCase):
    def test_namespace_access_rejects_unmapped_private_diagnostic_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            with self.assertRaisesRegex(ValueError, 'exclude mapped builder'):
                n.namespace_source_access(root, root.stat().st_uid + 100000)

    def test_namespace_access_accepts_public_inputs_and_mapped_private_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            parent.chmod(0o700)  # Host ancestors are bypassed by the source bind.
            root = parent / 'diagnostic'
            root.mkdir()
            leaf = root / 'uImage'
            leaf.write_bytes(b'boot payload')
            leaf.chmod(0o444)
            root.chmod(0o555)
            n.namespace_source_access(root, root.stat().st_uid + 100000, leaf)
            root.chmod(0o500)
            leaf.chmod(0o400)
            n.namespace_source_access(root, root.stat().st_uid, leaf)

    def test_namespace_access_rejects_private_leaf_symlink_and_access_acl(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o755)
            leaf = root / 'uImage'
            leaf.write_bytes(b'boot payload')
            leaf.chmod(0o600)
            mapped = root.stat().st_uid + 100000
            with self.assertRaisesRegex(ValueError, 'exclude mapped builder'):
                n.namespace_source_access(root, mapped, leaf)
            leaf.unlink()
            leaf.symlink_to('/dev/null')
            with self.assertRaisesRegex(ValueError, 'physical namespace-readable'):
                n.namespace_source_access(root, mapped, leaf)
            leaf.unlink()
            leaf.write_bytes(b'boot payload')
            leaf.chmod(0o444)
            with mock.patch.object(n.os, 'listxattr', return_value=['system.posix_acl_access']):
                with self.assertRaisesRegex(ValueError, 'unmodeled source access ACL'):
                    n.namespace_source_access(root, mapped, leaf)
            with self.assertRaises(ValueError):
                n.namespace_source_access(root, mapped, root.parent / 'outside')

    def test_main_rejects_private_component_manifest_before_holding_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs = {}
            for name in n.INPUTS:
                root = Path(temporary) / name
                root.mkdir()
                if name != 'diagnostic':
                    manifest = root / 'manifest.json'
                    manifest.write_bytes(b'{}')
                    manifest.chmod(0o600 if name == 'roots' else 0o444)
                root.chmod(0o555)
                inputs[name] = root
            held = mock.Mock(side_effect=AssertionError('held input opened'))
            modules = {n.STATE: types.SimpleNamespace(held_class=lambda verifier: held),
                       n.BOOTSTRAP: object(), n.VERIFIER: object(),
                       r.SHARED: types.SimpleNamespace(UID_BASE=os.getuid() + 100000),
                       r.CANDIDATE: object()}
            contents = {key: b'' for key in modules}
            contents.update({n.SELF: b'controller', n.ROOTS: b''})
            fake = types.ModuleType('access_roots')
            fake.SHARED, fake.CANDIDATE = r.SHARED, r.CANDIDATE
            fake.module = lambda name, data: modules[name]
            fake.physical = r.physical
            fake.committed_sources = lambda project: ({'revision': 'a' * 40}, contents)
            fake.read_source = lambda path: b'controller'
            with mock.patch.object(n.types, 'ModuleType', return_value=fake), \
                    mock.patch.object(n.subprocess, 'check_output', return_value=b''), \
                    mock.patch.object(n.sys, 'flags', simulated_flags(isolated=True, dont_write_bytecode=True)), \
                    mock.patch.object(n, 'contract', return_value=POLICY), \
                    mock.patch.object(n, 'admit_descriptor_limit', return_value={}), \
                    mock.patch.object(n, 'INPUTS', inputs):
                with self.assertRaisesRegex(ValueError, 'exclude mapped builder.*manifest.json'):
                    n.main(['--project', str(REPO), '--tag', 'tiny'])
            held.assert_not_called()

    def test_plan_never_touches_runtime(self):
        with mock.patch.object(Path, 'lstat', side_effect=AssertionError('runtime access')), \
             mock.patch.object(Path, 'open', side_effect=AssertionError('runtime access')), \
             mock.patch.object(Path, 'mkdir', side_effect=AssertionError('phase created')), \
             mock.patch.object(n.resource, 'getrlimit', side_effect=AssertionError('process limit queried')), \
             mock.patch.object(n.resource, 'setrlimit', side_effect=AssertionError('process limit changed')):
            result = n.plan('tiny', {'revision': 'a' * 40}, POLICY)
        self.assertEqual(result['phase'], str(n.BASE / 'nvme-tiny'))
        self.assertEqual(result['output'], 'work/project/out/nvme-image-tiny')
        self.assertTrue(result['candidate_for_installation'])
        self.assertFalse(result['physical_write_authorization'])
        self.assertFalse(result['hardware_acceptance'])
        self.assertEqual(result['timeout_seconds'], 10800)
        self.assertEqual(result['minimum_process_open_file_soft_limit'], 4096)

    def test_main_plan_never_queries_or_changes_descriptor_limit(self):
        fake=types.ModuleType('plan_roots')
        contents={n.SELF:b'controller',n.ROOTS:b''}
        fake.committed_sources=lambda project:({'revision':'a'*40},contents)
        fake.read_source=lambda path:b'controller'
        output=io.StringIO()
        with mock.patch.object(n.types,'ModuleType',return_value=fake), \
             mock.patch.object(n.subprocess,'check_output',return_value=b''), \
             mock.patch.object(n.sys,'flags',simulated_flags(isolated=True,dont_write_bytecode=True)), \
             mock.patch.object(n,'contract',return_value=POLICY), \
             mock.patch.object(n.resource,'getrlimit',side_effect=AssertionError('limit query')), \
             mock.patch.object(n.resource,'setrlimit',side_effect=AssertionError('limit mutation')), \
             contextlib.redirect_stdout(output):
            n.main(['--project',str(REPO),'--tag','tiny','--plan'])
        self.assertEqual(json.loads(output.getvalue())['minimum_process_open_file_soft_limit'],4096)

    def test_process_descriptor_limit_raises_soft_and_preserves_hard(self):
        with mock.patch.object(n.resource, 'getrlimit', side_effect=[(1024,1048576),(4096,1048576)]), \
             mock.patch.object(n.resource, 'setrlimit') as setter:
            result=n.admit_descriptor_limit()
        setter.assert_called_once_with(n.resource.RLIMIT_NOFILE,(4096,1048576))
        self.assertEqual(result['soft_before'],1024)
        self.assertEqual(result['soft_after'],4096)
        self.assertEqual(result['hard'],1048576)
        self.assertEqual(result['scope'],'current producer process only')

    def test_process_descriptor_limit_refuses_low_hard_before_opening_inputs(self):
        with mock.patch.object(n.resource, 'getrlimit', return_value=(1024,2048)), \
             mock.patch.object(n.resource, 'setrlimit') as setter:
            with self.assertRaisesRegex(ValueError,'hard limit'):n.admit_descriptor_limit()
        setter.assert_not_called()

    def test_process_descriptor_limit_keeps_large_or_infinite_soft(self):
        for limits in ((4096,4096),(8192,1048576),(n.resource.RLIM_INFINITY,n.resource.RLIM_INFINITY)):
            with self.subTest(limits=limits), \
                 mock.patch.object(n.resource,'getrlimit',return_value=limits), \
                 mock.patch.object(n.resource,'setrlimit') as setter:
                result=n.admit_descriptor_limit()
                self.assertEqual(result['soft_before'],result['soft_after'])
                setter.assert_not_called()

    def test_process_descriptor_limit_allows_infinite_hard_and_verifies_result(self):
        infinity=n.resource.RLIM_INFINITY
        with mock.patch.object(n.resource,'getrlimit',side_effect=[(1024,infinity),(4096,infinity)]), \
             mock.patch.object(n.resource,'setrlimit') as setter:
            n.admit_descriptor_limit()
        setter.assert_called_once_with(n.resource.RLIMIT_NOFILE,(4096,infinity))
        for final in ((1024,1048576),(4096,8192)):
            with self.subTest(final=final), \
                 mock.patch.object(n.resource,'getrlimit',side_effect=[(1024,1048576),final]), \
                 mock.patch.object(n.resource,'setrlimit'):
                with self.assertRaisesRegex(ValueError,'after admission'):n.admit_descriptor_limit()

    def test_unsafe_tags(self):
        for tag in ('', '../bad', '/bad', 'bad tag', 'a' * 65):
            with self.subTest(tag=tag), self.assertRaises(ValueError): n.phase_path(tag)

    def test_target_geometry_exact(self):
        for key, value in (('bytes', 250000000000), ('logical_sector_bytes', 4096),
                           ('physical_sector_bytes', 4096), ('serial', 'other')):
            contents = dict(CONTENTS); doc = json.loads(contents[n.TARGET])
            doc['installation']['target'][key] = value
            contents[n.TARGET] = json.dumps(doc).encode()
            with self.subTest(key=key), self.assertRaises(ValueError): n.contract(r, REPO, contents)

    def test_parent_component_substitution_rejected(self):
        changes = [('roots', ('phase',), '/wrong'),
                   ('roots', ('construction_success',), False),
                   ('roots', ('full_filesystem_readback_success',), False),
                   ('bootstrap', ('state_uuid',), '00fd936c-49fb-4fea-87c8-928b9c1599e0'),
                   ('bootstrap', ('boot_profile', 'name'), 'baseline-el1'),
                   ('bootstrap', ('state_receipt_sha256',), 'a' * 64)]
        for name, keys, value in changes:
            contents = dict(CONTENTS); path = n.EVIDENCE[name]; doc = json.loads(contents[path]); row = doc
            for key in keys[:-1]: row = row[key]
            row[keys[-1]] = value; contents[path] = json.dumps(doc).encode()
            with self.subTest(name=name, keys=keys), self.assertRaises(ValueError): n.contract(r, REPO, contents)

    def test_reader_pin_rejected(self):
        contents = dict(CONTENTS); contents[n.VERIFIER] += b'\n'
        with self.assertRaisesRegex(ValueError, 'generic reader'): n.contract(r, REPO, contents)

    def test_only_original_component_records_no_baseline_archive(self):
        records = n.records(POLICY)
        self.assertEqual(len(records), 9)
        self.assertFalse(any(path.name in ('busybox', 'rootfs.tar.gz') for path, pin in records.values()))
        self.assertEqual(n.INPUTS['diagnostic'], Path('/var/lib/a733-bootstrap-inputs-20261107'))
        for name in ('roots', 'state', 'sd'):
            self.assertEqual(records[name + '-receipt'][0], n.PHASES[name] / 'receipt.json')

    def test_budget_preserves_22gib_guard_without_copying_components(self):
        fake = types.SimpleNamespace(git=lambda *args: b'a blob 100\n')
        manifest = {'files': {'one': {'bytes': 100}}}
        value = n.budget(fake, REPO, manifest, b'payload', b'run')
        self.assertEqual(value['recipe_bytes'], 22 * 1024**3)
        self.assertEqual(value['git_staging_bound_bytes'], 300 + 4196 + 64 * 1024**2)
        self.assertLess(value['capsule_bytes'], 100000)
        self.assertEqual(value['required_free_bytes'], sum(value[k] for k in
                         ('recipe_bytes', 'capsule_bytes', 'git_staging_bound_bytes')))

    def test_runtime_explicit_three_hour_boundary(self):
        sample = {'free_bytes': 3_000_000_000, 'available_memory_bytes': 1_000_000_000}
        n.check_resources(sample, 10800)
        for altered, elapsed in ((sample,10801), (dict(sample,free_bytes=2_999_999_999),0),
                                 (dict(sample,available_memory_bytes=999_999_999),0)):
            with self.assertRaises(ValueError): n.check_resources(altered, elapsed)
        with self.assertRaises(ValueError): r.check_resources(sample, 3601)
        self.assertIsNone(inspect.signature(r.execute).parameters['resource_check'].default)

    def test_default_root_program_bytes_preserved(self):
        data = r.payload_program('test')
        self.assertEqual(len(data), 3116)
        self.assertEqual(hashlib.sha256(data).hexdigest(), 'eec3a0c554579d649787114979f1ad126ba3884e85e595aad594b355c993f834')

    def test_namespace_nested_bindings_are_readonly(self):
        shared = types.SimpleNamespace(ROOT=Path('/frozen'), SEED=Path('/seed'))
        builder = types.SimpleNamespace(nspawn_command=lambda *a:
            ['systemd-nspawn', '--private-users-ownership=chown', '/bin/sh', '/probe'])
        phase = Path('/phase'); args = n.command(r, shared, builder, phase)
        self.assertIn('--private-users-ownership=off', args)
        self.assertIn('--read-only', args)
        self.assertIn('--bind-ro=/phase/inputs:/a733-debs', args)
        self.assertIn('--bind=/phase/work/private-scratch:/var/tmp', args)
        for name, path in n.INPUTS.items():
            self.assertIn('--bind-ro=' + str(path) + ':/a733-debs/' + name,args)
        self.assertEqual(args[-3:], ['/bin/sh','-c','/bin/sh /a733-probe.sh && exec /usr/bin/python3 -I -B /a733-install.sh'])

    def test_execute_calls_explicit_resource_hook_and_preserves_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); phase = root/'phase'
            sample = {'free_bytes': 20_000_000_000, 'available_memory_bytes': 3 * 1024**3}
            shared = types.SimpleNamespace(safe_directory=lambda *a,**kw:None, UID_BASE=os.getuid(),
                interrupted=lambda *a:None, stop_process=mock.Mock(),mapped_processes=lambda:[],
                phase_mounts=lambda p:[], INVENTORY=['inventory'],resource_sample=lambda:sample,
                atomic_json=lambda p,value:p.write_text(json.dumps(value)))
            builder = types.SimpleNamespace(validate_probe=lambda log:{})
            resource = mock.Mock()
            verify = mock.Mock(return_value={'passed':True})
            with mock.patch.object(r.os,'chown'), \
                 mock.patch.object(r.subprocess,'check_output',return_value=b'package\n'*842), \
                 mock.patch.object(r,'check_resources',side_effect=AssertionError('wrong callback')):
                r.execute(shared,c,builder,root,phase,{}, {}, b'package\n'*842,{},None,None,'tiny',
                    output_name='nvme-image-tiny',stage=lambda *a:{},
                    namespace_command=lambda *a:[sys.executable,'-c','print("probe")'],
                    verify=verify,expectations=lambda *a:{},resource_check=resource)
            resource.assert_called()
            self.assertEqual(verify.call_args.args[1],phase/'work/project/out/nvme-image-tiny')
            receipt=json.loads((phase/'receipt.json').read_bytes())
            self.assertTrue(receipt['success']);self.assertEqual(receipt['state'],'complete')

    def test_input_identity_bytes_and_parent_replacement(self):
        Held = s.held_class(v)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); parent=root/'parent';parent.mkdir();path=parent/'file';path.write_bytes(b'one')
            held = Held(path)
            try:
                path.write_bytes(b'two')
                with self.assertRaises(ValueError): held.unchanged()
            finally: held.close()
            held = Held(path)
            try:
                parent.rename(root/'old');parent.mkdir();(parent/'file').write_bytes(b'two')
                with self.assertRaises(ValueError): held.unchanged()
            finally: held.close()

    def test_unknown_outputs_fail_before_authentication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('manifest.json','nvme.img','nvme.img.gz','unexpected'):
                (root/name).write_bytes(b'')
            with self.assertRaisesRegex(ValueError,'namespace'):
                n.verify_output(c,root,POLICY,{},CONTENTS,r)
            (root/'unexpected').unlink();(root/'unexpected').mkdir()
            with self.assertRaisesRegex(ValueError,'namespace'):
                n.verify_output(c,root,POLICY,{},CONTENTS,r)

    def private_helpers(self):
        namespace = {'os': os, 'stat': stat, 'gzip': gzip, 'hashlib': hashlib, 'reader': v, 'struct': struct, 'sdinspection': load('tests/verify_host_sd.py')}
        exec(n.private_artifact_program(), namespace)
        return namespace

    def test_spi_script_wrapper_executes_valid_and_rejects_crc_header_body(self):
        helper=self.private_helpers();command=b'echo tiny\n'
        def script(body,kind=6):
            payload=struct.pack('>II',len(body),0)+body
            header=bytearray(64)
            struct.pack_into('>7I',header,0,0x27051956,0,0,len(payload),0,0,zlib.crc32(payload))
            header[28:32]=bytes((5,2,kind,0))
            struct.pack_into('>I',header,4,zlib.crc32(header))
            return bytes(header)+payload
        valid=script(command)
        self.assertTrue(helper['validate_spi_script'](valid,command))
        crc_bad=bytearray(valid);crc_bad[-1]^=1
        for data in (bytes(crc_bad),script(command,kind=3),b'short'):
            with self.subTest(data=data),self.assertRaises(ValueError):helper['validate_spi_script'](data,command)
        with self.assertRaisesRegex(AssertionError,'body differs'):
            helper['validate_spi_script'](script(b'echo other\n'),command)

    def test_generated_compression_executes_with_new_sibling_and_exact_header(self):
        helper = self.private_helpers()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); raw = root/'nvme.img'; target = root/'nvme.img.gz'
            data = bytes(range(256))*4;raw.write_bytes(data);expected = r.pin(data)
            proof = helper['compress_private'](raw,target,expected)
            self.assertEqual(gzip.decompress(target.read_bytes()),data)
            self.assertEqual(target.read_bytes()[:10],bytes.fromhex('1f8b08000000000002ff'))
            self.assertEqual(proof['gzip'],r.pin(target.read_bytes()))
            self.assertEqual(proof['raw'],expected)
            self.assertTrue(proof['input_identity_unchanged'])
            self.assertEqual(proof['gzip_expansion_independent_verification'],'pending')
            retained = target.read_bytes()
            with self.assertRaises(FileExistsError): helper['compress_private'](raw,target,expected)
            self.assertEqual(target.read_bytes(),retained)

    def test_generated_compression_rejects_wrong_pin(self):
        helper = self.private_helpers()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);raw=root/'raw';raw.write_bytes(b'x'*1024)
            for expected in ({'bytes':1024,'sha256':'0'*64},r.pin(b'y'*512)):
                target=root/('gzip-'+str(expected['bytes']))
                with self.subTest(expected=expected), self.assertRaises(AssertionError):
                    helper['compress_private'](raw,target,expected)

    def test_generated_compression_rejects_mutated_and_substituted_raw(self):
        for operation in ('metadata','replace'):
            helper = self.private_helpers()
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw=root/'raw';data=b'x'*1024;raw.write_bytes(data)
                original_read=os.read;changed=[]
                def mutate_after_read(fd,size):
                    chunk=original_read(fd,size)
                    if not changed:
                        changed.append(True)
                        if operation=='metadata':
                            before=raw.stat();os.utime(raw,ns=(before.st_atime_ns,before.st_mtime_ns+1000000000))
                        else:
                            raw.rename(root/'original');raw.write_bytes(data)
                    return chunk
                with mock.patch.object(os,'read',side_effect=mutate_after_read):
                    with self.subTest(operation=operation), self.assertRaisesRegex(AssertionError,'identity changed'):
                        helper['compress_private'](raw,root/'gzip',r.pin(data))

    @unittest.skipUnless(shutil.which('mke2fs') and shutil.which('debugfs'), 'ext4 tools unavailable')
    def test_private_descriptor_complete_empty_filesystem_readback(self):
        helper=self.private_helpers()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);image=root/'empty.ext4';work=root/'scratch';work.mkdir()
            with image.open('xb') as stream:stream.truncate(8*1024**2)
            subprocess.run(['mke2fs','-q','-F','-t','ext4','-b','4096','-E','root_owner=0:0',str(image)],
                           check=True,capture_output=True)
            fd,before=helper['open_private'](image,8*1024**2)
            try:
                root_time=struct.unpack('<I',os.pread(fd,4,1024+264))[0]
                members={'.':{'type':'dir','mode':0o755,'uid':0,'gid':0,'mtime_ns':0,'xattrs':{}}}
                proof=v.verify(fd,members,work,root_time=root_time)
                # Expected sibling scratch creation must not invalidate the leaf.
                helper['check_private'](fd,image,before)
                self.assertEqual(proof['namespace_members'],1)
                before_mode=image.stat().st_mode;image.chmod(before_mode ^ stat.S_IXUSR)
                with self.assertRaisesRegex(AssertionError,'identity changed'):
                    helper['check_private'](fd,image,before)
            finally:os.close(fd)

    def test_payload_syntax_and_full_inspection_hooks(self):
        program = n.payload_program(r,'tiny',POLICY).decode(); ast.parse(program)
        for token in ("'ro' in mounts.get(path,set())",'snapshot(seed)==before',
                      "inspection.sparse_capture=inspect_capture",'reader.verify(',
                      "boot_command_profile='spi-nvme-v1'",'proof=inspection.verify(output)',
                      "gzip.GzipFile(filename=''",'compresslevel=9,mtime=0',
                      "gzip_expansion_independent_verification':'pending'"):
            self.assertIn(token, program)
        self.assertIn("work=pathlib.Path('/var/tmp')",program)
        self.assertNotIn('/dev/nvme',program)
        self.assertNotIn('rootfs.tar.gz',program)
        self.assertEqual(POLICY['nvme_boot_command_profile']['payload_context'],
                         {'devtype':'nvme','devnum':0,'partition':1,'prefix':'/boot/'})


if __name__ == '__main__':
    unittest.main()

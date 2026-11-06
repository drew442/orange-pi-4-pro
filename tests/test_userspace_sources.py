"""Source acquisition checks use synthetic responses; never contact Debian."""
import copy
import fcntl
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('fetch_userspace_sources',
                                             REPO / 'build/fetch-userspace-sources.py')
FETCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FETCH)


class Response(io.BytesIO):
    status = 200

    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {} if length is None else {'Content-Length': str(length)}


class Opener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def open(self, url, timeout):
        self.calls.append((url, timeout))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


class SourceFetchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.data = b'archive fixture'
        self.record = {'bytes': len(self.data), 'sha256': hashlib.sha256(self.data).hexdigest()}
        self.path = 'pool/main/a/acl/acl_1.orig.tar.xz'

    def test_real_lock_and_binary_proof(self):
        lock, records = FETCH.inputs(REPO)
        self.assertEqual(len(lock['sources']), 149)
        self.assertEqual(len(records), 479)
        self.assertEqual(sum(r['bytes'] for r in records.values()), 570785529)

    def test_malformed_lock_paths_hashes_and_coverage(self):
        lock = FETCH.read_json(REPO / FETCH.LOCK)
        source_map = FETCH.read_json(REPO / FETCH.MAP)
        alterations = [
            lambda d: d['origins'].append('https://attacker.invalid/'),
            lambda d: d['sources'][0].update(directory='pool/main/../acl'),
            lambda d: d['sources'][0].update(directory='/pool/main/a/acl'),
            lambda d: d['sources'][0]['archives'][0].update(filename='../file'),
            lambda d: d['sources'][0]['archives'][0].update(filename='file%2fother'),
            lambda d: d['sources'][0]['archives'][0].update(sha256='0' * 63),
            lambda d: d['sources'][0]['archives'][0].update(bytes=True),
            lambda d: d['sources'][0]['archives'].append(d['sources'][0]['archives'][0]),
            lambda d: d['sources'][0]['binary_packages'].append('unmapped'),
            lambda d: d.update(unexpected=1),
        ]
        for alter in alterations:
            with self.subTest(alter=alter):
                candidate = copy.deepcopy(lock)
                alter(candidate)
                with self.assertRaises(ValueError):
                    FETCH.validate_lock(candidate, source_map)

    def test_duplicate_json_keys_rejected(self):
        path = self.output / 'bad.json'
        path.write_text('{"schema": 1, "schema": 2}')
        with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
            FETCH.read_json(path)

    def test_download_and_verify(self):
        opener = Opener([Response(self.data, len(self.data))])
        result = FETCH.download(self.path, self.record, self.output, opener)
        self.assertEqual(result['status'], 'verified')
        FETCH.verify(self.output / self.path, self.record)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual((self.output / self.path).read_bytes(), self.data)
        self.assertFalse(list(self.output.rglob('.partial-*')))

    def test_truncated_oversized_wrong_hash_and_content_length(self):
        for data, length in [(self.data[:-1], None), (self.data + b'X', None),
                             (b'X' * len(self.data), None), (self.data, 1000000)]:
            with self.subTest(data=data, length=length):
                opener = Opener([Response(data, length), Response(data, length)])
                result = FETCH.download(self.path, self.record, self.output, opener)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(len(result['attempt_errors']), 2)
                self.assertEqual(len(opener.calls), 2)
                self.assertFalse((self.output / self.path).exists())
                self.assertFalse(list(self.output.rglob('.partial-*')))

    def test_fallback_once_and_bounded_reads(self):
        class BoundedResponse(Response):
            def read(self, count=-1):
                self.assert_bound(count)
                return super().read(count)

            @staticmethod
            def assert_bound(count):
                if not 0 < count <= 1024 * 1024:
                    raise AssertionError('unbounded read')
        opener = Opener([OSError('unavailable'), BoundedResponse(self.data)])
        result = FETCH.download(self.path, self.record, self.output, opener)
        self.assertEqual(result['origin'], FETCH.ORIGINS[1])
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(len(result['attempt_errors']), 1)

    def test_existing_corrupt_destination_never_overwritten(self):
        destination = self.output / self.path
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b'bad')
        opener = Opener([])
        with self.assertRaises(ValueError):
            FETCH.download(self.path, self.record, self.output, opener)
        with self.assertRaises(ValueError):
            FETCH.verify(destination, self.record)
        self.assertEqual(destination.read_bytes(), b'bad')
        self.assertEqual(opener.calls, [])

    def test_symlink_directory_and_file_rejected(self):
        (self.output / 'pool').symlink_to(self.output, target_is_directory=True)
        with self.assertRaises(ValueError):
            FETCH.download(self.path, self.record, self.output, Opener([]))
        link = self.output / 'link'
        link.symlink_to(__file__)
        with self.assertRaises(ValueError):
            FETCH.verify(link, self.record)

    def test_redirect_restrictions(self):
        handler = FETCH.DebianRedirect()
        request = urllib.request.Request(FETCH.ORIGINS[0] + self.path)
        for url in ['http://deb.debian.org/file', 'https://attacker.invalid/file',
                    'https://deb.debian.org:444/file', 'https://user@deb.debian.org/file']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, 'Found', {}, url)
        redirected = handler.redirect_request(request, None, 302, 'Found', {},
                                               FETCH.ORIGINS[1] + self.path)
        self.assertTrue(redirected.full_url.startswith(FETCH.ORIGINS[1]))

    def test_resume_binding_mismatch_refuses_network(self):
        output = self.output / 'out/userspace-sources-test'
        output.mkdir(parents=True)
        FETCH.write_json(output / 'fetch-state.json', {'head': 'old'})
        with patch.object(FETCH, 'binding', return_value={'head': 'new'}), \
                patch.object(FETCH, 'inputs', return_value=({}, {self.path: self.record})), \
                patch.object(FETCH.urllib.request, 'build_opener') as opener:
            with self.assertRaisesRegex(ValueError, 'resume binding mismatch'):
                FETCH.fetch(self.output, output, True)
            opener.assert_not_called()

    def test_complete_fetch_resume_rehash_and_corruption_invalidates_receipt(self):
        output = self.output / 'out/userspace-sources-test'
        binding = {'head': 'fixture'}
        opener = Opener([Response(self.data)])
        with patch.object(FETCH, 'binding', return_value=binding), \
                patch.object(FETCH, 'inputs', return_value=({'sources': ['fixture'],
                                                           'authenticated_metadata': {}},
                                                          {self.path: self.record})), \
                patch.object(FETCH.urllib.request, 'build_opener', return_value=opener) as factory:
            first = FETCH.fetch(self.output, output)
            self.assertEqual(first['status'], 'complete')
            self.assertTrue((output / 'receipt.json').exists())
            second = FETCH.fetch(self.output, output, True)
            self.assertTrue(second['archives'][0]['revalidated_cache'])
            self.assertEqual(second['archives'][0]['origin'], FETCH.ORIGINS[0])
            self.assertEqual(factory.call_count, 1)
            (output / self.path).write_bytes(b'X' * len(self.data))
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                FETCH.fetch(self.output, output, True)
            self.assertFalse((output / 'receipt.json').exists())
            self.assertEqual(FETCH.read_json(output / 'fetch-report.json')['status'], 'failed')
            self.assertEqual(factory.call_count, 1)

    def test_failed_fetch_resumes_only_missing_files(self):
        output = self.output / 'out/userspace-sources-test'
        binding = {'head': 'fixture'}
        failed = Opener([OSError('live unavailable'), OSError('snapshot unavailable')])
        good = Opener([Response(self.data)])
        with patch.object(FETCH, 'binding', return_value=binding), \
                patch.object(FETCH, 'inputs', return_value=({'sources': ['fixture'],
                                                           'authenticated_metadata': {}},
                                                          {self.path: self.record})), \
                patch.object(FETCH.urllib.request, 'build_opener', side_effect=[failed, good]):
            with self.assertRaisesRegex(ValueError, 'downloads failed'):
                FETCH.fetch(self.output, output)
            self.assertFalse((output / 'receipt.json').exists())
            report = FETCH.read_json(output / 'fetch-report.json')
            self.assertEqual(report['archives'][0]['status'], 'failed')
            self.assertEqual(len(report['archives'][0]['attempt_errors']), 2)
            result = FETCH.fetch(self.output, output, True)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(len(good.calls), 1)

    def test_concurrent_writer_refused(self):
        output = self.output / 'out/userspace-sources-test'
        output.mkdir(parents=True)
        with (output / '.fetch.lock').open('w') as guard:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(FETCH, 'binding', return_value={'head': 'fixture'}), \
                    patch.object(FETCH, 'inputs', return_value=({}, {self.path: self.record})), \
                    patch.object(FETCH.urllib.request, 'build_opener') as opener:
                with self.assertRaises(BlockingIOError):
                    FETCH.fetch(self.output, output, True)
                opener.assert_not_called()

    def test_dirty_checkout_rejected(self):
        with patch.object(FETCH, 'git', side_effect=[str(self.output).encode(), b' M tracked-file']):
            with self.assertRaisesRegex(ValueError, 'clean and committed'):
                FETCH.binding(self.output)

    def test_git_ignores_ambient_repository_overrides_and_path(self):
        original = self.output / 'original'
        foreign = self.output / 'foreign'
        original.mkdir()
        foreign.mkdir()
        FETCH.git(original, 'init', '--quiet')
        FETCH.git(foreign, 'init', '--quiet')
        with patch.dict(os.environ, {'GIT_DIR': str(foreign / '.git'),
                                     'GIT_WORK_TREE': str(foreign),
                                     'GIT_INDEX_FILE': str(foreign / 'index'),
                                     'PATH': '/nonexistent'}):
            root = FETCH.git(original, 'rev-parse', '--show-toplevel').decode().strip()
            self.assertEqual(Path(root), original)

    def test_binding_rejects_another_repository_root(self):
        with patch.object(FETCH, 'git', return_value=b'/different/root'):
            with self.assertRaisesRegex(ValueError, 'repository root mismatch'):
                FETCH.binding(self.output)

    def test_bounded_resume_json_refuses_fifo_symlink_and_oversize(self):
        fifo = self.output / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            FETCH.read_json(fifo, max_bytes=100)
        target = self.output / 'target'
        target.write_text('{}')
        symlink = self.output / 'symlink'
        symlink.symlink_to(target)
        with self.assertRaises(OSError):
            FETCH.read_json(symlink, max_bytes=100)
        target.write_text(' ' * 101)
        with self.assertRaises(ValueError):
            FETCH.read_json(target, max_bytes=100)

    def test_fetch_lock_refuses_fifo(self):
        output = self.output / 'out/userspace-sources-test'
        output.mkdir(parents=True)
        os.mkfifo(output / '.fetch.lock')
        with patch.object(FETCH, 'binding', return_value={'head': 'fixture'}), \
                patch.object(FETCH, 'inputs', return_value=({}, {self.path: self.record})):
            with self.assertRaisesRegex(ValueError, 'unsafe fetch lock'):
                FETCH.fetch(self.output, output, True)

    def test_publication_syncs_parent_directories(self):
        with patch.object(FETCH, 'fsync_directory') as sync:
            FETCH.download(self.path, self.record, self.output, Opener([Response(self.data)]))
            sync.assert_called_once_with((self.output / self.path).parent)
        with patch.object(FETCH, 'fsync_directory') as sync:
            FETCH.write_json(self.output / 'report.json', {'status': 'fixture'})
            sync.assert_called_once_with(self.output)

    def test_metadata_change_cannot_publish_receipt(self):
        output = self.output / 'out/userspace-sources-test'
        opener = Opener([Response(self.data)])
        with patch.object(FETCH, 'binding', side_effect=[{'head': 'first'}, {'head': 'changed'}]), \
                patch.object(FETCH, 'inputs', return_value=({'sources': ['fixture']},
                                                          {self.path: self.record})), \
                patch.object(FETCH.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(ValueError, 'checkout changed'):
                FETCH.fetch(self.output, output)
            self.assertFalse((output / 'receipt.json').exists())
            FETCH.verify(output / self.path, self.record)

    def test_output_location_rejected(self):
        for output in [Path('relative'), self.output / 'elsewhere/userspace-sources-test',
                       self.output / 'out/other']:
            with self.subTest(output=output), self.assertRaises(ValueError):
                FETCH.fetch(self.output, output)

    def test_space_guard_before_network(self):
        output = self.output / 'out/userspace-sources-test'
        with patch.object(FETCH, 'binding', return_value={'head': 'fixture'}), \
                patch.object(FETCH, 'inputs', return_value=({}, {self.path: self.record})), \
                patch.object(FETCH.shutil, 'disk_usage') as usage, \
                patch.object(FETCH.urllib.request, 'build_opener') as opener:
            usage.return_value.free = 0
            with self.assertRaisesRegex(ValueError, 'insufficient free space'):
                FETCH.fetch(self.output, output)
            opener.assert_not_called()
            self.assertFalse((output / 'receipt.json').exists())


if __name__ == '__main__':
    unittest.main()

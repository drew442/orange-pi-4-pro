import importlib.util
import io
import json
from pathlib import Path
import unittest
import subprocess
import sys
import tempfile

SPEC = importlib.util.spec_from_file_location('agent_usage', Path(__file__).resolve().parents[1] / 'scripts/summarize-agent-usage.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def event(identity='a', **kwargs):
    return dict(type='turn.completed', turn_id=identity, usage=dict(input_tokens=10, cached_input_tokens=4, output_tokens=3, reasoning_output_tokens=2), **kwargs)


def run(*events, **kwargs):
    return MODULE.summarize([io.StringIO('\n'.join(json.dumps(e) for e in events))], **kwargs)


class UsageTests(unittest.TestCase):
    def test_full_aggregation(self):
        result = run(event(), event('b'))
        self.assertEqual(result['turns'], 2)
        for key, expected in [('input_tokens', 20), ('cached_input_tokens', 8), ('output_tokens', 6), ('reasoning_output_tokens', 4)]:
            self.assertEqual(result['usage'][key]['total'], expected)
            self.assertTrue(result['usage'][key]['complete'])

    def test_missing_reasoning_is_unknown(self):
        item = event('b')
        del item['usage']['reasoning_output_tokens']
        result = run(event(), item)
        self.assertEqual(result['usage']['reasoning_output_tokens'], dict(total=None, known_subtotal=2, reported_turns=1, complete=False))

    def test_duplicate_identifiable_turn(self):
        self.assertEqual(run(event(), event())['turns'], 1)
        item = event()
        item['turn'] = {'id': item.pop('turn_id')}
        self.assertEqual(run(event(), item)['diagnostics']['duplicate_turns'], 1)

    def test_turn_ids_scoped_to_threads(self):
        result = run(dict(type='thread.started', thread_id='private-thread-a'), event('1'),
                     dict(type='thread.started', thread_id='private-thread-b'), event('1'))
        self.assertEqual(result['turns'], 2)
        self.assertNotIn('private-thread', json.dumps(result))

    def test_same_thread_deduplicated_across_files(self):
        content = '\n'.join(json.dumps(e) for e in [dict(type='thread.started', thread_id='private-thread'), event('1')])
        result = MODULE.summarize([io.StringIO(content), io.StringIO(content)])
        self.assertEqual(result['turns'], 1)
        self.assertEqual(result['diagnostics']['duplicate_turns'], 1)
        self.assertNotIn('private-thread', json.dumps(result))

    def test_no_thread_ids_are_scoped_to_input_stream(self):
        content = json.dumps(event('1'))
        result = MODULE.summarize([io.StringIO(content), io.StringIO(content)])
        self.assertEqual(result['turns'], 2)

    def test_explicit_event_thread_overrides_context(self):
        result = run(dict(type='thread.started', thread_id='a'), event('1'),
                     event('1', thread_id='b'), event('1'))
        self.assertEqual(result['turns'], 2)
        self.assertEqual(result['diagnostics']['duplicate_turns'], 1)

    def test_malformed_and_invalid_fields(self):
        item = event()
        item['usage'].update(input_tokens=-1, cached_input_tokens=True, output_tokens='3')
        result = MODULE.summarize([io.StringIO('bad\n[]\n' + json.dumps(item))])
        self.assertEqual(result['diagnostics']['malformed_lines'], 2)
        self.assertEqual(result['diagnostics']['invalid_counter_fields'], 3)
        self.assertIsNone(result['usage']['input_tokens']['total'])
        self.assertFalse(result['input_complete'])
        self.assertIsNone(result['usage']['reasoning_output_tokens']['total'])

    def test_cumulative_and_tool_events_ignored(self):
        result = run(event(), dict(type='token_count', usage=event()['usage'], total_token_usage={'input_tokens': 999}), dict(type='item.completed', text='private prompt'))
        self.assertEqual(result['usage']['input_tokens']['total'], 10)
        self.assertNotIn('private', json.dumps(result))

    def test_metadata_provenance(self):
        result = run(dict(type='thread.started', model='gpt-6.1-sol', model_reasoning_effort='low', session_id='secret'), event(), model='gpt-6-luna', effort='high')
        self.assertEqual(result['metadata']['explicit_event_values']['model'], {'gpt-6.1-sol': 1})
        self.assertEqual(result['metadata']['supplied_unverified_labels'], {'model': 'gpt-6-luna', 'effort': 'high'})
        self.assertNotIn('secret', json.dumps(result))

    def test_cli_files_output_and_private_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second, output = (root / name for name in ('a.jsonl', 'b.jsonl', 'summary.json'))
            first.write_text(json.dumps(event()) + '\n')
            second.write_text(json.dumps(event('b', prompt='private@example.test', session_id='secret')) + '\n')
            result = subprocess.run([sys.executable, str(Path(MODULE.__file__)), str(first), '--input', str(second), '--output', str(output), '--model', 'gpt-6-luna'], capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, '')
            summary = output.read_text()
            self.assertEqual(json.loads(summary)['turns'], 2)
            self.assertNotIn('private', summary)
            self.assertNotIn('secret', summary)

    def test_empty_has_no_known_totals(self):
        self.assertIsNone(run()['usage']['input_tokens']['total'])


if __name__ == '__main__':
    unittest.main()

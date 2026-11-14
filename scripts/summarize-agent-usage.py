#!/usr/bin/env python3
"""Summarize documented per-turn Codex exec JSONL usage; never echo event bodies.

Totals cover unique turn.completed events only. Missing/invalid counters make the
corresponding total unknown; known_subtotal remains useful but is not a total.
This is a local run summary, not account usage, billing or active model time.
Turn IDs are scoped by explicit thread identity; without it they are scoped to
the input stream. Cross-file deduplication therefore requires thread identity.
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')
LABEL = re.compile(r'[A-Za-z0-9_.:/+-]{1,100}\Z')


def summarize(streams, model=None, effort=None):
    known = Counter()
    reported = Counter()
    diagnostics = Counter()
    actual = {'model': Counter(), 'effort': Counter()}
    seen = set()
    turns = 0
    for stream_index, stream in enumerate(streams):
        thread_context = None
        for line in stream:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                diagnostics['malformed_lines'] += 1
                continue
            if not isinstance(event, dict):
                diagnostics['malformed_lines'] += 1
                continue
            explicit_thread = event.get('thread_id')
            if isinstance(explicit_thread, str) and explicit_thread:
                event_thread = ('thread', explicit_thread)
                if event.get('type') == 'thread.started':
                    thread_context = event_thread
            else:
                event_thread = thread_context or ('stream', stream_index)
            # Only explicit event metadata, never role names or assistant text.
            for source, target in (('model', 'model'), ('model_reasoning_effort', 'effort')):
                value = event.get(source)
                if isinstance(value, str) and LABEL.fullmatch(value):
                    actual[target][value] += 1
            if event.get('type') != 'turn.completed':
                continue
            identity = event.get('turn_id')
            if identity is None and isinstance(event.get('turn'), dict):
                identity = event['turn'].get('id')
            if isinstance(identity, (str, int)) and not isinstance(identity, bool):
                scoped_identity = (event_thread, identity)
                if scoped_identity in seen:
                    diagnostics['duplicate_turns'] += 1
                    continue
                seen.add(scoped_identity)
            else:
                diagnostics['turns_without_id'] += 1
            turns += 1
            usage = event.get('usage')
            if not isinstance(usage, dict):
                usage = {}
                diagnostics['missing_or_invalid_usage'] += 1
            for field in FIELDS:
                value = usage.get(field)
                if type(value) is int and value >= 0:
                    known[field] += value
                    reported[field] += 1
                elif field in usage:
                    diagnostics['invalid_counter_fields'] += 1
                else:
                    diagnostics['missing_counter_fields'] += 1
    supplied = {}
    for key, value in (('model', model), ('effort', effort)):
        if value is not None:
            supplied[key] = value
    input_complete = not diagnostics['malformed_lines']
    return {
        'schema_version': 1,
        'scope': 'unique_observed_turn.completed_events',
        'turns': turns,
        'input_complete': input_complete,
        'usage': {field: {'total': known[field] if input_complete and turns and reported[field] == turns else None,
                          'known_subtotal': known[field], 'reported_turns': reported[field],
                          'complete': input_complete and bool(turns) and reported[field] == turns} for field in FIELDS},
        'diagnostics': dict(sorted(diagnostics.items())),
        'metadata': {'explicit_event_values': {key: dict(sorted(values.items())) for key, values in actual.items()},
                     'supplied_unverified_labels': supplied},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='*', help='JSONL input files (stdin if none)')
    parser.add_argument('--input', action='append', default=[], help='Additional JSONL input file; repeatable')
    parser.add_argument('--output', help='Write compact JSON to this file instead of stdout')
    parser.add_argument('--model', help='Supplied, unverified run label')
    parser.add_argument('--effort', help='Supplied, unverified run label')
    args = parser.parse_args()
    for value in (args.model, args.effort):
        if value is not None and not LABEL.fullmatch(value):
            parser.error('labels must contain 1-100 model/effort identifier characters')
    streams = []
    try:
        for filename in args.files + args.input:
            streams.append(open(filename, encoding='utf-8'))
        result = summarize(streams or [sys.stdin], args.model, args.effort)
        output = json.dumps(result, separators=(',', ':'), sort_keys=True) + '\n'
        if args.output:
            Path(args.output).write_text(output, encoding='utf-8')
        else:
            sys.stdout.write(output)
    except (OSError, UnicodeError):
        # Paths and raw input can contain secrets; keep errors deliberately terse.
        parser.exit(2, 'unable to read input or write summary\n')
    finally:
        for stream in streams:
            stream.close()


if __name__ == '__main__':
    main()

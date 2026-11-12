"""Pure QMP protocol regressions; no physical KVM or clean-disk claim."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'guest_qmp', Path(__file__).resolve().parents[1] / 'configs/guest_qmp.py')
QMP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QMP)
RUN = '655f5a94-fb19-4198-863d-4162f99fc660'


def greeting():
    # Exact observed QEMU version, not an assertion of executable authenticity.
    return {'QMP': {'version': {'qemu': {'major': 7, 'minor': 2, 'micro': 22},
                                'package': 'Debian 1:7.2+dfsg-7+deb12u18'},
                    'capabilities': ['oob']}}


def shutdown(guest=True, reason='guest-shutdown'):
    return {'event': 'SHUTDOWN', 'data': {'guest': guest, 'reason': reason},
            'timestamp': {'seconds': 123, 'microseconds': 10}}


def ack(session, command):
    session.receive({'return': {}, 'id': command['id']})


def running():
    session = QMP.Session(RUN)
    session.receive(greeting())
    ack(session, session.command('qmp_capabilities'))
    ack(session, session.command('cont'))
    return session


class DecoderTests(unittest.TestCase):
    def test_every_single_byte_fragment_preserves_utf8_and_crlf(self):
        expected = [greeting(), {'event': 'CUSTOM', 'data': {'unicode': 'é中'}}]
        data = b''.join(json.dumps(m, ensure_ascii=False).encode() + b'\r\n' for m in expected)
        decoder = QMP.Decoder()
        actual = []
        for byte in data:
            actual.extend(decoder.feed(bytes([byte])))
        self.assertEqual(actual, expected)
        decoder.finish()
        with self.assertRaises(QMP.QMPError):
            decoder.feed(b'{}\n')

    def test_bad_json_terminal_failure(self):
        cases = [b'\n', b' \r\n', b'[]\n', b'null\n', b'{}\xff\n',
                 b'{"x":1,"x":2}\n', b'{"x":{"a":1,"a":2}}\n',
                 b'{"n":NaN}\n', b'{"n":Infinity}\n', b'{"n":1e999}\n',
                 b'{"n":' + b'[' * 33 + b'0' + b']' * 33 + b'}\n']
        for data in cases:
            with self.subTest(data=data):
                decoder = QMP.Decoder()
                with self.assertRaises(QMP.QMPError):
                    decoder.feed(data)
                with self.assertRaises(QMP.QMPError):
                    decoder.feed(b'{}\n')

    def test_truncated_tail_and_size_boundaries(self):
        decoder = QMP.Decoder()
        decoder.feed(b'{"text":"\xc3')
        with self.assertRaises(QMP.QMPError):
            decoder.finish()
        line = b'{"x":"' + b'a' * (QMP.MAX_MESSAGE_BYTES - 8) + b'"}'
        self.assertEqual(len(line), QMP.MAX_MESSAGE_BYTES)
        self.assertEqual(len(QMP.Decoder().feed(line + b'\r\n')), 1)
        with self.assertRaises(QMP.QMPError):
            QMP.Decoder().feed(line[:-1] + b' }\n')
        with self.assertRaises(QMP.QMPError):
            QMP.Decoder().feed(b'a' * (QMP.MAX_MESSAGE_BYTES + 1))

    def test_count_and_total_limits(self):
        decoder = QMP.Decoder()
        self.assertEqual(len(decoder.feed(b'{}\n' * QMP.MAX_MESSAGES)), QMP.MAX_MESSAGES)
        with self.assertRaises(QMP.QMPError):
            decoder.feed(b'{}\n')
        decoder = QMP.Decoder()
        line = b'{"x":"' + b'a' * 65500 + b'"}\n'
        with self.assertRaises(QMP.QMPError):
            for _ in range(130):
                decoder.feed(line)


class SessionTests(unittest.TestCase):
    def test_command_ids_and_clean_event_before_stop_ack(self):
        session = running()
        command = session.command('system_powerdown')
        self.assertEqual(command, {'execute': 'system_powerdown', 'id': 'system_powerdown:' + RUN})
        session.receive({'event': 'POWERDOWN'})
        self.assertFalse(session.clean_shutdown_observed)
        event = shutdown()
        session.receive(event)
        self.assertTrue(session.clean_shutdown_observed)
        ack(session, command)
        self.assertTrue(session.clean_shutdown_observed)
        event['data']['guest'] = False
        self.assertIs(session.shutdown_message['data']['guest'], True)
        transcript = session.transcript
        transcript[-1]['message']['return']['mutated'] = True
        self.assertEqual(session.transcript[-1]['message']['return'], {})

    def test_self_shutdown_before_cont_ack(self):
        session = QMP.Session(RUN)
        session.receive(greeting())
        ack(session, session.command('qmp_capabilities'))
        cont = session.command('cont')
        session.receive(shutdown())
        self.assertTrue(session.clean_shutdown_observed)
        ack(session, cont)
        with self.assertRaises(QMP.QMPError):
            session.command('system_powerdown')
        self.assertFalse(session.clean_shutdown_observed)

    def test_async_event_does_not_acknowledge_command(self):
        session = QMP.Session(RUN)
        session.receive(greeting())
        session.command('qmp_capabilities')
        session.receive({'event': 'CUSTOM', 'data': {'retained': 1}})
        with self.assertRaises(QMP.QMPError):
            session.command('cont')

    def test_unclean_early_and_duplicate_shutdown_disqualify(self):
        for guest, reason in [(False, 'host-qmp-quit'), (True, 'guest-reset'),
                              (1, 'guest-shutdown'), ('true', 'guest-shutdown'),
                              (True, None)]:
            session = running()
            session.receive(shutdown(guest, reason))
            self.assertFalse(session.clean_shutdown_observed)
        session = QMP.Session(RUN)
        session.receive(greeting())
        session.receive(shutdown())
        self.assertFalse(session.clean_shutdown_observed)
        with self.assertRaises(QMP.QMPError):
            session.command('qmp_capabilities')
        session = running()
        session.receive(shutdown())
        session.receive(shutdown())
        self.assertFalse(session.clean_shutdown_observed)

    def test_reset_and_panic_before_or_after_shutdown(self):
        for name in ['RESET', 'GUEST_PANICKED']:
            for first in [True, False]:
                session = running()
                events = [{'event': name}, shutdown()]
                for event in events if first else reversed(events):
                    session.receive(event)
                self.assertFalse(session.clean_shutdown_observed)
        session = QMP.Session(RUN)
        session.receive(greeting())
        session.receive({'event': 'RESET'})
        ack(session, session.command('qmp_capabilities'))
        ack(session, session.command('cont'))
        session.receive(shutdown())
        self.assertTrue(session.clean_shutdown_observed)

    def test_commands_are_single_use_and_strictly_ordered(self):
        for name in ['cont', 'system_powerdown', 'quit', 'qmp_capabilities']:
            session = QMP.Session(RUN)
            if name != 'qmp_capabilities':
                session.receive(greeting())
            with self.assertRaises(QMP.QMPError):
                session.command(name)
        for name in ['qmp_capabilities', 'cont', 'system_powerdown']:
            session = running()
            if name == 'system_powerdown':
                ack(session, session.command(name))
            with self.assertRaises(QMP.QMPError):
                session.command(name)

    def test_bad_greetings_and_run_ids(self):
        for value in ['', '0' * 32, RUN.upper(), '{' + RUN + '}', 123]:
            with self.assertRaises(QMP.QMPError):
                QMP.Session(value)
        for message in [{}, {'event': 'RESET'}, {'return': {}, 'id': 'x'}]:
            with self.assertRaises(QMP.QMPError):
                QMP.Session(RUN).receive(message)
        for field in ['major', 'minor', 'micro']:
            message = greeting()
            message['QMP']['version']['qemu'][field] += 1
            with self.assertRaises(QMP.QMPError):
                QMP.Session(RUN).receive(message)
        message = greeting()
        message['QMP']['version']['package'] = 'a' * 1025
        with self.assertRaises(QMP.QMPError):
            QMP.Session(RUN).receive(message)
        with self.assertRaises(QMP.QMPError):
            running().receive(greeting())

    def test_wrong_duplicate_error_unknown_and_mixed_responses(self):
        for response in [{'return': {}, 'id': 'other'}, {'return': {}},
                         {'error': {'class': 'GenericError'}, 'id': 'qmp_capabilities:' + RUN},
                         {'return': {}, 'id': 'qmp_capabilities:' + RUN, 'event': 'SHUTDOWN'},
                         {'unknown': 1}]:
            session = QMP.Session(RUN)
            session.receive(greeting())
            session.command('qmp_capabilities')
            with self.assertRaises(QMP.QMPError):
                session.receive(response)
        session = running()
        with self.assertRaises(QMP.QMPError):
            session.receive({'return': {}, 'id': 'cont:' + RUN})

    def test_direct_aggregate_budget_precedes_serialization(self):
        oversized = [
            {'event': 'X', 'data': {'values': ['x' * 60000] * 128}},
            {'event': 'X', 'data': {'values': ['\x00' * 6000] * 2}},
            {'event': 'X', 'data': {'values': ['😀' * 10000] * 2}},
            {'event': 'X', 'data': {'number': 1 << 1000000}},
        ]
        for message in oversized:
            session = running()
            with self.subTest(message_type=type(message['data']).__name__), \
                    patch.object(QMP.json, 'dumps', side_effect=AssertionError('serialized oversized input')):
                with self.assertRaises(QMP.QMPError):
                    session.receive(message)
            self.assertFalse(session.clean_shutdown_observed)
            with self.assertRaises(QMP.QMPError):
                session.receive({'event': 'SHUTDOWN', 'data': {'guest': True, 'reason': 'guest-shutdown'}})

    def test_exact_serialized_utf8_and_escape_budget(self):
        message = {'event': 'X', 'data': {'values': ['é', '😀', '\x00', '\n', '"', '\\']}}
        size = len(json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        session = running()
        with patch.object(QMP, 'MAX_MESSAGE_BYTES', size):
            session.receive(message)
        session = running()
        with patch.object(QMP, 'MAX_MESSAGE_BYTES', size - 1):
            with self.assertRaises(QMP.QMPError):
                session.receive(message)

    def test_direct_input_bounds_and_invalid_values(self):
        for event in [{'event': 'X', 'data': {'n': float('nan')}},
                      {'event': 'X', 'data': {'value': object()}},
                      {'event': 'X', 'data': {'text': '\ud800'}},
                      {'event': 'X', 'data': {'text': 'x' * 65536}},
                      {'event': 'X', 'timestamp': {'seconds': True, 'microseconds': 0}}]:
            with self.assertRaises(QMP.QMPError):
                running().receive(event)
        nested = {}
        for _ in range(40):
            nested = {'next': nested}
        with self.assertRaises(QMP.QMPError):
            running().receive({'event': 'X', 'data': nested})
        for bound, value in [('MAX_MESSAGES', 6), ('MAX_TOTAL_BYTES', 600)]:
            with patch.object(QMP, bound, value):
                session = running()
                with self.assertRaises(QMP.QMPError):
                    for _ in range(40):
                        session.receive({'event': 'CUSTOM', 'data': {'text': 'a' * 100}})
                self.assertFalse(session.clean_shutdown_observed)


if __name__ == '__main__':
    unittest.main()

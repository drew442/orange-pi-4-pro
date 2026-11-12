"""Pure bounded QMP observation for the pinned QEMU 7.2.22 profile.

No socket, process, clock or storage operations occur here. A clean event is only
one prerequisite: checkpoint authority also needs authenticated process/storage
identity, normal raw child exit and stopped-disk inspection outside this module.
Source: QEMU qapi/run-state.json, qapi/control.json, softmmu/runstate.c and
accel/kvm/kvm-all.c; pinned source evidence is recorded in the design evidence.
"""
import copy
import json
import math
import uuid

MAX_MESSAGE_BYTES = 65536
MAX_MESSAGES = 4096
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_DEPTH = 32
PROFILE = 'qmp-guest-shutdown-v1'
QEMU_VERSION = (7, 2, 22)


class QMPError(ValueError):
    """Malformed, unsupported or exhausted QMP observation; quarantine the run."""


def _require(condition, message):
    if not condition:
        raise QMPError(message)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def _constant(value):
    raise QMPError('nonfinite JSON number')


def _depth(text):
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            _require(depth <= MAX_DEPTH, 'JSON nesting limit exceeded')
        elif char in ']}':
            depth -= 1


def _consume(budget, count):
    budget[0] -= count
    _require(budget[0] >= 0, 'QMP message byte limit exceeded')


def _string(value, budget):
    # Count exactly json.dumps(ensure_ascii=False) UTF-8 bytes, including quotes,
    # without materializing escaped strings or oversized encoded copies.
    _consume(budget, 2)
    for char in value:
        point = ord(char)
        _require(not 0xd800 <= point <= 0xdfff, 'invalid Unicode surrogate')
        if char in ('"', '\\', '\b', '\f', '\n', '\r', '\t'):
            size = 2
        elif point < 32:
            size = 6
        else:
            size = 1 if point < 128 else 2 if point < 2048 else 3 if point < 65536 else 4
        _consume(budget, size)


def _tree(value, depth=0, budget=None):
    """Bound aggregate serialized bytes before any JSON allocation occurs."""
    if budget is None:
        budget = [MAX_MESSAGE_BYTES]
    _require(depth <= MAX_DEPTH, 'JSON nesting limit exceeded')
    if type(value) in (dict, list):
        _require(depth < MAX_DEPTH, 'JSON nesting limit exceeded')
        _consume(budget, 2 + max(0, len(value) - 1))
    if type(value) is dict:
        for key, child in value.items():
            _require(type(key) is str, 'JSON keys must be strings')
            _string(key, budget)
            _consume(budget, 1)
            _tree(child, depth + 1, budget)
    elif type(value) is list:
        for child in value:
            _tree(child, depth + 1, budget)
    elif type(value) is str:
        _string(value, budget)
    elif type(value) is bool:
        _consume(budget, 4 if value else 5)
    elif value is None:
        _consume(budget, 4)
    elif type(value) is int:
        # QMP numeric fields are small; also avoid a huge integer-to-text allocation.
        _require(value.bit_length() <= 4096, 'QMP JSON integer too large')
        _consume(budget, len(str(value)))
    elif type(value) is float:
        _require(math.isfinite(value), 'nonfinite JSON number')
        _consume(budget, len(repr(value)))
    else:
        raise QMPError('invalid JSON value')


def _parse(line):
    _require(0 < len(line) <= MAX_MESSAGE_BYTES, 'invalid QMP message size')
    try:
        text = line.decode('utf-8', errors='strict')
        _require(bool(text.strip()), 'empty QMP line')
        _depth(text)
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
        _tree(value)
        _require(type(value) is dict, 'QMP message must be an object')
        return value
    except (UnicodeError, RecursionError, ValueError) as exc:
        raise QMPError(str(exc)) from exc


class Decoder:
    """Streaming newline-delimited JSON; bytes wait intact for a complete line.

    Message size excludes LF and an optional preceding CR. Total includes all
    incoming bytes. Any failure is terminal, including an incomplete final line.
    """
    def __init__(self):
        self._buffer = bytearray()
        self._total = 0
        self._count = 0
        self._failed = False
        self._finished = False

    def feed(self, data):
        _require(not self._failed and not self._finished, 'decoder is closed')
        try:
            _require(type(data) is bytes, 'decoder requires bytes')
            self._total += len(data)
            _require(self._total <= MAX_TOTAL_BYTES, 'QMP total byte limit exceeded')
            result = []
            # Never retain an unbounded caller-supplied chunk in the line buffer.
            offset = 0
            while offset < len(data):
                newline = data.find(b'\n', offset)
                end = len(data) if newline == -1 else newline
                _require(len(self._buffer) + end - offset <= MAX_MESSAGE_BYTES + 1,
                         'QMP message byte limit exceeded')
                self._buffer.extend(data[offset:end])
                if newline == -1:
                    _require(len(self._buffer) <= MAX_MESSAGE_BYTES
                             or self._buffer.endswith(b'\r'),
                             'QMP message byte limit exceeded')
                    break
                line = bytes(self._buffer)
                self._buffer.clear()
                if line.endswith(b'\r'):
                    line = line[:-1]
                self._count += 1
                _require(self._count <= MAX_MESSAGES, 'QMP message count exceeded')
                result.append(_parse(line))
                offset = newline + 1
            return result
        except QMPError:
            self._failed = True
            raise

    def finish(self):
        _require(not self._failed and not self._finished, 'decoder is closed')
        self._finished = True
        if self._buffer:
            self._failed = True
            raise QMPError('incomplete final QMP line')


class Session:
    """Single-use command sequencing and shutdown evidence, without any I/O.

    command() records issuance before returning bytes to its caller: uncertain
    delivery must never be retried. receive() accepts async events while a command
    acknowledgement is pending, including SHUTDOWN before the cont/stop reply.
    All received messages and issued commands share transcript resource limits.
    """
    def __init__(self, run_id):
        try:
            parsed = uuid.UUID(run_id) if type(run_id) is str else None
        except ValueError as exc:
            raise QMPError('invalid run UUID') from exc
        _require(parsed is not None and parsed.int != 0 and str(parsed) == run_id,
                 'run UUID must be canonical and nonzero')
        self._run_id = run_id
        self._greeting = False
        self._capabilities = False
        self._sent = set()
        self._pending = None
        self._shutdown = None
        self._shutdown_count = 0
        self._disqualified = False
        self._failed = False
        self._transcript = []
        self._total = 0

    @property
    def clean_shutdown_observed(self):
        return (not self._failed and not self._disqualified
                and self._shutdown_count == 1 and self._shutdown is not None)

    @property
    def shutdown_message(self):
        return copy.deepcopy(self._shutdown)

    @property
    def transcript(self):
        return tuple(copy.deepcopy(self._transcript))

    def _record(self, direction, message):
        _require(type(message) is dict, 'QMP message must be an object')
        _tree(message)
        try:
            encoded = json.dumps(message, ensure_ascii=False, allow_nan=False,
                                 separators=(',', ':')).encode('utf-8')
        except (ValueError, UnicodeError) as exc:
            raise QMPError(str(exc)) from exc
        _require(len(encoded) <= MAX_MESSAGE_BYTES, 'QMP message byte limit exceeded')
        _require(len(self._transcript) < MAX_MESSAGES, 'QMP message count exceeded')
        _require(self._total + len(encoded) + 1 <= MAX_TOTAL_BYTES,
                 'QMP total byte limit exceeded')
        self._total += len(encoded) + 1
        self._transcript.append({'direction': direction, 'message': copy.deepcopy(message)})

    def command(self, name):
        _require(not self._failed, 'session failed')
        try:
            _require(type(name) is str and name in ('qmp_capabilities', 'cont', 'system_powerdown'),
                     'unsupported QMP command')
            _require(self._greeting and not self._shutdown_count, 'command not admitted')
            _require(self._pending is None, 'command acknowledgement pending')
            _require(name not in self._sent, 'QMP command cannot be retried')
            if name == 'qmp_capabilities':
                _require(not self._sent, 'capabilities must be first')
            else:
                _require(self._capabilities, 'capabilities not acknowledged')
                if name == 'system_powerdown':
                    _require('cont' in self._sent, 'guest execution not admitted')
            message = {'execute': name, 'id': name + ':' + self._run_id}
            self._record('send', message)
            self._sent.add(name)
            self._pending = message['id']
            return message
        except QMPError:
            self._failed = True
            raise

    def receive(self, message):
        _require(not self._failed, 'session failed')
        try:
            self._record('receive', message)
            if not self._greeting:
                self._receive_greeting(message)
                self._greeting = True
                return
            _require('QMP' not in message, 'duplicate greeting')
            if 'event' in message:
                _require(not ({'return', 'error', 'id'} & message.keys()), 'ambiguous QMP event')
                self._receive_event(message)
                return
            _require(set(message) in ({'return', 'id'}, {'error', 'id'}),
                     'unknown QMP response')
            _require(type(message['id']) is str and message['id'] == self._pending,
                     'unexpected command acknowledgement')
            _require('error' not in message, 'QMP command failed')
            _require(message['return'] == {} and type(message['return']) is dict,
                     'unexpected QMP command return')
            if self._pending == 'qmp_capabilities:' + self._run_id:
                self._capabilities = True
            self._pending = None
        except QMPError:
            self._failed = True
            raise

    def _receive_greeting(self, message):
        _require(set(message) == {'QMP'} and type(message['QMP']) is dict,
                 'QMP greeting required first')
        greeting = message['QMP']
        _require(set(greeting) == {'version', 'capabilities'}, 'invalid QMP greeting')
        version = greeting['version']
        _require(type(version) is dict and set(version) == {'qemu', 'package'},
                 'invalid QMP version')
        qemu = version['qemu']
        _require(type(qemu) is dict and set(qemu) == {'major', 'minor', 'micro'},
                 'invalid QEMU version fields')
        _require(all(type(qemu[key]) is int for key in ('major', 'minor', 'micro'))
                 and tuple(qemu[key] for key in ('major', 'minor', 'micro')) == QEMU_VERSION,
                 'unsupported QEMU version')
        _require(type(version['package']) is str and len(version['package']) <= 1024,
                 'invalid QEMU package')
        caps = greeting['capabilities']
        _require(type(caps) is list and all(type(cap) is str and len(cap) <= 128 for cap in caps),
                 'invalid QMP capabilities')

    def _receive_event(self, message):
        _require(set(message) <= {'event', 'data', 'timestamp'}, 'invalid QMP event fields')
        _require(type(message['event']) is str and 0 < len(message['event']) <= 128,
                 'invalid QMP event name')
        if 'data' in message:
            _require(type(message['data']) is dict, 'invalid QMP event data')
        if 'timestamp' in message:
            stamp = message['timestamp']
            _require(type(stamp) is dict and set(stamp) == {'seconds', 'microseconds'}
                     and all(type(item) is int and item >= 0 for item in stamp.values())
                     and stamp['microseconds'] < 1000000, 'invalid QMP timestamp')
        name = message['event']
        if name in ('RESET', 'GUEST_PANICKED') and 'cont' in self._sent:
            self._disqualified = True
        if name == 'SHUTDOWN':
            self._shutdown_count += 1
            if self._shutdown is None:
                self._shutdown = copy.deepcopy(message)
            data = message.get('data', {})
            if (self._shutdown_count != 1 or 'cont' not in self._sent
                    or data.get('guest') is not True or data.get('reason') != 'guest-shutdown'):
                self._disqualified = True

#!/usr/bin/python3
"""Bounded QMP transport for an already authenticated supervisor context.

The trusted root caller supplies a connected, nonblocking UNIX stream and a
ChildWitness. Both are borrowed: this module never closes or changes their mode,
opens paths, launches/signals/reaps children, or grants checkpoint authority.
The caller must retain exclusive socket I/O and bind the final QMP observation to
raw child exit, fixture and durable intent evidence. QEMU 7.2.22 semantics and
limits are inherited from configs/guest_qmp.py (qapi/control.json,
qapi/run-state.json, softmmu/runstate.c and accel/kvm/kvm-all.c).
"""
from collections import deque
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import select
import socket
import time

_SPEC = importlib.util.spec_from_file_location(
    'channel_guest_qmp', Path(__file__).resolve().parents[1] / 'configs/guest_qmp.py')
QMP = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(QMP)


class ChannelError(ValueError):
    """Terminal channel failure; the run must not resume QMP commands."""


class Channel:
    """Single run, no reconnect/replay; all public I/O failures poison the run.

    receive returns one validated message (None at EOF); pump waits for a
    SHUTDOWN observation, while finish must drain/validate every trailing frame
    through EOF after the caller obtains raw child wait evidence. Neither a
    command acknowledgement nor pump alone establishes clean_shutdown.
    """
    def __init__(self, connected_socket, child_witness, run_id):
        self._failed = False
        self._eof = False
        self._greeted = False
        self._finished = False
        self._socket = connected_socket
        self._witness = child_witness
        self._queue = deque()
        self._raw_total = 0
        self._message_count = 0
        self._decoder = QMP.Decoder()
        self._session = QMP.Session(run_id)
        try:
            if (not isinstance(connected_socket, socket.socket)
                    or connected_socket.family != socket.AF_UNIX
                    or connected_socket.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)
                    != socket.SOCK_STREAM or connected_socket.gettimeout() != 0.0
                    or not fcntl.fcntl(connected_socket, fcntl.F_GETFL) & os.O_NONBLOCK):
                raise ChannelError('connected nonblocking UNIX stream required')
            connected_socket.getpeername()
            # No reads, parsing or writes are allowed before the live peer check.
            child_witness.verify_qmp_peer(connected_socket)
        except Exception as exc:
            self._failed = True
            raise ChannelError(str(exc)) from exc

    @property
    def transcript(self):
        return self._session.transcript

    @property
    def clean_shutdown(self):
        return (not self._failed and self._finished and self._eof
                and self._session.clean_shutdown_observed)

    @property
    def shutdown_message(self):
        return self._session.shutdown_message

    def _deadline(self, timeout, maximum=60):
        if self._failed or self._finished:
            raise ChannelError('channel is closed')
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 < timeout <= maximum):
            raise ChannelError(f'timeout must be finite and within (0, {maximum}]')
        return time.monotonic() + timeout

    def _wait(self, deadline, writing=False):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ChannelError('QMP deadline exceeded')
        readable, writable, _ = select.select(
            [] if writing else [self._socket],
            [self._socket] if writing else [], [], remaining)
        if not (writable if writing else readable):
            raise ChannelError('QMP deadline exceeded')

    def _next(self, deadline):
        if time.monotonic() >= deadline:
            raise ChannelError('QMP deadline exceeded')
        while not self._queue and not self._eof:
            self._wait(deadline)
            try:
                data = self._socket.recv(4096)
            except BlockingIOError:
                continue
            if not data:
                self._decoder.finish()  # Reject truncated final frames.
                self._eof = True
            else:
                # Decoder counts raw bytes and lines, including pipelined input.
                self._budget(len(data), 0)
                messages = self._decoder.feed(data)
                self._budget(0, len(messages))
                self._queue.extend(messages)
        if not self._queue:
            return None
        message = self._queue.popleft()
        self._session.receive(message)
        return message

    def _budget(self, raw_bytes, messages):
        # Include issued commands as well as incoming wire bytes. Whitespace and
        # pipelined frames cannot evade the pure Session's canonical limits.
        self._raw_total += raw_bytes
        self._message_count += messages
        if self._raw_total > QMP.MAX_TOTAL_BYTES:
            raise ChannelError('QMP total byte limit exceeded')
        if self._message_count > QMP.MAX_MESSAGES:
            raise ChannelError('QMP message count exceeded')

    def _run(self, action):
        try:
            return action()
        except Exception as exc:
            self._failed = True
            if isinstance(exc, ChannelError):
                raise
            raise ChannelError(str(exc)) from exc

    def greeting(self, timeout=10):
        def action():
            deadline = self._deadline(timeout, maximum=10)
            if self._greeted:
                raise ChannelError('greeting already consumed')
            message = self._next(deadline)
            if message is None:
                raise ChannelError('EOF before greeting')
            self._greeted = True
            return message
        return self._run(action)

    def command(self, name, timeout=10):
        def action():
            deadline = self._deadline(timeout, maximum=10)
            if not self._greeted or self._eof:
                raise ChannelError('command requires live greeted channel')
            # Validate already decoded trailing messages before command admission.
            while self._queue:
                self._next(deadline)
            message = self._session.command(name)  # Records issuance once.
            payload = json.dumps(message, ensure_ascii=False, allow_nan=False,
                                 separators=(',', ':')).encode('utf-8') + b'\n'
            self._budget(len(payload), 1)
            offset = 0
            while offset < len(payload):
                self._wait(deadline, writing=True)
                try:
                    count = self._socket.send(payload[offset:])
                except BlockingIOError:
                    continue
                if count <= 0:
                    raise ChannelError('QMP send made no progress')
                offset += count
            while True:
                reply = self._next(deadline)
                if reply is None:
                    raise ChannelError('EOF before command acknowledgement')
                if reply.get('id') == message['id']:
                    return reply
        return self._run(action)

    def receive(self, timeout=10):
        def action():
            deadline = self._deadline(timeout)
            if not self._greeted:
                raise ChannelError('greeting must be consumed first')
            return self._next(deadline)
        return self._run(action)

    def pump(self, timeout=60):
        def action():
            deadline = self._deadline(timeout)
            if not self._greeted:
                raise ChannelError('greeting must be consumed first')
            while self._session.shutdown_message is None:
                if self._next(deadline) is None:
                    raise ChannelError('EOF before SHUTDOWN observation')
            return self._session.shutdown_message
        return self._run(action)

    def finish(self, timeout=10):
        def action():
            deadline = self._deadline(timeout, maximum=10)
            if not self._greeted:
                raise ChannelError('greeting must be consumed first')
            while self._next(deadline) is not None:
                pass
            self._finished = True
            return self.clean_shutdown
        return self._run(action)

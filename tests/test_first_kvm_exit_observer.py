import contextlib
import io
import unittest
from unittest.mock import patch
import importlib.util
from pathlib import Path

_OBSERVER_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'observe-first-kvm-exit.py'
_SPEC = importlib.util.spec_from_file_location('first_kvm_exit_observer', _OBSERVER_PATH)
observe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(observe)


class FakeBus:
    def __init__(self, states, **changes):
        self.states, self.index = states, 0
        self.data = dict(LoadState='loaded', ActiveState='inactive', SubState='dead',
                         Result='success', MainPID=0, ControlPID=0, ExecMainPID=123,
                         ExecMainCode=1, ExecMainStatus=0,
                         ExecMainStartTimestampMonotonic=100,
                         ExecMainExitTimestampMonotonic=200)
        self.data.update(changes)

    def get(self, name):
        if name == 'SubState':
            return 'running'
        value = self.states[min(self.index, len(self.states) - 1)]
        self.index += 1
        return value

    def snapshot(self):
        return self.data


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class ObserverTests(unittest.TestCase):
    def run_observer(self, bus):
        clock, records = Clock(), []
        return observe.observe(bus, records.append, clock, clock.sleep), records

    def test_waits_for_active_and_captures_raw_fields(self):
        bus = FakeBus(['inactive', 'inactive', 'inactive', 'active', 'inactive'])
        final, records = self.run_observer(bus)
        self.assertEqual(records, [dict(event='ready', unit=observe.UNIT.decode(), ActiveState='inactive')])
        self.assertEqual(final['ExecMainCode'], 1)
        self.assertEqual(final['ExecMainStatus'], 0)
        self.assertEqual(bus.index, 5)

    def test_initial_inactive_is_not_completion(self):
        with self.assertRaises(TimeoutError):
            self.run_observer(FakeBus(['inactive']))

    def test_initial_active_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'before readiness'):
            self.run_observer(FakeBus(['active']))

    def test_failed_state_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'unit failed'):
            self.run_observer(FakeBus(['inactive', 'failed']))

    def test_bad_final_fields_rejected(self):
        for change in [dict(ExecMainCode=0), dict(ExecMainStatus=1), dict(Result='signal'),
                       dict(MainPID=123), dict(ExecMainStartTimestampMonotonic=0),
                       dict(ExecMainExitTimestampMonotonic=99), dict(ActiveState='active')]:
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.run_observer(FakeBus(['inactive', 'active', 'inactive'], **change))

    def test_main_cleanup_before_final(self):
        records = []
        class Context:
            def __enter__(self):
                records.append('enter')
                return object()
            def __exit__(self, *_):
                records.append('close')
        with patch.object(observe, 'Bus', Context), patch.object(observe, 'observe', return_value={'event':'final'}), patch.object(observe, 'emit', side_effect=lambda x: records.append(x)), patch.object(observe.sys, 'argv', ['observe.py']):
            self.assertEqual(observe.main(), 0)
        self.assertEqual(records, ['enter', 'close', {'event':'final'}])

    def test_main_failure_closes_and_emits_no_final(self):
        for error in [TimeoutError('timeout'), RuntimeError('bad fields')]:
            closed, records = [], []
            class Context:
                def __enter__(self):
                    return object()
                def __exit__(self, *_):
                    closed.append(True)
            with patch.object(observe, 'Bus', Context), patch.object(observe, 'observe', side_effect=error), patch.object(observe, 'emit', side_effect=records.append), patch.object(observe.sys, 'argv', ['observe.py']), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(observe.main(), 1)
            self.assertEqual(closed, [True])
            self.assertEqual(records, [])


if __name__ == '__main__':
    unittest.main()

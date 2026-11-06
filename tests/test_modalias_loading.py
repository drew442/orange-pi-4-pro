"""Run the real init modalias command against whitespace-containing sysfs fixtures."""
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class ModaliasLoadingTests(unittest.TestCase):
    def test_paths_are_preserved_and_module_failure_does_not_stop_scanning(self):
        for config in ('diagnostic-init', 'installer-init'):
            with self.subTest(config=config), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                devices = root / 'devices'
                aliases = ['platform:Fixed MDIO bus', 'test:load-fails', 'test:last-device']
                names = ['Fixed MDIO bus.0', "device's name", 'device\nwith-newline']
                for name, alias in zip(names, aliases):
                    directory = devices / name
                    directory.mkdir(parents=True)
                    (directory / 'modalias').write_text(alias + '\n')
                (devices / 'empty-device').mkdir()
                (devices / 'empty-device/modalias').write_bytes(b'')
                mock = root / 'bin'
                mock.mkdir()
                loader = mock / 'modprobe'
                loader.write_text('#!/bin/sh\nprintf "%s\\0" "$@" >> "$ALIAS_LOG"\n'
                                  '[ "$1" != test:load-fails ]\n')
                loader.chmod(0o755)
                source = (REPO / 'configs' / config).read_text()
                command = re.search(r"find /sys/devices -name modalias -exec sh -c '.*?' sh \{\} \\\; 2>/dev/null", source, re.S)
                self.assertIsNotNone(command)
                command = command.group().replace('/sys/devices', shlex.quote(str(devices)), 1)
                log = root / 'aliases.log'
                result = subprocess.run(['sh', '-c', command], capture_output=True, text=True,
                                        env={**os.environ, 'PATH': str(mock) + ':' + os.environ['PATH'],
                                             'ALIAS_LOG': str(log)}, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, '')
                self.assertCountEqual(log.read_bytes().split(b'\0')[:-1],
                                      [alias.encode() for alias in aliases])


if __name__ == '__main__':
    unittest.main()

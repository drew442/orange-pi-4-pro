"""Boot script control-flow fixtures, not U-Boot or physical boot acceptance."""
import os
from pathlib import Path
import subprocess
import unittest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'configs/spi-nvme-host-boot.cmd'
HARNESS = r'''
load_count=0
setenv() { printf 'ENV|%s|%s\n' "$1" "$2"; }
load() {
    load_count=$((load_count + 1))
    printf 'LOAD|%s|%s|%s|%s\n' "$1" "$2" "$3" "$4"
    test "$load_count" != "$fail_load"
}
fdt() { printf 'FDT|%s\n' "$*"; test "$1" != "$fail_fdt"; }
bootm() {
    printf 'BOOTM|%s\n' "$*"
    if test "$handoff" = yes; then exit 0; fi
    return 1
}
source "$1"
'''


class BootScriptTests(unittest.TestCase):
    def run_script(self, **overrides):
        env = dict(os.environ, devtype='nvme', devnum='0', distro_bootpart='1', prefix='/boot/',
                   kernel_addr_r='0x40080000', ramdisk_addr_r='0x44000000', fdt_addr_r='0x43000000',
                   fail_load='0', fail_fdt='none', handoff='no')
        env.update(overrides)
        return subprocess.run(['bash', '-c', HARNESS, 'fixture', str(SCRIPT)],
                              env=env, text=True, capture_output=True)

    def test_exact_payload_paths_and_preserved_boot_protocol(self):
        result = self.run_script(handoff='yes')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([l for l in result.stdout.splitlines() if l.startswith('LOAD|')], [
            'LOAD|nvme|0:1|0x40080000|/boot/uImage',
            'LOAD|nvme|0:1|0x44000000|/boot/uInitrd',
            'LOAD|nvme|0:1|0x43000000|/boot/dtb/allwinner/sun60i-a733-orangepi-4-pro.dtb'])
        self.assertIn('FDT|addr 0x43000000\nFDT|resize 65536\n', result.stdout)
        self.assertIn('BOOTM|0x40080000 0x44000000 0x43000000', result.stdout)
        old = (REPO / 'configs/host-boot.cmd').read_text()
        new = SCRIPT.read_text()
        old_args = next(l.strip() for l in old.splitlines() if l.startswith('setenv bootargs '))
        self.assertIn(old_args, new)

    def test_wrong_or_missing_context_never_reads_payloads(self):
        cases = [{'devtype': 'mmc'}, {'devtype': ''}, {'devnum': '1'}, {'devnum': '00'},
                 {'devnum': ''}, {'distro_bootpart': '2'}, {'distro_bootpart': '01'},
                 {'distro_bootpart': ''}, {'prefix': '/'}, {'prefix': '/boot'}, {'prefix': ''}]
        for case in cases:
            with self.subTest(case=case):
                result = self.run_script(**case)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('LOAD|', result.stdout)
                self.assertNotIn('ENV|', result.stdout)
                self.assertNotIn('BOOTM|', result.stdout)
                self.assertIn('Rejected SPI/NVMe', result.stdout)

    def test_load_failures_stop_before_handoff_and_return_failure(self):
        for position in range(1, 4):
            with self.subTest(position=position):
                result = self.run_script(fail_load=str(position))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout.count('LOAD|'), position)
                self.assertNotIn('FDT|', result.stdout)
                self.assertNotIn('BOOTM|', result.stdout)
                self.assertIn('Manual SD recovery: run bootcmd_mmc0', result.stdout)

    def test_fdt_and_returning_bootm_fail_without_loops(self):
        for command in ('addr', 'resize', 'none'):
            with self.subTest(command=command):
                result = self.run_script(fail_fdt=command)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual('BOOTM|' in result.stdout, command == 'none')
                self.assertIn('Manual SD recovery:', result.stdout)

    def test_no_persistent_writes_target_override_or_exit(self):
        commands = [l.strip().split()[0] for l in SCRIPT.read_text().splitlines()
                    if l.strip() and not l.lstrip().startswith('#')]
        self.assertEqual(commands[-1], 'false')
        self.assertFalse(set(commands) & {'exit', 'while', 'saveenv', 'sf', 'mmc', 'nvme', 'mw', 'reset'})
        self.assertNotIn('setenv boot_targets', SCRIPT.read_text())


if __name__ == '__main__':
    unittest.main()

"""Receipt applicability, conservative planning, and failure reporting; no board access."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("validation_workflow", REPO / "scripts/validate-project.py")
VALIDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATE)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for directory in ("tests", "scripts", "configs", "out", "docs"):
            (self.root / directory).mkdir()
        (self.root / ".gitignore").write_text("__pycache__/\n")
        shutil.copy(REPO / "scripts/validate-project.py", self.root / "scripts/validate-project.py")
        self.policy = {
            "schema": 1, "timeout_seconds": 10,
            "documentation": ["docs/**"], "known_inputs": ["scripts/**", "configs/**"],
            "fallback_inputs": ["scripts/**", "configs/**", "tests/**"],
            "ci_exclusions": [{"reason": "requires root", "prerequisite": "native root"}],
            "suites": [
                {"name": "boot", "tests": ["tests/test_boot.py"], "inputs": ["scripts/boot.py"]},
                {"name": "guest", "tests": ["tests/test_guest.py"], "inputs": ["scripts/guest.py"]}]}
        (self.root / "configs/validation-policy.json").write_text(json.dumps(self.policy))
        self.write_test("test_boot.py")
        self.write_test("test_guest.py")
        (self.root / "scripts/boot.py").write_text("# boot v1\n")
        (self.root / "scripts/guest.py").write_text("# guest v1\n")
        (self.root / "docs/note.md").write_text("documentation\n")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        self.paths = VALIDATE.inventory(self.root)
        self.stages = VALIDATE.stages(self.root, self.policy, self.paths)

    def write_test(self, name, body=None):
        (self.root / "tests" / name).write_text(body or
            "import unittest\nclass Checks(unittest.TestCase):\n    def test_one(self): self.assertEqual(1, 1)\n")

    def digest(self, stage=None, env=None):
        stage = stage or self.stages[0]
        return VALIDATE.fingerprint(self.root, stage["inputs"], stage,
                                    env or {"python": "test"}, VALIDATE.inventory(self.root))[0]

    def run_stage(self, source=None, scope="native", stage=None):
        stage = dict(stage or self.stages[0])
        if source:
            (self.root / stage["test"]).write_text(source)
        return VALIDATE.run_stage(self.root, stage, self.root / "out", self.digest(stage),
                                  scope, self.policy["ci_exclusions"])

    def test_docs_do_not_select_host_tests_or_change_applicability(self):
        before = self.digest()
        (self.root / "docs/note.md").write_text("different docs\n")
        self.assertEqual([], VALIDATE.select_stages(self.stages, ["docs/note.md"], "focused", self.policy))
        self.assertEqual(before, self.digest())

    def test_runtime_test_definition_and_unknown_changes(self):
        selected = VALIDATE.select_stages(self.stages, ["scripts/boot.py"], "focused", self.policy)
        self.assertEqual(["tests/test_boot.py"], [s["test"] for s in selected])
        self.assertEqual(self.stages, VALIDATE.select_stages(self.stages, ["scripts/unknown.py"], "focused", self.policy))
        self.assertEqual(self.stages, VALIDATE.select_stages(self.stages, ["new-runtime.c"], "focused", self.policy))
        self.assertEqual(self.stages, VALIDATE.select_stages(self.stages, [], "beta", self.policy))
        selected = VALIDATE.select_stages(self.stages, ["tests/test_guest.py"], "focused", self.policy)
        self.assertEqual(self.stages, selected)

    def test_imported_test_fixture_selects_and_invalidates_consumers(self):
        self.write_test("test_shared.py", "FIXTURE = 1\n")
        self.write_test("test_boot.py", "from test_shared import FIXTURE\nimport unittest\n"
                        "class Checks(unittest.TestCase):\n"
                        " def test_fixture(self): self.assertEqual(FIXTURE, 1)\n")
        stages = VALIDATE.stages(self.root, self.policy, VALIDATE.inventory(self.root))
        consumer = next(stage for stage in stages if stage["test"] == "tests/test_boot.py")
        before = self.digest(consumer)
        selected = VALIDATE.select_stages(stages, ["tests/test_shared.py"], "focused", self.policy)
        self.assertIn(consumer, selected)
        self.write_test("test_shared.py", "FIXTURE = 2\n")
        self.assertNotEqual(before, self.digest(consumer))

    def test_new_tests_are_required_without_registry_edit(self):
        self.write_test("test_new_module.py")
        stages = VALIDATE.stages(self.root, self.policy, VALIDATE.inventory(self.root))
        self.assertEqual(3, len(stages))
        self.assertTrue(next(s for s in stages if s["test"] == "tests/test_new_module.py")["required"])

    def test_unknown_runtime_invalidates_every_stage_including_full_plans(self):
        unknown = self.root / "scripts/unknown.py"
        unknown.write_text("VALUE = 1\n")
        stages = VALIDATE.stages(self.root, self.policy, VALIDATE.inventory(self.root))
        before = [self.digest(stage) for stage in stages]
        self.assertEqual(stages, VALIDATE.select_stages(stages, ["scripts/unknown.py"],
                                                      "focused", self.policy))
        unknown.write_text("VALUE = 2\n")
        for stage, digest in zip(stages, before):
            self.assertNotEqual(digest, self.digest(stage))
        # The changed file remains a dependency even without focused selection.
        full = VALIDATE.select_stages(stages, [], "full", self.policy)
        self.assertTrue(all("scripts/unknown.py" in stage["inputs"] for stage in full))

    def test_guest_https_restore_includes_shared_acquisition_dependencies(self):
        policy = VALIDATE.load_policy(REPO)
        stages = VALIDATE.stages(REPO, policy, VALIDATE.inventory(REPO))
        stage = next(s for s in stages if s["test"] == "tests/test_guest_restore_https.py")
        for path in ("scripts/acquire-nvme.py", "scripts/install-nvme.py"):
            self.assertTrue(VALIDATE.matches(path, stage["inputs"]))
            self.assertIn(stage, VALIDATE.select_stages(stages, [path], "focused", policy))

    def test_byte_definition_and_environment_changes_invalidate(self):
        stage = self.stages[0]
        before = self.digest()
        (self.root / "scripts/boot.py").write_text("# changed\n")
        self.assertNotEqual(before, self.digest())
        self.assertNotEqual(self.digest(), self.digest(env={"python": "other"}))
        self.assertNotEqual(self.digest(), self.digest(stage={**stage, "timeout": 20}))
        changed = self.digest()
        self.write_test("test_boot.py", "# replaced test definition\n")
        self.assertNotEqual(changed, self.digest())

    def test_membership_and_deleted_dependency_invalidate(self):
        args = (["scripts/*.py"], {"stage": "physical-gate-dependencies"}, {"board": "unit-a"})
        before, _ = VALIDATE.fingerprint(self.root, *args)
        (self.root / "scripts/new.py").write_text("# new dependency\n")
        added, _ = VALIDATE.fingerprint(self.root, *args)
        self.assertNotEqual(before, added)
        (self.root / "scripts/boot.py").unlink()
        deleted, inputs = VALIDATE.fingerprint(self.root, ["scripts/boot.py"], {}, {})
        self.assertIn(["scripts/boot.py", "MISSING"], inputs)
        self.assertNotEqual(added, deleted)

    def test_success_counts_and_reuse_without_execution(self):
        result = self.run_stage()
        self.assertEqual("PASS", result["status"])
        self.assertEqual((1, 1, 1), tuple(result["counts"][k] for k in ("collected", "tests_run", "executed")))
        receipt = self.root / "out" / (self.stages[0]["id"] + ".receipt.json")
        self.assertIsNotNone(VALIDATE.reusable(receipt, self.stages[0], self.digest(), "native"))
        next_out = self.root / "next"
        next_out.mkdir()
        reused = VALIDATE.run_stage(self.root, self.stages[0], next_out, self.digest(), "native", [], self.root / "out")
        self.assertEqual("REUSED", reused["status"])
        self.assertEqual(str(receipt.resolve()), reused["prior_receipt"])
        self.assertFalse((next_out / (self.stages[0]["id"] + ".stdout.log")).exists())

    def test_missing_stale_failed_and_corrupted_evidence_not_reused(self):
        receipt = self.root / "out" / (self.stages[0]["id"] + ".receipt.json")
        self.assertIsNone(VALIDATE.reusable(receipt, self.stages[0], self.digest(), "native"))
        self.run_stage()
        valid = receipt.read_text()
        self.assertIsNone(VALIDATE.reusable(receipt, self.stages[0], "stale", "native"))
        self.assertIsNone(VALIDATE.reusable(receipt, self.stages[0], self.digest(), "ci"))
        for key, value in (("status", "FAIL"), ("timeout", True), ("logs", []), ("exit_code", 1)):
            altered = json.loads(valid)
            altered[key] = value
            receipt.write_text(json.dumps(altered))
            self.assertIsNone(VALIDATE.reusable(receipt, self.stages[0], self.digest(), "native"))
        receipt.write_text(valid)
        (self.root / "out" / self.stages[0]["id"]).write_text("irrelevant")
        (self.root / "out" / (self.stages[0]["id"] + ".stderr.log")).write_text("corrupted")
        self.assertIsNone(VALIDATE.reusable(receipt, self.stages[0], self.digest(), "native"))

    def test_assertion_and_import_failure_never_pass(self):
        failed = self.run_stage("import unittest\nclass T(unittest.TestCase):\n def test_bad(self): self.fail('broken')\n")
        self.assertEqual("FAIL", failed["status"])
        self.assertEqual(1, failed["counts"]["failures"])
        self.assertNotEqual(0, failed["exit_code"])
        failed = self.run_stage("raise RuntimeError('import failure')\n")
        self.assertEqual("FAIL", failed["status"])
        self.assertEqual(0, failed["counts"]["executed"])

    def test_worker_imports_project_package_and_neighbor_fixture(self):
        (self.root / "scripts/project_fixture.py").write_text("VALUE = 7\n")
        (self.root / "tests/shared_fixture.py").write_text("VALUE = 3\n")
        result = self.run_stage("from scripts.project_fixture import VALUE\n"
                                "import shared_fixture\nimport unittest\n"
                                "class T(unittest.TestCase):\n"
                                " def test_imports(self): self.assertEqual(VALUE + shared_fixture.VALUE, 10)\n")
        self.assertEqual("PASS", result["status"])
        self.assertEqual(1, result["counts"]["executed"])

    def test_unexpected_and_native_expected_skips_block(self):
        for scope, reason in (("ci", "surprise"), ("native", "requires root")):
            with self.subTest(scope=scope):
                result = self.run_stage("import unittest\nclass T(unittest.TestCase):\n"
                                        f" @unittest.skip({reason!r})\n def test_skip(self): pass\n", scope)
                self.assertEqual("BLOCKED", result["status"])
                self.assertEqual(1, len(result["counts"]["unexpected_skips"]))

    def test_declared_ci_exclusions_visible_and_empty_not_pass(self):
        result = self.run_stage("import unittest\nclass T(unittest.TestCase):\n"
                                " @unittest.skip('requires root')\n def test_skip(self): pass\n", "ci")
        self.assertEqual("NOT_APPLICABLE", result["status"])
        self.assertEqual(0, result["counts"]["executed"])
        self.assertEqual(1, len(result["counts"]["excluded"]))
        empty = self.run_stage("# no tests\n")
        self.assertEqual("BLOCKED", empty["status"])

    def test_timeout_kills_process_group_and_preserves_logs(self):
        stage = {**self.stages[0], "timeout": 1}
        result = self.run_stage("import time\nprint('begin', flush=True)\ntime.sleep(20)\n", stage=stage)
        self.assertEqual("TIMEOUT", result["status"])
        self.assertTrue(result["timeout"])
        self.assertIn("begin", (self.root / "out" / (stage["id"] + ".stdout.log")).read_text())

    def test_shell_failure_and_success_count_one_program(self):
        stage = {"id": "test-shell", "test": "tests/test-shell.sh", "kind": "shell",
                 "inputs": ["tests/test-shell.sh"], "timeout": 10, "required": True}
        (self.root / stage["test"]).write_text("echo raw stdout\necho raw stderr >&2\nexit 7\n")
        result = self.run_stage(stage=stage)
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(7, result["exit_code"])
        (self.root / stage["test"]).write_text("exit 0\n")
        result = self.run_stage(stage=stage)
        self.assertEqual("PASS", result["status"])
        self.assertEqual(1, result["counts"]["executed"])

    def test_cli_plan_and_doc_only_summary_are_explicit(self):
        command = ["python3", str(self.root / "scripts/validate-project.py"), "--root", str(self.root),
                   "--changed", "docs/note.md", "--output", str(self.root / "out")]
        completed = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(0, completed.returncode, completed.stderr)
        summary = json.loads((self.root / "out/summary.json").read_text())
        self.assertEqual("NO_TESTS_REQUIRED", summary["status"])
        self.assertEqual(0, summary["selected_stages"])
        self.assertEqual("NOT_EVALUATED", summary["physical_gates"])
        completed = subprocess.run(command + ["--plan", "--mode", "beta"], capture_output=True, text=True)
        self.assertNotEqual(0, completed.returncode)

    def test_missing_command_and_interrupted_coordinator_never_retain_pass(self):
        digest = self.digest()
        with mock.patch.object(VALIDATE.subprocess, "Popen", side_effect=FileNotFoundError("missing executable")):
            result = VALIDATE.run_stage(self.root, self.stages[0], self.root / "out", digest,
                                        "native", self.policy["ci_exclusions"])
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(127, result["exit_code"])
        (self.root / "out/summary.json").write_text('{"status":"PASS"}')
        with mock.patch.object(VALIDATE, "environment", return_value={}), \
                mock.patch.object(VALIDATE, "run_stage", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                VALIDATE.main(["--root", str(self.root), "--changed", "scripts/boot.py",
                               "--output", str(self.root / "out")])
        summary = json.loads((self.root / "out/summary.json").read_text())
        self.assertEqual("INCOMPLETE", summary["status"])
        self.assertEqual(1, summary["selected_stages"])


if __name__ == "__main__":
    unittest.main()

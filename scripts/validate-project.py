#!/usr/bin/env python3
"""Plan host regressions and retain byte/environment-scoped validation receipts.

No hardware acceptance is implied. --mode beta requires every host stage, including
native prerequisites; --scope ci explicitly excludes declared unavailable fixtures.
"""
import argparse
import ast
import fnmatch
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
POLICY = "configs/validation-policy.json"
VERSION = 1


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def load_policy(root):
    policy = json.loads((root / POLICY).read_text())
    if policy.get("schema") != VERSION or not policy.get("suites"):
        raise ValueError("missing validation suite registry")
    return policy


def matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def inventory(root):
    """Tracked and untracked inputs; ignored logs/build outputs never become inputs."""
    result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                            cwd=root, check=True, stdout=subprocess.PIPE)
    return sorted(set(os.fsdecode(p) for p in result.stdout.split(b"\0") if p))


def environment():
    tools = {}
    for name in ("bash", "git", "mke2fs", "e2fsck", "debugfs", "tune2fs", "dumpe2fs",
                 "sfdisk", "blkid", "dpkg-deb", "openssl", "aria2c", "cpio", "gzip", "dtc", "mkimage"):
        executable = shutil.which(name)
        tools[name] = {"path": executable, "sha256": None}
        if executable:
            tools[name]["sha256"] = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
    return {"python": sys.version, "executable": sys.executable, "platform": platform.platform(),
            "uid": os.getuid(), "gid": os.getgid(), "groups": sorted(os.getgroups()),
            "capabilities": next((line for line in Path("/proc/self/status").read_text().splitlines()
                                  if line.startswith("CapEff:")), "unknown")
            if Path("/proc/self/status").exists() else "unavailable",
            "env": {key: os.environ.get(key) for key in
                    ("PATH", "PYTHONPATH", "LANG", "LC_ALL", "TZ", "TMPDIR", "HOME")},
            "tools": tools}


def fingerprint(root, patterns, definition, env, paths=None):
    """Generic applicability helper: dependencies + definition + explicit environment.

    Physical receipts can call this with their own image/boot/board dependencies and
    board identity in env. The caller must declare those dependencies completely.
    Missing literals and glob membership are hashed, including deletions/additions.
    """
    paths = inventory(root) if paths is None else paths
    selected = set(path for path in paths if matches(path, patterns))
    selected.update(p for p in patterns if not any(c in p for c in "*?["))
    inputs = []
    for relative in sorted(selected):
        path = root / relative
        if path.is_symlink():
            value = "symlink:" + os.readlink(path)
        elif path.is_file():
            value = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            value = "MISSING"
        inputs.append([relative, value])
    payload = {"schema": VERSION, "inputs": inputs, "definition": definition, "environment": env}
    return hashlib.sha256(canonical(payload)).hexdigest(), inputs


def stages(root, policy, paths):
    result = []
    declared = [pattern for suite in policy["suites"] for pattern in suite["inputs"]]
    # Unknown inputs force broad selection and must also invalidate broad reuse,
    # including full/beta plans where select_stages takes the all-stages branch.
    unknown_inputs = [path for path in paths if not matches(
        path, policy["documentation"] + declared + ["tests/**", POLICY, "scripts/validate-project.py"])]
    python_tests = sorted(p for p in paths if p.startswith("tests/") and
                          (fnmatch.fnmatchcase(p, "tests/test_*.py") or p == "tests/test-assemble-diagnostic.py"))
    shell_tests = sorted(p for p in paths if fnmatch.fnmatchcase(p, "tests/test-*.sh"))
    for test in python_tests + shell_tests:
        owners = [suite for suite in policy["suites"] if matches(test, suite["tests"])]
        # A new/unclassified test is never silently omitted or experimental.
        inputs = sorted(set(p for suite in owners for p in suite["inputs"])) if owners else policy["fallback_inputs"]
        # Retained fixture bytes are inputs too, even when ignored under out/.
        # Documentation prose is otherwise unrelated to host validation.
        fixture_inputs = []
        if test.endswith(".py"):
            tree = ast.parse((root / test).read_text())
            fixture_inputs = sorted({node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node.value.startswith(("docs/", "out/", "tmp/"))
                and "\n" not in node.value and not any(c in node.value for c in "*?[ ")})
        result.append({"id": Path(test).stem, "test": test,
                       "kind": "shell" if test.endswith(".sh") else "unittest",
                       # Test modules also import fixtures/helpers from other test
                       # modules. Conservatively retain the whole test tree so
                       # those shared bytes select and invalidate every consumer.
                       "inputs": inputs + fixture_inputs + unknown_inputs + [test, "tests/**", POLICY,
                                            "scripts/validate-project.py"],
                       "timeout": policy["timeout_seconds"], "required": True})
    if not result:
        raise ValueError("no registered/discovered test stages")
    return result


def select_stages(all_stages, changed, mode, policy):
    if mode in ("full", "beta"):
        return all_stages
    if any(not matches(path, policy["documentation"] + policy["known_inputs"] + ["tests/**"])
           for path in changed):
        return all_stages
    # A runtime path with no declared domain dependency is an unknown dependency,
    # even if fallback stages happen to include a broad glob for it.
    declared = [pattern for suite in policy["suites"] for pattern in suite["inputs"]]
    if any(matches(path, policy["known_inputs"]) and not matches(path, declared)
           and path not in (POLICY, "scripts/validate-project.py") for path in changed):
        return all_stages
    return [stage for stage in all_stages if any(matches(path, stage["inputs"]) for path in changed)]


def changed_paths(root, base):
    # Merge-base changes plus working tree/index and new files; deletions included.
    result = subprocess.run(["git", "diff", "--name-only", "-z", base], cwd=root,
                            check=True, stdout=subprocess.PIPE)
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                               cwd=root, check=True, stdout=subprocess.PIPE)
    return sorted(set(os.fsdecode(p) for p in (result.stdout + untracked.stdout).split(b"\0") if p))


def classify_counts(data, scope, allowed):
    skipped = data.get("skipped", [])
    exclusions = [item for item in skipped if scope == "ci" and
                  any(item[1] == rule.get("reason") or
                      (rule.get("prefix") and item[1].startswith(rule["prefix"])) for rule in allowed)]
    unexpected = [item for item in skipped if item not in exclusions]
    executed = data["tests_run"] - len(skipped)
    if unexpected:
        status = "BLOCKED"
    elif data.get("failures") or data.get("errors") or data.get("unexpected_successes"):
        status = "FAIL"
    elif executed <= 0:
        status = "NOT_APPLICABLE" if exclusions and scope == "ci" else "BLOCKED"
    else:
        status = "PASS"
    return {**data, "executed": executed, "excluded": exclusions,
            "unexpected_skips": unexpected, "status": status}


def worker(root, test, result_path):
    spec = importlib.util.spec_from_file_location(Path(test).stem.replace("-", "_"), root / test)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(root / "tests"))
    spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    collected = suite.countTestCases()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    data = {"collected": collected, "tests_run": result.testsRun,
            "failures": len(result.failures), "errors": len(result.errors),
            "unexpected_successes": len(result.unexpectedSuccesses),
            "expected_failures": len(result.expectedFailures),
            "skipped": [[case.id(), reason] for case, reason in result.skipped]}
    Path(result_path).write_text(json.dumps(data))
    return 0 if result.wasSuccessful() else 1


def reusable(path, stage, digest, scope):
    try:
        prior = json.loads(path.read_text())
        if (prior["schema"] != VERSION or prior["status"] != "PASS" or prior["fingerprint"] != digest
                or prior["stage"] != stage or prior["scope"] != scope or prior["exit_code"] != 0
                or prior["timeout"] or prior["counts"]["executed"] <= 0
                or prior["counts"].get("unexpected_skips")
                or prior["counts"].get("failures") or prior["counts"].get("errors")
                or prior["counts"].get("unexpected_successes")
                or prior["counts"]["collected"] != prior["counts"]["tests_run"]
                or prior["counts"]["status"] != "PASS"):
            return None
        # A receipt whose evidence files have disappeared or changed is incomplete.
        for log in prior["logs"]:
            target = path.parent / log["file"]
            if hashlib.sha256(target.read_bytes()).hexdigest() != log["sha256"]:
                return None
        if not prior["logs"]:
            return None
        return prior
    except (OSError, ValueError, KeyError, TypeError):
        return None


def run_stage(root, stage, output, digest, scope, allowed, reuse_dir=None):
    receipt_name = stage["id"] + ".receipt.json"
    if reuse_dir:
        prior_path = reuse_dir / receipt_name
        prior = reusable(prior_path, stage, digest, scope)
        if prior:
            return {**prior, "status": "REUSED", "prior_receipt": str(prior_path.resolve())}
    stdout = output / (stage["id"] + ".stdout.log")
    stderr = output / (stage["id"] + ".stderr.log")
    counts_path = output / (stage["id"] + ".counts.json")
    counts_path.unlink(missing_ok=True)
    command = ([sys.executable, str(root / "scripts/validate-project.py"), "--worker", stage["test"],
                "--worker-result", str(counts_path)] if stage["kind"] == "unittest"
               else ["bash", stage["test"]])
    timed_out = False
    started = time.monotonic()
    with stdout.open("wb") as out, stderr.open("wb") as err:
        try:
            process = subprocess.Popen(command, cwd=root, stdout=out, stderr=err, start_new_session=True)
            try:
                code = process.wait(timeout=stage["timeout"])
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                code = process.wait()
        except OSError as error:
            err.write((str(error) + "\n").encode())
            code = 127
    if stage["kind"] == "shell":
        # A shell file is one assertion program; never invent individual test counts.
        counts = {"collected": 1, "tests_run": int(not timed_out), "skipped": [],
                  "failures": int(code != 0), "errors": 0}
    else:
        try:
            counts = json.loads(counts_path.read_text())
            if counts["collected"] != counts["tests_run"]:
                raise ValueError("incomplete test run")
        except (OSError, ValueError, KeyError):
            counts = {"collected": 0, "tests_run": 0, "skipped": [], "failures": 0, "errors": 1}
    counts = classify_counts(counts, scope, allowed)
    status = "TIMEOUT" if timed_out else "FAIL" if code != 0 else counts["status"]
    receipt = {"schema": VERSION, "stage": stage, "scope": scope, "status": status,
               "fingerprint": digest, "exit_code": code, "timeout": timed_out,
               "duration_seconds": round(time.monotonic() - started, 3), "command": command,
               "counts": counts, "logs": [{"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                                           for p in (stdout, stderr)]}
    (output / receipt_name).write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--mode", choices=("focused", "full", "beta"), default="focused")
    parser.add_argument("--scope", choices=("ci", "native"), default="ci")
    parser.add_argument("--base", default="HEAD")
    parser.add_argument("--changed", action="append", help="explicit relative changed path (repeatable)")
    parser.add_argument("--output", type=Path, default=Path("out/validation/latest"))
    parser.add_argument("--reuse", type=Path)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--worker-result", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.worker:
        return worker(root, args.worker, args.worker_result)
    if args.mode == "beta" and args.scope != "native":
        parser.error("beta requires --scope native; CI exclusions cannot establish beta acceptance")
    policy = load_policy(root)
    paths = inventory(root)
    all_stages = stages(root, policy, paths)
    changed = args.changed if args.changed is not None else changed_paths(root, args.base)
    selected = select_stages(all_stages, changed, args.mode, policy)
    plan = {"mode": args.mode, "scope": args.scope, "changed": changed,
            "registered_stages": len(all_stages), "selected_stages": len(selected),
            "stages": [{"id": stage["id"], "test": stage["test"], "timeout_seconds": stage["timeout"]}
                       for stage in selected],
            "acceptance": "host regressions only; physical SPI/manual KVM gates remain separate"}
    if args.plan:
        print(json.dumps(plan, indent=2))
        return 0
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # An interrupted/failed coordinator must not leave an earlier PASS summary.
    (output / "summary.json").write_text(json.dumps({**plan, "schema": VERSION,
        "status": "INCOMPLETE", "results": [], "physical_gates": "NOT_EVALUATED"}, indent=2) + "\n")
    (output / "summary.txt").write_text("INCOMPLETE: required stages have not all completed\n")
    env = environment()
    results = []
    for stage in selected:
        digest, _ = fingerprint(root, stage["inputs"], stage, env, paths)
        result = run_stage(root, stage, output, digest, args.scope,
                           policy["ci_exclusions"], args.reuse.resolve() if args.reuse else None)
        results.append(result)
        print(f"{result['status']:14} {stage['test']} run={result['counts']['tests_run']} "
              f"excluded={len(result['counts']['excluded'])} exit={result['exit_code']}", flush=True)
    good = {"PASS", "REUSED"} | ({"NOT_APPLICABLE"} if args.scope == "ci" else set())
    complete = len(results) == len(selected) and all(item["status"] in good for item in results)
    # No changed runtime stage is called PASS when the plan contains zero tests.
    summary = {**plan, "schema": VERSION, "status": ("PASS" if selected else "NO_TESTS_REQUIRED")
               if complete else "FAIL", "environment": env, "results": results,
               "totals": {"collected": sum(r["counts"]["collected"] for r in results),
                          "executed": sum(r["counts"]["executed"] for r in results),
                          "excluded": sum(len(r["counts"]["excluded"]) for r in results),
                          "reused_stages": sum(r["status"] == "REUSED" for r in results)},
               "physical_gates": "NOT_EVALUATED"}
    if selected and complete and not summary["totals"]["executed"]:
        summary["status"] = "NO_APPLICABLE_TESTS"
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    text = [f"{summary['status']} mode={args.mode} scope={args.scope} stages={len(results)}/{len(selected)}",
            f"tests: {summary['totals']}", "physical_gates=NOT_EVALUATED"]
    text += [f"{r['status']} {r['stage']['test']} tests={r['counts']['tests_run']} "
             f"exit={r['exit_code']} timeout={r['timeout']} fingerprint={r['fingerprint']}"
             + (f" prior={r['prior_receipt']}" if "prior_receipt" in r else "") for r in results]
    (output / "summary.txt").write_text("\n".join(text) + "\n")
    print("\n".join(text[:3]))
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())

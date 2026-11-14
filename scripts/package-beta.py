#!/usr/bin/env python3
"""Assemble existing producer outputs into a private candidate or gated beta.

Pins and eligibility assessments are operator assertions, not signatures, secret
scanning, legal clearance, or independent verification of physical observations.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
POLICY = "configs/beta-release.json"


class PackageError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise PackageError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def identity(value):
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def sha(value):
    require(isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value), "invalid SHA256 pin")
    return value


def relative(value):
    require(isinstance(value, str) and value and "\\" not in value and "\0" not in value,
            "invalid relative path")
    parts = PurePosixPath(value)
    require(not parts.is_absolute() and ".." not in parts.parts and str(parts) == value,
            "absolute/traversing/noncanonical input path: " + value)
    return parts


def safe_file(base, value):
    parts = relative(value)
    path = base
    for part in parts.parts:
        path = path / part
        require(not path.is_symlink(), "symlink input refused: " + str(path))
    require(path.is_file(), "missing input file: " + str(path))
    return path


def git(project, *args):
    return subprocess.check_output(["git", "-C", str(project), *args], stderr=subprocess.PIPE)


def pinned(base, record):
    require(isinstance(record, dict), "pin must be an object")
    path = safe_file(base, record["path"])
    data = path.read_bytes()
    require(digest(data) == sha(record["sha256"]), "pin mismatch: " + str(path))
    return path, data


def pointer(document, location):
    require(isinstance(location, str) and location.startswith("/"), "record must be a JSON pointer")
    for component in location[1:].split("/"):
        component = component.replace("~1", "/").replace("~0", "~")
        document = document[int(component)] if isinstance(document, list) else document[component]
    require(isinstance(document, dict), "producer file record must be an object")
    return document


def distribution(assessment, bound, public, base, pins):
    require(isinstance(assessment, dict) and all(assessment.get(k) for k in ("assessed_by", "basis")),
            "each distribution assessment needs assessed_by and basis")
    require(all(assessment.get(k) == value for k, value in bound.items()), "distribution assessment identity mismatch")
    require(type(assessment.get("personalized")) is bool and type(assessment.get("private_identity")) is bool,
            "distribution assessment needs explicit personalized/private_identity booleans")
    require(assessment.get("redistribution") in ("granted", "unresolved", "prohibited"),
            "distribution assessment needs redistribution status")
    if assessment.get("grant"):
        pins.append(pinned(base, assessment["grant"]))
    if public:
        require(not assessment["personalized"] and not assessment["private_identity"],
                "public distribution refuses personalized/private-identity content")
        require(assessment["redistribution"] == "granted" and assessment.get("grant"),
                "public distribution requires a pinned redistribution grant for every asset and source")


def gate_inputs(name, rule, spec, assets, runtime_hash, dependencies):
    result = {"gate": name, "scope": rule["scope"], "board_model": spec["board_model"]}
    for dependency in rule["dependencies"]:
        if dependency == "source_commit":
            result[dependency] = spec[dependency]
        elif dependency == "runtime_manifest":
            result[dependency] = runtime_hash
        elif dependency in dependencies:
            result[dependency] = dependencies[dependency]
        elif dependency == "all_assets":
            result[dependency] = {a["role"]: {"sha256": a["sha256"], "bytes": a["bytes"],
                                              "producer_sha256": a["producer_sha256"]} for a in assets}
        else:
            asset = next(a for a in assets if a["role"] == dependency)
            result[dependency] = {"sha256": asset["sha256"], "bytes": asset["bytes"]}
    return result


def committed_records(project, revision):
    records = {}
    for entry in git(project, "ls-tree", "-rz", revision).split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        require(metadata.split()[0] in (b"100644", b"100755"), "source snapshot refuses nonregular Git entries")
        name = name.decode()
        relative(name)
        data = git(project, "show", revision + ":" + name)
        records[name] = {"bytes": len(data), "sha256": digest(data)}
    return records


def source_record(project, base, pin, pins):
    path, data = pinned(base, pin)
    record = json.loads(data)
    revision = record.get("revision")
    require(isinstance(revision, str) and re.fullmatch("[0-9a-f]{40}", revision),
            "source record needs launcher revision")
    require(isinstance(record.get("files"), dict) and record["files"], "source record needs pinned files")
    committed = committed_records(project, revision)
    require(set(record["files"]) == set(committed), "source record must contain the complete committed snapshot")
    for name, expected in record["files"].items():
        relative(name)
        sha(expected["sha256"])
        require(type(expected.get("bytes")) is int and expected["bytes"] >= 0, "source record file size invalid")
        require(all(expected[k] == committed[name][k] for k in ("bytes", "sha256")),
                "source record file differs from committed source: " + name)
    pins.append((path, data))
    return {"sha256": digest(data), "revision": revision, "files": committed}


def runtime_dependencies(project, runtime, source, current):
    """Bind existing payload provenance and its closed tool set to build/current source."""
    revision = source["revision"]
    files = runtime.get("files", {})
    packaged = {}

    def check_source(name, record):
        expected = source["files"].get(name)
        require(expected is not None and current.get(name) == expected,
                "packaged runtime source differs from release source: " + name)
        require(isinstance(record, dict) and all(record.get(k) == expected[k] for k in ("bytes", "sha256")),
                "runtime provenance differs from build source: " + name)
        packaged[name] = expected

    # The recipe fixes provenance layout and the packager fixes tool membership.
    check_source("build/host-payload.py", files.get("provenance/host-payload.py"))
    check_source("build/maintenance-tools.py", files.get("provenance/maintenance/build/maintenance-tools.py"))
    recipe = ast.parse(git(project, "show", revision + ":build/host-payload.py"))
    maps = {}
    for node in recipe.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("MANAGEMENT_FILES", "GUEST_BOOT_FILES"):
                    maps[target.id] = ast.literal_eval(node.value)
    require(set(maps) == {"MANAGEMENT_FILES", "GUEST_BOOT_FILES"}, "payload runtime maps missing")
    for name, descriptor in (("MANAGEMENT_FILES", "management_runtime"), ("GUEST_BOOT_FILES", "guest_boot_runtime")):
        records = runtime.get(descriptor, {}).get("files", {})
        require(set(records) == {destination for destination, _ in maps[name].values()}, "runtime file membership differs: " + descriptor)
        for path, (destination, _) in maps[name].items():
            check_source(path, records[destination])
    tree = ast.parse(git(project, "show", revision + ":build/maintenance-tools.py"))
    tools = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("SOURCES", "LEGACY_SOURCES") for t in node.targets):
            tools.update(n.value for n in ast.walk(node.value) if isinstance(n, ast.Constant) and isinstance(n.value, str) and "/" in n.value)
    require(tools, "maintenance source membership missing")
    records = runtime.get("maintenance_runtime", {}).get("files", {})
    prefix = "usr/lib/a733-tools/"
    require(set(records) == {prefix + name for name in tools | {"package.json"}}, "maintenance runtime membership differs")
    for name in tools:
        check_source(name, records[prefix + name])
    require(isinstance(runtime.get("sources"), dict) and runtime["sources"].get("rootfs.tar.gz"), "runtime userspace/kernel sources missing")
    return {"packaged_sources": packaged, "components": runtime["sources"],
            "guest_management_profile": runtime["guest_management_profile"],
            "package_state": runtime.get("package_state")}


def inspect(project, spec_path, candidate):
    spec_path = spec_path.absolute()
    require(not any(p.is_symlink() for p in [spec_path, *spec_path.parents]), "symlink spec/ancestor refused")
    spec = json.loads(spec_path.read_bytes())
    base = spec_path.parent
    commit = spec.get("source_commit")
    require(isinstance(commit, str) and re.fullmatch("[0-9a-f]{40}", commit), "source_commit must be a full commit ID")
    require(git(project, "rev-parse", "HEAD").decode().strip() == commit, "source_commit must equal current committed HEAD")
    require(not git(project, "status", "--porcelain", "--untracked-files=all").strip(),
            "source project must be clean, including untracked files (place operator inputs under ignored out/)")
    policy = json.loads(git(project, "show", commit + ":" + POLICY))
    require(spec.get("schema") == policy.get("schema") == 1, "unsupported package schema")
    for field in ("board_model", "boot_profile", "guest_management_profile"):
        require(spec.get(field) == policy[field], "unsupported " + field)
    require(spec.get("visibility") in ("private", "public"), "visibility must be private or public")
    public = spec["visibility"] == "public"
    pins = []
    runtime_path, runtime_bytes = pinned(base, spec["runtime_manifest"])
    pins.append((runtime_path, runtime_bytes))
    runtime = json.loads(runtime_bytes)
    # Existing host-payload manifests explicitly carry this field. Missing legacy
    # values do not get the new manual default applied during packaging.
    require(runtime.get("guest_management_profile") == spec["guest_management_profile"],
            "authenticated runtime manifest lacks/mismatches guest_management_profile")
    runtime_source = source_record(project, base, spec["runtime_source"], pins)
    current = committed_records(project, commit)
    runtime_inputs = runtime_dependencies(project, runtime, runtime_source, current)
    runtime_fingerprint = identity(runtime_inputs)
    runtime_hash = digest(runtime_bytes)
    assets = []
    roles = set()
    for item in spec["assets"]:
        role = item["role"]
        require(role in policy["required_roles"] + policy["optional_roles"] and role not in roles,
                "unknown or duplicate asset role: " + role)
        roles.add(role)
        producer_path, producer_bytes = pinned(base, item["producer"])
        pins.append((producer_path, producer_bytes))
        producer = json.loads(producer_bytes)
        if role == "nvme-install-image":
            require(producer.get("inputs", {}).get("roots_manifest") ==
                    {"bytes": len(runtime_bytes), "sha256": runtime_hash},
                    "NVMe producer does not bind the authenticated manual-beta roots manifest")
            for slot in ("A", "B"):
                require(producer.get("partition_payloads", {}).get("root-" + slot.lower()) ==
                        runtime.get("files", {}).get("root-" + slot + ".ext4") and
                        runtime.get("files", {}).get("root-" + slot + ".ext4"),
                        "NVMe root partition does not match authenticated runtime")
            boot_components = producer.get("boot_files")
            require(isinstance(boot_components, dict) and boot_components, "NVMe producer boot_files missing")
        if role.startswith("root-update"):
            require(digest(producer_bytes) == runtime_hash,
                    "root-update producer must be the authenticated current runtime manifest")
            slots = ("A", "B") if role == "root-update" else (role[-1].upper(),)
            require(item["record"] in {"/files/root-" + slot + suffix for slot in slots for suffix in (".ext4", ".ext4.gz")},
                    "root-update role must select its matching root image")
        record = pointer(producer, item["record"])
        artifact = safe_file(producer_path.parent, item.get("file", record.get("path")))
        require(type(record.get("bytes")) is int and record["bytes"] > 0, "producer file bytes must be positive")
        require(artifact.stat().st_size == record["bytes"], "producer file size mismatch: " + str(artifact))
        asset = {"role": role, "sha256": sha(record["sha256"]), "bytes": record["bytes"],
                 "producer_sha256": digest(producer_bytes), "producer_record": item["record"],
                 "source_record": source_record(project, base, item["source_record"], pins) if item.get("source_record") else None,
                 "source_path": str(artifact), "file": "assets/" + role + "/" + artifact.name,
                 "distribution": item["distribution"]}
        distribution(item["distribution"], {"sha256": asset["sha256"]}, public, base, pins)
        assets.append(asset)
    require(set(policy["required_roles"]) <= roles, "missing required asset roles")
    distribution(spec["source_distribution"], {"source_commit": commit}, public, base, pins)
    require(isinstance(spec.get("hardware_compatibility"), str) and spec["hardware_compatibility"].strip(),
            "hardware_compatibility must identify the assessed board/platform")
    context = spec.get("manual_lifecycle_context", {})
    require(isinstance(context, dict) and set(context) == {"guest_fixture_sha256", "storage_plan_sha256"},
            "manual_lifecycle_context needs guest fixture and storage plan pins")
    for value in context.values():
        sha(value)
    dependencies = {
        "software_inputs": {name: record for name, record in current.items()
                            if name.startswith(("scripts/", "build/", "configs/", "tests/", ".github/workflows/"))},
        "runtime": runtime_inputs,
        "boot_components": boot_components,
        "hardware_compatibility": spec["hardware_compatibility"],
        "manual_lifecycle_context": context,
    }
    gates = {}
    problems = []
    for name, rule in policy["gates"].items():
        inputs = gate_inputs(name, rule, spec, assets, runtime_hash, dependencies)
        expected = identity(inputs)
        gate = spec.get("gates", {}).get(name, {})
        evaluated = {"status": "MISSING", "expected_dependency_fingerprint": expected, "inputs": inputs}
        try:
            require(gate.get("status") in ("PASS", "REUSED"), "required gate has no PASS/REUSED")
            require(gate.get("dependency_fingerprint") == expected, "gate dependency fingerprint mismatch")
            if gate["status"] == "REUSED":
                assessment = gate.get("applicability", {})
                require(all(assessment.get(k) for k in ("assessed_by", "basis")) and
                        assessment.get("dependency_fingerprint") == expected,
                        "reused observations need explicit current applicability assessment")
            receipts = gate.get("receipts", [])
            require(isinstance(receipts, list) and receipts, "gate needs pinned scoped evidence receipts")
            observations = {}
            current_manual_profile = False
            receipt_hashes = set()
            for receipt_pin in receipts:
                path, data = pinned(base, receipt_pin)
                require(digest(data) not in receipt_hashes, "duplicate evidence receipt")
                receipt_hashes.add(digest(data))
                pins.append((path, data))
                receipt = json.loads(data)
                require(receipt.get("gate") == name and receipt.get("scope") == rule["scope"] and
                        receipt.get("status") == "PASS" and receipt.get("dependency_fingerprint") == expected,
                        "receipt gate/scope/status/dependency identity mismatch")
                require(receipt.get("observations"), "receipt needs pinned observations")
                for observation in receipt["observations"]:
                    key = observation.get("id")
                    require(isinstance(key, str) and key and key not in observations, "missing/duplicate observation identity")
                    raw_path, raw = pinned(path.parent, observation)
                    require(raw, "empty raw observation")
                    pins.append((raw_path, raw))
                    observations[key] = observation["kind"]
                    if observation["kind"] == "manual-profile" and observation.get("runtime_fingerprint") == runtime_fingerprint:
                        current_manual_profile = True
            kinds = list(observations.values())
            for required_kind in rule.get("observations", []):
                require(required_kind in kinds, "missing observation kind: " + required_kind)
            for kind in ("cold", "warm"):
                if kind in rule:
                    require(kinds.count(kind) >= rule[kind], "insufficient " + kind + " SPI boot credit")
            if rule.get("current_manual_profile"):
                require(current_manual_profile, "missing final manual-profile observation for current runtime fingerprint")
            evaluated.update(status=gate["status"], observations=observations,
                             receipts=receipts, applicability=gate.get("applicability"))
        except (PackageError, KeyError, TypeError, ValueError, OSError) as error:
            evaluated.update(status="BLOCKED", reason=str(error))
            problems.append(name + ": " + str(error))
        gates[name] = evaluated
    metadata_assessment = spec.get("metadata_distribution")
    if metadata_assessment and metadata_assessment.get("grant"):
        pins.append(pinned(base, metadata_assessment["grant"]))
    declarations = {key: value for key, value in spec.items() if key not in ("metadata_distribution", "visibility", "_instructions")}
    metadata_identity = identity({"contents": sorted({digest(data) for _, data in pins}), "declarations": declarations})
    if public:
        distribution(metadata_assessment, {"sha256": metadata_identity}, public, base, pins)
    guides = {}
    entries = git(project, "ls-tree", "-rz", commit).split(b"\0")
    names = []
    for entry in entries:
        if entry:
            metadata, name = entry.split(b"\t", 1)
            require(metadata.split()[0] in (b"100644", b"100755"), "source archive refuses nonregular Git entries")
            names.append(name.decode())
    # Preserve relative links from the maintained guides into committed references.
    guide_names = set(policy["operator_guides"]) | {name for name in names if name.startswith("docs/")}
    for name in sorted(guide_names):
        relative(name)
        require(name in names, "missing committed operator guide: " + name)
        guides[name] = git(project, "show", commit + ":" + name)
    notices = [name for name in names if any(name.startswith(prefix) for prefix in policy["notice_prefixes"])]
    require(notices, "committed notices are missing")
    for name in notices:
        relative(name)
        guides[name] = git(project, "show", commit + ":" + name)
    if not candidate:
        require(not problems, "beta release blocked: " + "; ".join(problems))
    return {"spec": spec, "policy": policy, "assets": assets, "pins": pins, "guides": guides,
            "runtime_source": runtime_source,
            "runtime_fingerprint": runtime_fingerprint,
            "metadata_identity": metadata_identity, "input_base": base,
            "gates": gates, "problems": problems, "candidate": candidate}


def stream_copy(source, destination, expected_hash=None, expected_size=None):
    total = 0
    hasher = hashlib.sha256()
    with destination.open("xb") as target:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            target.write(block)
            hasher.update(block)
            total += len(block)
    checksum = hasher.hexdigest()
    require(expected_size is None or total == expected_size, "copied asset size mismatch")
    require(expected_hash is None or checksum == expected_hash, "copied asset SHA256 mismatch")
    return {"sha256": checksum, "bytes": total}


def assemble(project, inspected, output):
    require(not output.exists() and not output.is_symlink(), "output must be a NEW directory")
    # Reject symlink ancestors rather than following a surprise external destination.
    require(not any(p.is_symlink() for p in [output.parent, *output.parents]), "symlink output ancestor refused")
    output.mkdir(parents=False)
    os.chmod(output, 0o700)
    files = {}
    try:
        def write(name, data):
            relative(name)
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(data)
            files[name] = {"sha256": digest(data), "bytes": len(data)}

        for asset in inspected["assets"]:
            target = output / asset["file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            source = Path(asset["source_path"])
            # Recheck symlinks after planning; O_NOFOLLOW guards the final open.
            require(not source.is_symlink() and not any(p.is_symlink() for p in source.parents), "symlink artifact refused")
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as handle:
                files[asset["file"]] = stream_copy(handle, target, asset["sha256"], asset["bytes"])
        for path, data in inspected["pins"]:
            # Content-addressed names retain every pinned producer/evidence/grant byte.
            name = "receipts/" + digest(data) + "." + (path.suffix.lstrip(".") or "bin")
            if name not in files:
                write(name, data)
        for name, data in inspected["guides"].items():
            write("operator/" + name, data)
        commit = inspected["spec"]["source_commit"]
        archive_name = "source/" + commit + ".tar"
        archive = output / archive_name
        archive.parent.mkdir()
        process = subprocess.Popen(["git", "-C", str(project), "archive", "--format=tar", commit],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        files[archive_name] = stream_copy(process.stdout, archive)
        _, error = process.communicate()
        require(process.returncode == 0, "git source archive failed: " + error.decode(errors="replace"))
        spec = inspected["spec"]
        notes = ("# " + ("Candidate" if inspected["candidate"] else "Beta ready") + " package\n\n"
                 + f"Source: {commit}\nVisibility: {spec['visibility']}\n"
                 + "Board: Orange Pi 4 Pro; SPI/NVMe boot with SD absent; manual-beta guest management.\n\n"
                 + "Existing compressed assets were copied without rebuilding or recompression.\n"
                 + "Eligibility and physical applicability records are operator assertions; no automatic legal clearance, secret scanning or public upload.\n"
                 + "Private packages retain producer licensing and personalization restrictions.\n\n"
                 + "operator/docs retains committed documentation references. For links to build/config/script files, "
                 + "extract the source archive and read its README/docs in that complete source tree.\n\n"
                 + "Gate results:\n" + "\n".join(f"- {name}: {gate['status']}" for name, gate in inspected["gates"].items())
                 + "\n\n" + "\n".join(inspected["problems"]) + "\n")
        write("RELEASE-NOTES.md", notes.encode())
        manifest = {"schema": 1, "state": "candidate" if inspected["candidate"] else "beta_ready",
                    "visibility": spec["visibility"], "source_commit": commit,
                    "board_model": spec["board_model"], "boot_profile": spec["boot_profile"],
                    "guest_management_profile": spec["guest_management_profile"],
                    "runtime_manifest": spec["runtime_manifest"], "gates": inspected["gates"],
                    "runtime_source": inspected["runtime_source"],
                    "runtime_fingerprint": inspected["runtime_fingerprint"],
                    "metadata_distribution": spec.get("metadata_distribution"),
                    "metadata_content_fingerprint": inspected["metadata_identity"],
                    "pinned_inputs": [{"input_path": str(path.relative_to(inspected["input_base"])),
                                       "sha256": digest(data), "file": "receipts/" + digest(data) + "." +
                                       (path.suffix.lstrip(".") or "bin")}
                                      for path, data in inspected["pins"]],
                    "assets": [{k: v for k, v in a.items() if k != "source_path"} for a in inspected["assets"]],
                    "source_distribution": spec["source_distribution"], "eligibility_is_operator_assertion": True,
                    "files": dict(files)}
        write("release-manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
        write("SHA256SUMS", "".join(f"{value['sha256']}  {name}\n" for name, value in sorted(files.items())).encode())
        return manifest
    except BaseException:
        # Only the directory created by this invocation is removed on failure.
        shutil.rmtree(output)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=ROOT)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--candidate", action="store_true")
    args = parser.parse_args(argv)
    try:
        # Plans expose missing gates even when planning a normal release.
        inspected = inspect(args.project.resolve(), args.spec, args.candidate or args.plan)
        if args.plan:
            print(json.dumps({"state": "candidate" if args.candidate else "beta_ready_requested",
                              "source_commit": inspected["spec"]["source_commit"],
                              "assets": inspected["assets"], "gates": inspected["gates"],
                              "metadata_content_fingerprint": inspected["metadata_identity"],
                              "runtime_fingerprint": inspected["runtime_fingerprint"],
                              "missing_prerequisites": inspected["problems"],
                              "asset_bytes_verified": False, "read_only": True}, indent=2))
            return 0
        require(args.output is not None, "--output NEW_DIR is required")
        manifest = assemble(args.project.resolve(), inspected, args.output.absolute())
        print(json.dumps({"state": manifest["state"], "visibility": manifest["visibility"],
                          "output": str(args.output.absolute())}))
        return 0
    except (PackageError, OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        if args.plan:
            print(json.dumps({"state": "BLOCKED", "missing_prerequisites": [str(error)],
                              "asset_bytes_verified": False, "read_only": True}, indent=2))
        print("package-beta: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

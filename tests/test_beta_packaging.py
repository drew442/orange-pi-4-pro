"""Tiny existing-producer fixtures; no builds, downloads, devices or physical claims."""
import copy
import gzip
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("beta_packaging", REPO / "scripts/package-beta.py")
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "project"
        self.root.mkdir()
        self.policy = json.loads((REPO / "configs/beta-release.json").read_bytes())
        (self.root / "configs").mkdir()
        (self.root / "configs/beta-release.json").write_text(json.dumps(self.policy))
        for guide in self.policy["operator_guides"] + ["licenses/notice.txt"]:
            target = self.root / guide
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("Committed operator/source notice: " + guide + "\n")
        sources = {
            "build/host-payload.py": "MANAGEMENT_FILES = {'configs/host_config.py': ('usr/lib/host_config.py', 420)}\nGUEST_BOOT_FILES = {'configs/storage.service': ('usr/lib/storage.service', 420)}\n",
            "build/maintenance-tools.py": "LEGACY_SOURCES = ('scripts/manual.py',)\nSOURCES = LEGACY_SOURCES\n",
            "configs/host_config.py": "PROFILE = 'manual-beta'\n",
            "configs/storage.service": "[Service]\nExecStart=prepare\n",
            "scripts/manual.py": "# Manual fixture source\n",
        }
        for name, data in sources.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data)
        (self.root / ".gitignore").write_text("out/\n")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Fixture")
        commit = self.git("rev-parse", "HEAD").decode().strip()
        self.base = self.root / "out/input"
        self.base.mkdir(parents=True)
        runtime = {"schema": 1, "guest_management_profile": "manual-beta", "files": {
            "root-A.ext4": {"bytes": 4096, "sha256": "a" * 64},
            "root-B.ext4": {"bytes": 4096, "sha256": "b" * 64},
            "provenance/host-payload.py": self.record(sources["build/host-payload.py"].encode()),
            "provenance/maintenance/build/maintenance-tools.py": self.record(sources["build/maintenance-tools.py"].encode())},
            "management_runtime": {"files": {"usr/lib/host_config.py": self.record(sources["configs/host_config.py"].encode())}},
            "guest_boot_runtime": {"files": {"usr/lib/storage.service": self.record(sources["configs/storage.service"].encode())}},
            "maintenance_runtime": {"files": {"usr/lib/a733-tools/scripts/manual.py": self.record(sources["scripts/manual.py"].encode()),
                                                "usr/lib/a733-tools/package.json": self.record(b'generated metadata')}},
            "sources": {"rootfs.tar.gz": {"bytes": 8192, "sha256": "c" * 64}, "Image": {"bytes": 1024, "sha256": "d" * 64}}}
        runtime_pin = self.write_json("runtime.json", runtime)
        self.source_pin = self.write_json("source-manifest.json", {"revision": commit, "files": PACKAGE.committed_records(self.root, commit)})
        self.spec = {"schema": 1, "source_commit": commit, "board_model": "Orange Pi 4 Pro",
                     "boot_profile": "spi-nvme-sd-absent", "guest_management_profile": "manual-beta",
                     "visibility": "private", "runtime_manifest": runtime_pin,
                     "hardware_compatibility": "fixture-board-revision-and-storage",
                     "manual_lifecycle_context": {"guest_fixture_sha256": "e" * 64, "storage_plan_sha256": "f" * 64},
                     "runtime_source": self.source_pin, "assets": [], "gates": {},
                     "source_distribution": self.assessment({"source_commit": commit})}
        for role in self.policy["required_roles"]:
            filename = role + ".img.gz"
            content = gzip.compress((role + " existing producer output\n").encode(), mtime=0)
            (self.base / filename).write_bytes(content)
            producer = {"schema": 1, "files": {filename: self.record(content)}}
            if role == "spi-firmware":
                producer = {"schema": 2, "candidate": self.record(content), "tool_revision": "c" * 40}
                location = "/candidate"
            else:
                location = "/files/" + filename
            if role == "nvme-install-image":
                producer.update(inputs={"roots_manifest": self.record((self.base / "runtime.json").read_bytes())},
                                partition_payloads={"root-a": runtime["files"]["root-A.ext4"],
                                                    "root-b": runtime["files"]["root-B.ext4"]},
                                boot_files={"boot/Image": runtime["sources"]["Image"],
                                            "boot/board.dtb": {"bytes": 100, "sha256": "1" * 64}})
            pin = self.write_json(role + ".json", producer)
            self.spec["assets"].append({"role": role, "producer": pin, "record": location,
                                        "file": filename, "distribution": self.assessment(
                                            {"sha256": PACKAGE.digest(content)}, private=role == "recovery-sd")})
        self.spec_path = self.base / "spec.json"
        self.save()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.PIPE)

    @staticmethod
    def record(data):
        return {"bytes": len(data), "sha256": PACKAGE.digest(data)}

    @staticmethod
    def assessment(binding, private=False):
        return {**binding, "assessed_by": "fixture operator", "basis": "Fixture review; no public grant",
                "personalized": private, "private_identity": private, "redistribution": "unresolved"}

    def write_json(self, name, value):
        path = self.base / name
        path.write_text(json.dumps(value, sort_keys=True) + "\n")
        return {"path": name, "sha256": PACKAGE.digest(path.read_bytes())}

    def save(self):
        self.spec_path.write_text(json.dumps(self.spec, indent=2))

    def inspect(self, candidate=True):
        self.save()
        return PACKAGE.inspect(self.root, self.spec_path, candidate)

    def complete_gates(self, reuse_spi=False):
        plan = self.inspect()
        for name, rule in self.policy["gates"].items():
            expected = plan["gates"][name]["expected_dependency_fingerprint"]
            kinds = rule.get("observations", ["cold"] * 3 + ["warm"] * 3)
            if rule.get("current_manual_profile"):
                kinds = kinds + ["manual-profile"]
            observations = []
            for number, kind in enumerate(kinds):
                filename = name + "-" + str(number) + ".log"
                raw = (name + ": actual fixture record " + str(number) + " " + kind).encode()
                (self.base / filename).write_bytes(raw)
                observations.append({"id": name + "-" + str(number), "kind": kind,
                                     "path": filename, "sha256": PACKAGE.digest(raw)})
                if kind == "manual-profile":
                    observations[-1]["runtime_fingerprint"] = plan["runtime_fingerprint"]
            receipt = self.write_json(name + "-receipt.json", {"gate": name, "scope": rule["scope"],
                "status": "PASS", "dependency_fingerprint": expected, "observations": observations})
            self.spec["gates"][name] = {"status": "PASS", "dependency_fingerprint": expected, "receipts": [receipt]}
            if name == "spi_boots" and reuse_spi:
                self.spec["gates"][name].update(status="REUSED", applicability={"assessed_by": "operator",
                    "basis": "Same pinned SPI/NVMe/runtime; retained observations apply", "dependency_fingerprint": expected})
        self.save()

    def update_producer(self, index, change):
        item = self.spec["assets"][index]
        path = self.base / item["producer"]["path"]
        value = json.loads(path.read_bytes())
        change(value)
        item["producer"] = self.write_json(path.name, value)

    def test_candidate_is_usable_and_keeps_restrictions(self):
        inspected = self.inspect()
        self.assertEqual(5, len(inspected["problems"]))
        output = self.root / "out/candidate"
        manifest = PACKAGE.assemble(self.root, inspected, output)
        self.assertEqual("candidate", manifest["state"])
        self.assertEqual("private", manifest["visibility"])
        self.assertTrue(manifest["assets"][1]["distribution"]["private_identity"])
        self.assertTrue((output / "operator/docs/manual-guest-guide.md").is_file())
        with tarfile.open(output / ("source/" + self.spec["source_commit"] + ".tar")) as archive:
            self.assertEqual((self.root / "README.md").read_bytes(), archive.extractfile("README.md").read())
        for line in (output / "SHA256SUMS").read_text().splitlines():
            expected, name = line.split("  ", 1)
            self.assertEqual(expected, PACKAGE.digest((output / name).read_bytes()))
        for entry in manifest["pinned_inputs"]:
            self.assertEqual(entry["sha256"], PACKAGE.digest((output / entry["file"]).read_bytes()))

    def test_release_requires_all_gates_and_credits_reused_six_boots(self):
        with self.assertRaisesRegex(PACKAGE.PackageError, "beta release blocked"):
            self.inspect(candidate=False)
        self.complete_gates(reuse_spi=True)
        inspected = self.inspect(candidate=False)
        self.assertEqual("REUSED", inspected["gates"]["spi_boots"]["status"])
        self.assertEqual(7, len(inspected["gates"]["spi_boots"]["observations"]))
        release = PACKAGE.assemble(self.root, inspected, self.root / "out/release")
        self.assertEqual("beta_ready", release["state"])

    def test_corrupt_producer_and_corrupt_artifact_refused(self):
        producer = self.base / self.spec["assets"][0]["producer"]["path"]
        original = producer.read_bytes()
        producer.write_bytes(original + b" ")
        with self.assertRaisesRegex(PACKAGE.PackageError, "pin mismatch"):
            self.inspect()
        producer.write_bytes(original)
        artifact = self.base / self.spec["assets"][0]["file"]
        raw = artifact.read_bytes()
        artifact.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        inspected = self.inspect()  # Planning deliberately does not hash large images.
        output = self.root / "out/corrupt"
        with self.assertRaisesRegex(PACKAGE.PackageError, "SHA256 mismatch"):
            PACKAGE.assemble(self.root, inspected, output)
        self.assertFalse(output.exists())

    def test_profile_must_be_explicit_and_nvme_roots_must_match(self):
        runtime_path = self.base / "runtime.json"
        runtime = json.loads(runtime_path.read_bytes())
        del runtime["guest_management_profile"]
        self.spec["runtime_manifest"] = self.write_json("runtime.json", runtime)
        with self.assertRaisesRegex(PACKAGE.PackageError, "guest_management_profile"):
            self.inspect()
        runtime["guest_management_profile"] = "manual-beta"
        self.spec["runtime_manifest"] = self.write_json("runtime.json", runtime)
        self.update_producer(2, lambda p: p["inputs"]["roots_manifest"].update(sha256="0" * 64))
        with self.assertRaisesRegex(PACKAGE.PackageError, "NVMe producer"):
            self.inspect()

    def test_source_commit_cleanliness_and_runtime_source_pin(self):
        self.spec["source_commit"] = "0" * 40
        with self.assertRaisesRegex(PACKAGE.PackageError, "HEAD"):
            self.inspect()
        self.spec["source_commit"] = self.git("rev-parse", "HEAD").decode().strip()
        (self.root / "README.md").write_text("dirty")
        with self.assertRaisesRegex(PACKAGE.PackageError, "clean"):
            self.inspect()
        self.git("checkout", "--", "README.md")
        source = json.loads((self.base / "source-manifest.json").read_bytes())
        source["files"]["README.md"]["sha256"] = "f" * 64
        self.spec["runtime_source"] = self.write_json("source-manifest.json", source)
        with self.assertRaisesRegex(PACKAGE.PackageError, "differs from committed"):
            self.inspect()

    def test_public_refuses_private_identity_and_unresolved_grants(self):
        self.spec["visibility"] = "public"
        # First asset can be cleared, but personalized recovery still blocks.
        grant = self.write_json("grant.json", {"grant": "fixture assertion"})
        first = self.spec["assets"][0]["distribution"]
        first.update(redistribution="granted", grant=grant)
        with self.assertRaisesRegex(PACKAGE.PackageError, "personalized/private"):
            self.inspect()
        self.spec["assets"][1]["distribution"].update(personalized=False, private_identity=False)
        with self.assertRaisesRegex(PACKAGE.PackageError, "redistribution grant"):
            self.inspect()
        for asset in self.spec["assets"]:
            asset["distribution"].update(personalized=False, private_identity=False,
                                          redistribution="granted", grant=grant)
        self.spec["source_distribution"].update(redistribution="granted", grant=grant)
        self.spec["visibility"] = "private"
        self.spec["metadata_distribution"] = {**self.assessment({"sha256": "0" * 64}),
                                                "redistribution": "granted", "grant": grant}
        planned = self.inspect()
        self.spec["metadata_distribution"]["sha256"] = planned["metadata_identity"]
        self.spec["visibility"] = "public"
        cleared = self.inspect()
        manifest = PACKAGE.assemble(self.root, cleared, self.root / "out/public-candidate")
        self.assertEqual("public", manifest["visibility"])
        self.assertTrue(manifest["eligibility_is_operator_assertion"])
        self.spec["assets"][0]["distribution"]["basis"] = "Changed emitted metadata declaration"
        with self.assertRaisesRegex(PACKAGE.PackageError, "assessment identity mismatch"):
            self.inspect()

    def test_traversal_symlinks_missing_roles_and_overwrite_refused(self):
        original = self.spec["assets"][0]["file"]
        self.spec["assets"][0]["file"] = "../escape"
        with self.assertRaisesRegex(PACKAGE.PackageError, "traversing"):
            self.inspect()
        self.spec["assets"][0]["file"] = original
        raw = (self.base / original).read_bytes()
        (self.base / "real.bin").write_bytes(raw)
        (self.base / original).unlink()
        (self.base / original).symlink_to("real.bin")
        with self.assertRaisesRegex(PACKAGE.PackageError, "symlink"):
            self.inspect()
        (self.base / original).unlink()
        (self.base / original).write_bytes(raw)
        inspected = self.inspect()
        output = self.root / "out/existing"
        output.mkdir()
        (output / "keep").write_text("preserved")
        with self.assertRaisesRegex(PACKAGE.PackageError, "NEW"):
            PACKAGE.assemble(self.root, inspected, output)
        self.assertEqual("preserved", (output / "keep").read_text())
        self.spec["assets"].pop()
        with self.assertRaisesRegex(PACKAGE.PackageError, "missing required"):
            self.inspect()

    def test_missing_boot_credit_duplicate_observations_and_stale_reuse_block(self):
        self.complete_gates(reuse_spi=True)
        gate = self.spec["gates"]["spi_boots"]
        path = self.base / gate["receipts"][0]["path"]
        original = json.loads(path.read_bytes())
        incomplete = copy.deepcopy(original)
        incomplete["observations"].pop(5)
        gate["receipts"][0] = self.write_json(path.name, incomplete)
        with self.assertRaisesRegex(PACKAGE.PackageError, "insufficient warm"):
            self.inspect(candidate=False)
        gate["receipts"][0] = self.write_json(path.name, original)
        gate["applicability"]["dependency_fingerprint"] = "0" * 64
        with self.assertRaisesRegex(PACKAGE.PackageError, "applicability"):
            self.inspect(candidate=False)
        gate["applicability"]["dependency_fingerprint"] = gate["dependency_fingerprint"]
        original["observations"][1]["id"] = original["observations"][0]["id"]
        gate["receipts"][0] = self.write_json(path.name, original)
        with self.assertRaisesRegex(PACKAGE.PackageError, "duplicate observation"):
            self.inspect(candidate=False)

    def test_arbitrary_pass_without_scoped_raw_evidence_blocked(self):
        self.complete_gates()
        gate = self.spec["gates"]["software"]
        bad = {"status": "PASS", "gate": "software", "dependency_fingerprint": gate["dependency_fingerprint"]}
        gate["receipts"] = [self.write_json("bad.json", bad)]
        with self.assertRaisesRegex(PACKAGE.PackageError, "scope"):
            self.inspect(candidate=False)
        bad.update(scope="host-beta", observations=[])
        gate["receipts"] = [self.write_json("bad.json", bad)]
        with self.assertRaisesRegex(PACKAGE.PackageError, "pinned observations"):
            self.inspect(candidate=False)

    def test_plan_is_read_only_and_candidate_does_not_upgrade(self):
        output = self.root / "out/plan-output"
        with mock.patch.object(PACKAGE.sys, "stdout", new_callable=io.StringIO) as captured:
            self.assertEqual(0, PACKAGE.main(["--project", str(self.root), "--spec", str(self.spec_path),
                                             "--output", str(output), "--plan"]))
            self.assertTrue(json.loads(captured.getvalue())["read_only"])
        self.assertFalse(output.exists())
        self.complete_gates()
        manifest = PACKAGE.assemble(self.root, self.inspect(candidate=True), output)
        self.assertEqual("candidate", manifest["state"])

    def test_existing_spi_revision_and_optional_root_update(self):
        self.assertEqual("c" * 40, json.loads((self.base / "spi-firmware.json").read_bytes())["tool_revision"])
        inspected = self.inspect()
        self.assertFalse(any("source" in problem for problem in inspected["problems"]))
        content = b"tiny root update"
        (self.base / "root-A.ext4.gz").write_bytes(content)
        item = {"role": "root-update-a", "producer": self.spec["assets"][0]["producer"],
                "record": "/candidate", "file": self.spec["assets"][0]["file"],
                "distribution": self.assessment({"sha256": PACKAGE.digest(content)})}
        self.spec["assets"].append(item)
        with self.assertRaisesRegex(PACKAGE.PackageError, "root-update producer"):
            self.inspect()

    def test_complete_source_and_runtime_provenance_are_required(self):
        source = json.loads((self.base / "source-manifest.json").read_bytes())
        incomplete = copy.deepcopy(source)
        incomplete["files"] = {"README.md": source["files"]["README.md"]}
        self.spec["runtime_source"] = self.write_json("source-manifest.json", incomplete)
        with self.assertRaisesRegex(PACKAGE.PackageError, "complete committed snapshot"):
            self.inspect()
        self.spec["runtime_source"] = self.write_json("source-manifest.json", source)
        runtime = json.loads((self.base / "runtime.json").read_bytes())
        runtime["management_runtime"]["files"]["usr/lib/host_config.py"]["sha256"] = "0" * 64
        self.spec["runtime_manifest"] = self.write_json("runtime.json", runtime)
        with self.assertRaisesRegex(PACKAGE.PackageError, "runtime provenance differs"):
            self.inspect()

    def test_docs_only_release_advancement_reuses_build_and_gate_dependencies(self):
        self.complete_gates(reuse_spi=True)
        before = self.inspect(candidate=False)
        old_build = before["runtime_source"]["revision"]
        (self.root / "README.md").write_text("Updated operator prose\n")
        self.git("add", "README.md")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Docs")
        commit = self.git("rev-parse", "HEAD").decode().strip()
        self.spec["source_commit"] = commit
        self.spec["source_distribution"]["source_commit"] = commit
        after = self.inspect(candidate=False)
        self.assertEqual(old_build, after["runtime_source"]["revision"])
        for name in self.policy["gates"]:
            self.assertEqual(before["gates"][name]["expected_dependency_fingerprint"],
                             after["gates"][name]["expected_dependency_fingerprint"])
        # A changed packaged source cannot borrow the previous runtime build.
        (self.root / "scripts/manual.py").write_text("# Changed runtime\n")
        self.git("add", "scripts/manual.py")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Runtime")
        self.spec["source_commit"] = self.git("rev-parse", "HEAD").decode().strip()
        self.spec["source_distribution"]["source_commit"] = self.spec["source_commit"]
        with self.assertRaisesRegex(PACKAGE.PackageError, "runtime source differs"):
            self.inspect()

    def test_manual_restart_persistence_and_final_profile_observation_required(self):
        self.complete_gates()
        for kind in ("restart", "persistence"):
            gate = self.spec["gates"]["manual_kvm"]
            original = json.loads((self.base / gate["receipts"][0]["path"]).read_bytes())
            changed = copy.deepcopy(original)
            changed["observations"] = [o for o in original["observations"] if o["kind"] != kind]
            gate["receipts"] = [self.write_json("manual_kvm-receipt.json", changed)]
            with self.assertRaisesRegex(PACKAGE.PackageError, "missing observation kind: " + kind):
                self.inspect(candidate=False)
            gate["receipts"] = [self.write_json("manual_kvm-receipt.json", original)]
        gate = self.spec["gates"]["spi_boots"]
        receipt = json.loads((self.base / gate["receipts"][0]["path"]).read_bytes())
        receipt["observations"][-1]["runtime_fingerprint"] = "0" * 64
        gate["receipts"] = [self.write_json("spi_boots-receipt.json", receipt)]
        with self.assertRaisesRegex(PACKAGE.PackageError, "final manual-profile"):
            self.inspect(candidate=False)

    def test_spi_credit_uses_boot_components_and_retained_records(self):
        before = self.inspect()
        runtime = json.loads((self.base / "runtime.json").read_bytes())
        runtime["files"]["root-A.ext4"]["sha256"] = "2" * 64
        self.spec["runtime_manifest"] = self.write_json("runtime.json", runtime)
        self.update_producer(2, lambda p: p.update(
            inputs={"roots_manifest": self.record((self.base / "runtime.json").read_bytes())},
            partition_payloads={"root-a": runtime["files"]["root-A.ext4"], "root-b": runtime["files"]["root-B.ext4"]}))
        after = self.inspect()
        self.assertEqual(before["gates"]["spi_boots"]["expected_dependency_fingerprint"],
                         after["gates"]["spi_boots"]["expected_dependency_fingerprint"])
        self.complete_gates(reuse_spi=True)
        self.assertEqual("REUSED", self.inspect(candidate=False)["gates"]["spi_boots"]["status"])

    def test_root_update_slot_cannot_select_other_runtime_file(self):
        self.spec["assets"].append({"role": "root-update-a", "producer": self.spec["runtime_manifest"],
                                   "record": "/files/root-B.ext4", "file": "unused",
                                   "distribution": self.assessment({"sha256": "b" * 64})})
        with self.assertRaisesRegex(PACKAGE.PackageError, "matching root image"):
            self.inspect()


if __name__ == "__main__":
    unittest.main()

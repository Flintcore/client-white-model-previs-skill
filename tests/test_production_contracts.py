"""Isolated production-contract regressions, not client/media acceptance.

The job/gate fixtures contain synthetic bytes and synthetic report fields.
current_revision and media.verify_native are mocked only for those contract
tests. Their positive cases certify metadata routing, not Blender geometry,
decoded pixels, human viewing, or a real delivery. Installer tests instead use
actual temporary local Git commits and actual file hashing/backup operations.
No client assets, installed skills, credentials, remote Git or global config
are used.
"""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "skills" / "client-white-model-previs" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import common
import gate
import job
import media
import team_queue

installer_spec = importlib.util.spec_from_file_location(
    "client_previs_contract_installer", REPO / "tools" / "install_skill.py"
)
installer = importlib.util.module_from_spec(installer_spec)
installer_spec.loader.exec_module(installer)
PIN = "1" * 40


class ContractFixture(unittest.TestCase):
    """Metadata fixture only; source/model/video bytes are not usable media."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="previs-contract-only-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.revision_mock = mock.patch.object(common, "current_revision", return_value=PIN)
        self.revision_mock.start()
        self.addCleanup(self.revision_mock.stop)
        inputs = self.root / "inputs"
        inputs.mkdir()
        (inputs / "source.mp4").write_bytes(b"CONTRACT_ONLY_SOURCE_NOT_VIDEO\n")
        (inputs / "template.blend").write_bytes(b"CONTRACT_ONLY_TEMPLATE_NOT_BLEND\n")
        source = {
            "path": "inputs/source.mp4", "sha256": common.sha256(inputs / "source.mp4"),
            "width": 3840, "height": 2160, "fps_num": 24, "fps_den": 1,
            "frame_count": 240, "audio_present": False,
        }
        template = {
            "path": "inputs/template.blend", "sha256": common.sha256(inputs / "template.blend"),
            "level": "L3",
        }
        render = {"width": 3840, "height": 2160, "percentage": 100, "samples": 64, "dark_scene": False}
        timeline = {"frame_start": 1, "frame_end": 240}
        task = {
            "source_sha256": source["sha256"], "template_sha256": template["sha256"],
            "standards_sha256": common.sha256(common.RULES_PATH), "skill_revision": PIN,
            "spec": {"render": copy.deepcopy(render), "fps": "24/1", "profile": "client-4k",
                     "palette_decision": "contract-only gray", "uniform_character_level": "L3"},
            "timeline": copy.deepcopy(timeline),
        }
        task["job_id"] = team_queue.task_id(task)
        self.job_data = {
            "schema": "client-white-model-job.v1", "job_id": task["job_id"],
            "standards_version": common.VERSION, "standards_sha256": task["standards_sha256"],
            "skill_revision": PIN, "producer": "fixture-worker", "project_name": "Fixture",
            "profile": "client-4k", "palette_decision": "contract-only gray", "source": source,
            "template": template, "render": render, "timeline": timeline, "queue_task": task,
            "scene": {"shots": [{"id": "shot_001", "start": 1, "end": 240}]},
        }
        self.job_path = self.root / "job.json"
        self.save_job()

    def save_job(self):
        common.write_json(self.job_path, self.job_data)

    def assert_job_rejected(self, message):
        self.save_job()
        with self.assertRaisesRegex(ValueError, message):
            common.load_job(self.job_path)

    def make_bundle(self):
        """Build otherwise-valid synthetic gate metadata; native verification is mocked."""
        folder = self.root / "delivery" / "Fixture"
        folder.mkdir(parents=True)
        self.media_paths = {}
        for kind, name in [("original", "Fixture.mp4"), ("white", "Fixture 白模.mp4"),
                           ("comparison", "Fixture 对比.mp4"), ("blend", "Fixture.blend")]:
            path = folder / name
            if kind == "original":
                shutil.copyfile(self.root / self.job_data["source"]["path"], path)
            else:
                path.write_bytes(("CONTRACT_ONLY_" + kind.upper() + "\n").encode())
            self.media_paths[kind] = path
        self.inputs_path = self.root / "inputs_report.json"
        self.blender_path = self.root / "blender-final.json"
        self.media_path = self.root / "media.json"
        self.review_path = self.root / "visual-review.json"
        native = self.root / "renders" / "render_manifest.json"
        self.native_path = native
        binding = {"job_id": self.job_data["job_id"], "standards_sha256": self.job_data["standards_sha256"],
                   "skill_revision": PIN, "scope": "CONTRACT_ONLY_NOT_PRODUCTION_EVIDENCE"}
        common.write_json(self.inputs_path, {
            **binding, "schema": "client-white-model-inputs.v1", "passed": True,
            "source_sha256": self.job_data["source"]["sha256"],
            "template_sha256": self.job_data["template"]["sha256"],
        })
        self.blender_data = {
            **binding, "schema": "client-white-model-blender-qa.v1", "passed": True,
            "blend_sha256": common.sha256(self.media_paths["blend"]),
            "render_evidence": {"passed": True},
            "final_bundle_portability": {"applicable": True, "passed": True},
        }
        common.write_json(self.blender_path, self.blender_data)
        common.write_json(native, {
            **binding, "status": "COMPLETE", "blend_sha256": common.sha256(self.media_paths["blend"]),
        })
        self.media_data = {
            **binding, "schema": "client-white-model-media.v1", "passed": True,
            "blend_sha256": common.sha256(self.media_paths["blend"]),
            "source_sha256": self.job_data["source"]["sha256"],
            "artifacts": [common.artifact(path, kind) for kind, path in self.media_paths.items()],
            "native_render": common.artifact(native, "renders"),
        }
        common.write_json(self.media_path, self.media_data)
        evidence_paths = {"inputs": self.inputs_path, "blender": self.blender_path, "media": self.media_path,
                          "renders": native, "visual": self.media_paths["comparison"], "integrity": self.job_path}
        self.review_data = {
            **binding, "schema": "client-white-model-review.v1", "producer": self.job_data["producer"],
            "reviewer": {"id": "fixture-reviewer", "kind": "independent_reviewer"},
            "source_sha256": self.job_data["source"]["sha256"],
            "blend_sha256": common.sha256(self.media_paths["blend"]),
            "white_sha256": common.sha256(self.media_paths["white"]),
            "comparison_sha256": common.sha256(self.media_paths["comparison"]),
            "all_frames_reviewed": True, "reviewed_frame_count": 240,
            "playback_reviewed": True, "last_second_reviewed": True, "client_acceptance": False,
            "rule_results": [
                {"id": rule["id"], "status": "pass", "shot_ids": ["shot_001"],
                 "observations": "CONTRACT FIXTURE ONLY: routing assertion, no actual viewing occurred.",
                 "evidence": [common.artifact(evidence_paths[kind], kind) for kind in rule["evidence_kinds"]]}
                for rule in common.blocking_rules()
            ],
        }
        common.write_json(self.review_path, self.review_data)

    def check_bundle(self):
        common.write_json(self.review_path, self.review_data)
        with mock.patch.object(media, "verify_native", return_value={"scope": "CONTRACT_ONLY"}) as native_mock:
            result = gate.verify_bundle(self.job_path, self.blender_path, self.media_path, self.review_path)
            native_mock.assert_called_once()
        return result

    def assert_gate_issue(self, text):
        report = self.check_bundle()
        self.assertFalse(report["passed"], report)
        self.assertFalse(report["client_acceptance"])
        self.assertTrue(any(text in issue for issue in report["issues"]), report)
        return report


class LockedJobContractTests(ContractFixture):
    def test_consistent_job_loads_under_mocked_release(self):
        loaded, root = common.load_job(self.job_path)
        self.assertEqual(loaded["job_id"], team_queue.task_id(loaded["queue_task"]))
        # Windows TEMP can use an 8.3 alias; macOS /var aliases /private/var.
        # The loader intentionally resolves physical bounded paths.
        self.assertEqual(root, self.root.resolve())

    def test_fps_change_without_queue_spec_is_rejected(self):
        self.job_data["source"]["fps_num"] = 25
        self.assert_job_rejected("immutable queue task")

    def test_palette_change_without_queue_spec_is_rejected(self):
        self.job_data["palette_decision"] = "modified after pin"
        self.assert_job_rejected("immutable queue task")

    def test_template_sha_replacement_with_old_task_is_rejected(self):
        path = self.root / self.job_data["template"]["path"]
        path.write_bytes(b"changed synthetic template bytes\n")
        self.job_data["template"]["sha256"] = common.sha256(path)
        self.assert_job_rejected("immutable queue task")

    def test_job_revision_must_equal_current_release(self):
        self.job_data["skill_revision"] = "2" * 40
        self.assert_job_rejected("another skill revision")

    def test_task_digest_changes_even_if_new_fps_specs_match(self):
        self.job_data["source"]["fps_num"] = 25
        self.job_data["queue_task"]["spec"]["fps"] = "25/1"
        self.assert_job_rejected("Deterministic task identity changed")

    def test_queue_task_embedded_job_id_is_checked(self):
        self.job_data["queue_task"]["job_id"] = "0" * 64
        self.assert_job_rejected("Deterministic task identity changed")

    def test_input_bytes_with_old_digest_are_rejected(self):
        (self.root / self.job_data["source"]["path"]).write_bytes(b"changed source\n")
        self.assert_job_rejected("Asset identity mismatch: source")

    def test_old_lod_metadata_is_rejected(self):
        self.job_data["template"]["level"] = "L1"
        self.assert_job_rejected("uniform L3")


class ReviewWorksheetContractTests(ContractFixture):
    def test_worksheet_keeps_all_123_rules_unverified_and_all_viewing_flags_false(self):
        output = self.root / "worksheet.json"
        result = job.review_worksheet(self.job_path, output, "independent-fixture-reviewer")
        worksheet = common.read_json(output)
        required = {row["id"] for row in common.blocking_rules()}
        self.assertEqual(len(required), 123)
        self.assertEqual(result["unverified_blocking_rules"], 123)
        self.assertEqual({row["id"] for row in worksheet["rule_results"]}, required)
        self.assertTrue(all(row["status"] == "unverified" for row in worksheet["rule_results"]))
        self.assertTrue(all(row["evidence"] == [] and row["observations"] == "" for row in worksheet["rule_results"]))
        self.assertTrue(all(row["shot_ids"] == ["shot_001"] for row in worksheet["rule_results"]))
        for name in ["all_frames_reviewed", "playback_reviewed", "last_second_reviewed", "client_acceptance"]:
            self.assertIs(worksheet[name], False)
        self.assertEqual(worksheet["reviewed_frame_count"], 0)
        for name in ["blend_sha256", "white_sha256", "comparison_sha256"]:
            self.assertIsNone(worksheet[name])


class DeliveryGateContractTests(ContractFixture):
    def setUp(self):
        super().setUp()
        self.make_bundle()

    def test_positive_contract_is_internal_only_and_does_not_claim_actual_native_validation(self):
        report = self.check_bundle()
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["blocking_rule_count"], 123)
        self.assertEqual(report["reviewed_rule_count"], 123)
        self.assertFalse(report["client_acceptance"])
        self.assertIn("NOT_CLIENT_ACCEPTANCE", report["scope"])

    def test_missing_blocking_rule_is_rejected(self):
        missing = self.review_data["rule_results"].pop()["id"]
        self.assert_gate_issue("Missing rule " + missing)

    def test_producer_self_review_is_rejected(self):
        self.review_data["reviewer"]["id"] = self.job_data["producer"]
        self.assert_gate_issue("Producer may not self-approve")

    def test_reviewer_role_must_be_independent_reviewer(self):
        self.review_data["reviewer"]["kind"] = "producer"
        self.assert_gate_issue("Independent reviewer identity required")

    def test_stale_visual_scene_hash_is_rejected(self):
        self.review_data["blend_sha256"] = "0" * 64
        self.assert_gate_issue("Visual review not bound to current blend_sha256")

    def test_changed_delivered_artifact_is_rejected(self):
        self.media_paths["white"].write_bytes(b"changed after report\n")
        self.assert_gate_issue("Changed artifact: white")

    def test_unrelated_hashed_file_does_not_satisfy_evidence_kind(self):
        unrelated = self.root / "unrelated.txt"
        unrelated.write_text("unrelated contract fixture\n", encoding="utf-8")
        baseline = copy.deepcopy(self.review_data)
        for kind in ["inputs", "blender", "media", "renders", "visual", "integrity"]:
            with self.subTest(kind=kind):
                self.review_data = copy.deepcopy(baseline)
                row = next(row for row in self.review_data["rule_results"]
                           if any(item["kind"] == kind for item in row["evidence"]))
                index = next(i for i, item in enumerate(row["evidence"]) if item["kind"] == kind)
                row["evidence"][index] = common.artifact(unrelated, kind)
                self.assert_gate_issue("evidence kind is not bound to the actual current artifact")

    def test_wrong_final_filename_is_rejected_even_when_bytes_match(self):
        replacement = self.root / "wrong-name.mp4"
        shutil.copyfile(self.media_paths["white"], replacement)
        item = next(item for item in self.media_data["artifacts"] if item["kind"] == "white")
        item["path"] = str(replacement)
        common.write_json(self.media_path, self.media_data)
        self.assert_gate_issue("Wrong final bundle filename/location: white")

    def test_duplicate_delivery_artifact_is_rejected(self):
        self.media_data["artifacts"].append(copy.deepcopy(self.media_data["artifacts"][0]))
        common.write_json(self.media_path, self.media_data)
        self.assert_gate_issue("Duplicate delivered artifact: original")

    def test_missing_final_bundle_portability_is_rejected(self):
        self.blender_data.pop("final_bundle_portability")
        common.write_json(self.blender_path, self.blender_data)
        self.assert_gate_issue("dependencies were not verified portable")

    def test_failed_final_bundle_portability_is_rejected(self):
        self.blender_data["final_bundle_portability"]["passed"] = False
        common.write_json(self.blender_path, self.blender_data)
        self.assert_gate_issue("dependencies were not verified portable")

    def test_missing_required_evidence_kind_is_rejected(self):
        self.review_data["rule_results"][0]["evidence"].pop()
        self.assert_gate_issue("incomplete rule evidence kinds")

    def test_missing_full_review_flags_is_rejected(self):
        self.review_data["last_second_reviewed"] = False
        self.assert_gate_issue("last-second review is missing")

    def test_duplicate_rule_result_is_rejected(self):
        self.review_data["rule_results"].append(copy.deepcopy(self.review_data["rule_results"][0]))
        self.assert_gate_issue("Duplicate rule result")

    def test_unconditional_rule_is_not_exempted_by_rationale(self):
        unconditional = next(rule["id"] for rule in common.blocking_rules() if not rule["conditional"])
        row = next(row for row in self.review_data["rule_results"] if row["id"] == unconditional)
        row.update(status="not_applicable", rationale="synthetic fixture assertion", source_frames=[1])
        self.assert_gate_issue("unsupported applicability exemption")

    def test_conditional_exemption_with_typed_in_scope_frames_satisfies_contract_only(self):
        conditional = next(rule["id"] for rule in common.blocking_rules() if rule["conditional"])
        row = next(row for row in self.review_data["rule_results"] if row["id"] == conditional)
        row.update(status="not_applicable", rationale="CONTRACT ONLY: fabricated applicability fixture.", source_frames=[1, 240])
        result = self.check_bundle()
        self.assertTrue(result["passed"], result)
        self.assertFalse(result["client_acceptance"])

    def test_conditional_exemption_needs_integer_frames_in_assigned_scope(self):
        baseline = copy.deepcopy(self.review_data)
        conditional = next(rule["id"] for rule in common.blocking_rules() if rule["conditional"])
        for frames in [[0], [241], ["1"], [True], "1"]:
            with self.subTest(source_frames=frames):
                self.review_data = copy.deepcopy(baseline)
                row = next(row for row in self.review_data["rule_results"] if row["id"] == conditional)
                row.update(status="not_applicable", rationale="CONTRACT ONLY applicability assertion.", source_frames=frames)
                self.assert_gate_issue("applicability evidence must name actual assigned source frames")

    def test_missing_report_fails_closed_without_synthetic_passing_stub(self):
        self.media_path.unlink()
        with self.assertRaises(FileNotFoundError):
            gate.verify_bundle(self.job_path, self.blender_path, self.media_path, self.review_path)


@unittest.skipUnless(shutil.which("git"), "Local Git is required for actual isolated installer tests")
class InstallerLocalGitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="previs-installer-local-git-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "source-repository"
        self.source = self.repository / "skills" / installer.NAME
        (self.source / "references").mkdir(parents=True)
        self.skill_file = self.source / "SKILL.md"
        self.skill_file.write_text("---\nname: client-white-model-previs\ndescription: Contract fixture\n---\n# Synthetic fixture\n", encoding="utf-8")
        shutil.copyfile(common.RULES_PATH, self.source / "references" / "standards.json")
        self.target = self.root / "isolated-install" / "skills" / installer.NAME
        self.git("init", "-q")
        self.git("config", "user.name", "Contract Fixture")
        self.git("config", "user.email", "contract-fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        hooks = self.root / "empty-hooks"
        hooks.mkdir()
        self.git("config", "core.hooksPath", str(hooks))
        self.revision = self.commit("Initial isolated fixture")

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repository), *args], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def test_install_and_verify_actual_clean_head(self):
        result = installer.install(self.repository, self.target, revision=self.revision)
        self.assertEqual(result["revision"], self.revision)
        self.assertTrue(result["all_file_hashes_match"])
        self.assertIsNone(result["backup"])
        verified = installer.verify(self.target)
        self.assertTrue(verified["verified"])
        self.assertEqual(verified["revision"], self.revision)

    def test_arbitrary_explicit_revision_is_rejected_without_install(self):
        wrong = "0" * 40 if self.revision != "0" * 40 else "1" * 40
        with self.assertRaisesRegex(ValueError, "differs from actual checkout HEAD"):
            installer.install(self.repository, self.target, revision=wrong)
        self.assertFalse(self.target.exists())

    def test_dirty_source_skill_is_rejected_without_install(self):
        self.skill_file.write_text(self.skill_file.read_text(encoding="utf-8") + "dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "skill edits"):
            installer.install(self.repository, self.target)
        self.assertFalse(self.target.exists())

    def test_tampered_install_is_detected(self):
        installer.install(self.repository, self.target)
        (self.target / "SKILL.md").write_text("tampered installed fixture\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed or has extra/missing files"):
            installer.verify(self.target)

    def test_update_preserves_customized_prior_install_outside_skills(self):
        installer.install(self.repository, self.target)
        old_receipt = (self.target / "installation.json").read_bytes()
        local = self.target / "local-note.txt"
        local.write_text("custom local fixture content\n", encoding="utf-8")
        (self.target / "SKILL.md").write_text("customized prior skill fixture\n", encoding="utf-8")
        old_files = installer.files(self.target)
        self.skill_file.write_text(self.skill_file.read_text(encoding="utf-8") + "second release\n", encoding="utf-8")
        new_revision = self.commit("Second isolated fixture")
        self.assertNotEqual(new_revision, self.revision)
        result = installer.install(self.repository, self.target, update=True, revision=new_revision)
        backup = Path(result["backup"])
        self.assertEqual(backup.parent.name, "skill-backups")
        self.assertNotIn(self.target.parent, backup.parents)
        self.assertEqual(installer.files(backup), old_files)
        self.assertEqual((backup / "installation.json").read_bytes(), old_receipt)
        self.assertEqual(installer.files(self.target), installer.files(self.source))
        self.assertFalse((self.target / "local-note.txt").exists())
        self.assertEqual(installer.verify(self.target)["revision"], new_revision)

    def test_existing_install_requires_explicit_update_and_remains_intact(self):
        installer.install(self.repository, self.target)
        before = installer.files(self.target)
        with self.assertRaisesRegex(ValueError, "Installed skill exists"):
            installer.install(self.repository, self.target)
        self.assertEqual(installer.files(self.target), before)
        self.assertTrue(installer.verify(self.target)["verified"])

    def test_bad_receipt_schema_is_rejected(self):
        installer.install(self.repository, self.target)
        path = self.target / "installation.json"
        receipt = json.loads(path.read_text(encoding="utf-8"))
        receipt["schema"] = "unrecognized-fixture"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unsupported installation receipt"):
            installer.verify(self.target)

    def test_common_revision_uses_byte_verified_installed_receipt(self):
        installer.install(self.repository, self.target)
        with mock.patch.object(common, "SKILL_ROOT", self.target), mock.patch.object(
            common, "RULES_PATH", self.target / "references" / "standards.json"
        ):
            self.assertEqual(common.current_revision(), self.revision)
            (self.target / "SKILL.md").write_text("changed after receipt\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "bytes differ"):
                common.current_revision()


if __name__ == "__main__":
    unittest.main()

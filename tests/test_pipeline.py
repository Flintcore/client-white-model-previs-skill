"""Actual temporary files and measured timers; no animation/visual acceptance.

Cache/DAG tests mock only the special quality validators, then separate tests
assert that preview/native/final gates are delegated and failures stay closed.
Source/model bytes are synthetic metadata fixtures, not playable client media.
"""
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import types
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "client-white-model-previs" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import pipeline
from test_production_contracts import ContractFixture


class PipelineTests(ContractFixture):
    def setUp(self):
        super().setUp()
        self.config_path = self.root / "pipeline.json"
        pipeline.init_pipeline(self.job_path)
        config = common.read_json(self.config_path)
        code = self.root / "inputs" / "fixture_solver.py"
        code.write_text("# CONTRACT_ONLY_NOT_A_SOLVER\npass\n", encoding="utf-8")
        for stage in pipeline.STAGES:
            setting = self.root / "inputs" / (stage + ".json")
            setting.write_text(json.dumps({"stage": stage, "setting": 1}), encoding="utf-8")
            config["stages"][stage] = {"algorithm": {"name": "fixture-" + stage,
                "path": "inputs/fixture_solver.py", "sha256": common.sha256(code)}, "parameters": {"mode": "fixture"},
                "inputs": [setting.relative_to(self.root).as_posix()]}
        common.write_json(self.config_path, config)
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()

    def _config(self, change):
        config = common.read_json(self.config_path)
        change(config)
        common.write_json(self.config_path, config)

    def _rows(self):
        return {row["stage"]: row for row in pipeline.plan(self.job_path)["stages"]}

    def _finish(self, stage):
        token = pipeline.start_stage(self.job_path, stage)["token"]
        file = self.artifacts / (stage + ".bin")
        file.write_bytes(("ACTUAL_FIXTURE_OUTPUT_" + stage).encode())
        # This isolates deterministic cache behaviour from separately tested
        # real preview/render/media/final validators, not a claimed quality pass.
        with mock.patch.object(pipeline, "_validate_evidence", return_value={}):
            result = pipeline.record_stage(self.job_path, token, "passed", [file])
        self.assertEqual(result["status"], "passed")
        return result

    def _complete_to(self, end):
        with mock.patch.object(pipeline, "_validate_evidence", return_value={}):
            for stage in pipeline.STAGES:
                self._finish(stage)
                if stage == end:
                    break

    def _receipt_path(self, stage):
        with mock.patch.object(pipeline, "_validate_evidence", return_value={}):
            row = self._rows()[stage]
        return self.root / ".pipeline" / "cache" / stage / (row["key"] + ".json")

    def test_init_unconfigured_and_never_marks_quality_pass(self):
        self.config_path.unlink()
        result = pipeline.init_pipeline(self.job_path)
        self.assertFalse(result["client_acceptance"])
        report = pipeline.plan(self.job_path)
        self.assertEqual(report["next_action"]["stage"], "observations")
        self.assertEqual(report["next_action"]["action"], "configuration_required")
        self.assertFalse(report["client_acceptance"])

    def test_existing_pipeline_preserved(self):
        before = self.config_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Existing pipeline preserved"):
            pipeline.init_pipeline(self.job_path)
        self.assertEqual(before, self.config_path.read_bytes())

    def test_new_plan_only_first_stage_runnable(self):
        rows = self._rows()
        self.assertEqual(rows["observations"]["status"], "run")
        self.assertTrue(all(rows[name]["status"] == "blocked" for name in pipeline.STAGES[1:]))

    def test_dependencies_block_start(self):
        with self.assertRaisesRegex(ValueError, "dependencies"):
            pipeline.start_stage(self.job_path, "camera")

    def test_actual_sha_cache_hit_not_fileexists(self):
        self._finish("observations")
        before = self._rows()["observations"]
        self.assertEqual(before["status"], "cache_hit")
        (self.artifacts / "observations.bin").write_bytes(b"DIFFERENT_BYTES_SAME_FILE")
        rows = self._rows()
        self.assertEqual(rows["observations"]["status"], "repair")
        self.assertEqual(rows["camera"]["status"], "blocked")

    def test_missing_output_is_repair(self):
        self._finish("observations")
        (self.artifacts / "observations.bin").unlink()
        self.assertEqual(self._rows()["observations"]["status"], "repair")

    def test_empty_partial_output_is_repair(self):
        self._finish("observations")
        (self.artifacts / "observations.bin").write_bytes(b"")
        self.assertEqual(self._rows()["observations"]["status"], "repair")

    def test_malformed_receipt_is_repair(self):
        self._finish("observations")
        self._receipt_path("observations").write_text("{unfinished", encoding="utf-8")
        self.assertEqual(self._rows()["observations"]["status"], "repair")

    def test_wrong_receipt_key_rejected(self):
        self._finish("observations")
        path = self._receipt_path("observations")
        data = common.read_json(path)
        data["key"] = "a" * 64
        common.write_json(path, data)
        self.assertEqual(self._rows()["observations"]["status"], "repair")

    def test_passed_receipt_with_failure_ranges_stays_repair(self):
        self._finish("observations")
        path = self._receipt_path("observations")
        data = common.read_json(path)
        data["failed_ranges"] = [{"start": 1, "end": 2, "reason": "mismatch"}]
        common.write_json(path, data)
        self.assertEqual(self._rows()["observations"]["status"], "repair")

    def test_algorithm_pin_is_required(self):
        self._config(lambda config: config["stages"]["observations"].update(
            algorithm={"name": "unknown", "sha256": "latest"}))
        self.assertEqual(self._rows()["observations"]["status"], "configuration_required")

    def test_full_external_git_revision_required_when_present(self):
        self._config(lambda config: config["stages"]["observations"]["algorithm"].update(revision="main"))
        self.assertEqual(self._rows()["observations"]["status"], "configuration_required")

    def test_algorithm_change_invalidates_itself_and_descendants(self):
        self._complete_to("scene")
        code = self.root / "inputs" / "camera_solver_new.py"
        code.write_bytes(b"# NEW_ISOLATED_ALGORITHM\npass\n")
        self._config(lambda config: config["stages"]["camera"]["algorithm"].update(
            path="inputs/camera_solver_new.py", sha256=common.sha256(code)))
        rows = self._rows()
        self.assertEqual(rows["observations"]["status"], "cache_hit")
        self.assertEqual(rows["camera"]["status"], "run")
        self.assertTrue(all(rows[name]["status"] == "blocked" for name in ("pose", "contact", "scene", "preview", "render")))

    def test_declared_algorithm_sha_without_actual_file_is_not_trusted(self):
        self._config(lambda config: config["stages"]["observations"]["algorithm"].pop("path"))
        self.assertEqual(self._rows()["observations"]["status"], "configuration_required")

    def test_actual_algorithm_corruption_is_not_a_cache_hit(self):
        self._finish("observations")
        (self.root / "inputs" / "fixture_solver.py").write_bytes(b"MUTATED_CODE")
        self.assertEqual(self._rows()["observations"]["status"], "configuration_required")

    def test_camera_file_change_invalidates_pose_contact_preview_render(self):
        self._complete_to("light")
        (self.root / "inputs" / "camera.json").write_text('{"new_camera":true}', encoding="utf-8")
        rows = self._rows()
        self.assertEqual(rows["observations"]["status"], "cache_hit")
        self.assertEqual(rows["camera"]["status"], "run")
        self.assertTrue(all(rows[name]["status"] == "blocked" for name in ("pose", "contact", "scene", "light", "preview", "render", "media", "review")))

    def test_light_change_keeps_observations_camera_pose_contact_geometry(self):
        self._complete_to("light")
        (self.root / "inputs" / "light.json").write_text('{"energy":12}', encoding="utf-8")
        rows = self._rows()
        self.assertTrue(all(rows[name]["status"] == "cache_hit" for name in ("observations", "camera", "pose", "contact", "scene")))
        self.assertEqual(rows["light"]["status"], "run")
        self.assertEqual(rows["preview"]["status"], "blocked")
        self.assertEqual(rows["render"]["status"], "blocked")

    def test_parameter_change_changes_cache_key(self):
        key = self._rows()["observations"]["key"]
        self._config(lambda config: config["stages"]["observations"]["parameters"].update(threshold=.03))
        self.assertNotEqual(key, self._rows()["observations"]["key"])

    def test_nonfinite_parameters_rejected(self):
        self._config(lambda config: config["stages"]["observations"]["parameters"].update(threshold=float("nan")))
        self.assertEqual(self._rows()["observations"]["status"], "configuration_required")

    def test_output_digest_changes_downstream_key_even_when_stage_key_same(self):
        self._complete_to("camera")
        key = self._rows()["camera"]["key"]
        observation_path = self._receipt_path("observations")
        data = common.read_json(observation_path)
        (self.artifacts / "observations.bin").write_bytes(b"NEW_RECOMPUTED_OBSERVATIONS")
        data["outputs"] = pipeline._fingerprint(self.root, ["artifacts/observations.bin"])
        common.write_json(observation_path, data)
        self.assertNotEqual(key, self._rows()["camera"]["key"])

    def test_actual_measured_time_and_summary_not_estimate(self):
        start = pipeline.start_stage(self.job_path, "observations")
        time.sleep(.015)
        path = self.artifacts / "obs.bin"
        path.write_bytes(b"ACTUAL_OBSERVATIONS")
        result = pipeline.record_stage(self.job_path, start["token"], "passed", [path])
        self.assertGreaterEqual(result["measured_elapsed_seconds"], .01)
        summary = pipeline.timing_summary(self.job_path)
        self.assertEqual(summary["stages"]["observations"]["attempts"], 1)
        self.assertEqual(summary["stages"]["observations"]["passed"], 1)
        self.assertEqual(summary["measured_total_attempt_seconds"], result["measured_elapsed_seconds"])
        self.assertIn("NOT_GPU_OR_AGENT_TOKEN_TIME", summary["scope"])

    def test_running_time_is_not_fabricated(self):
        pipeline.start_stage(self.job_path, "observations")
        summary = pipeline.timing_summary(self.job_path)
        self.assertEqual(summary["stages"]["observations"]["running"], 1)
        self.assertEqual(summary["measured_total_attempt_seconds"], 0)

    def test_duplicate_running_attempt_blocked(self):
        pipeline.start_stage(self.job_path, "observations")
        with self.assertRaisesRegex(ValueError, "already has"):
            pipeline.start_stage(self.job_path, "observations")

    def test_cached_stage_not_reexecuted(self):
        self._finish("observations")
        with self.assertRaisesRegex(ValueError, "cached"):
            pipeline.start_stage(self.job_path, "observations")

    def test_record_preserves_failed_range_summary_and_no_render(self):
        self._complete_to("light")
        token = pipeline.start_stage(self.job_path, "preview")["token"]
        ranges = [{"start": 21, "end": 24, "reason": "foot sliding"}]
        result = pipeline.record_stage(self.job_path, token, "failed", failed_ranges=ranges,
                                       message="actual measured preview differs")
        self.assertEqual(result["next_action"]["stage"], "preview")
        self.assertEqual(result["next_action"]["action"], "repair")
        self.assertEqual(result["next_action"]["failed_ranges"], ranges)
        self.assertEqual(self._rows()["render"]["status"], "blocked")

    def test_failed_range_summary_is_bounded_but_count_complete(self):
        token = pipeline.start_stage(self.job_path, "observations")["token"]
        ranges = [{"start": i, "end": i, "reason": "uncertain"} for i in range(1, 21)]
        result = pipeline.record_stage(self.job_path, token, "failed", failed_ranges=ranges)
        self.assertEqual(result["next_action"]["failed_range_count"], 20)
        self.assertEqual(len(result["next_action"]["failed_ranges"]), pipeline.MAX_RANGE_PREVIEW)

    def test_ranges_outside_job_rejected(self):
        token = pipeline.start_stage(self.job_path, "observations")["token"]
        with self.assertRaisesRegex(ValueError, "outside assigned"):
            pipeline.record_stage(self.job_path, token, "failed", failed_ranges=[{"start": 0, "end": 4, "reason": "bad"}])

    def test_pass_with_failures_becomes_failed(self):
        token = pipeline.start_stage(self.job_path, "observations")["token"]
        path = self.artifacts / "obs.bin"
        path.write_bytes(b"OBS")
        result = pipeline.record_stage(self.job_path, token, "passed", [path],
            failed_ranges=[{"start": 2, "end": 3, "reason": "missing"}])
        self.assertEqual(result["status"], "failed")

    def test_pass_without_actual_output_becomes_failed(self):
        token = pipeline.start_stage(self.job_path, "observations")["token"]
        result = pipeline.record_stage(self.job_path, token, "passed")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["next_action"]["action"], "repair")

    def test_missing_preview_evidence_becomes_failed(self):
        self._complete_to("light")
        token = pipeline.start_stage(self.job_path, "preview")["token"]
        path = self.artifacts / "preview.bin"
        path.write_bytes(b"NOT_A_REPORT")
        result = pipeline.record_stage(self.job_path, token, "passed", [path])
        self.assertEqual(result["status"], "failed")
        self.assertIn("report required", result["errors"][0])
        self.assertEqual(self._rows()["render"]["status"], "blocked")

    def test_preview_delegates_recomputing_validator(self):
        self._complete_to("light")
        token = pipeline.start_stage(self.job_path, "preview")["token"]
        report = self.artifacts / "match.json"
        common.write_json(report, {"job_id": self.job_data["job_id"], "passed": True})
        validator = mock.Mock(return_value={"passed": True})
        with mock.patch.dict(sys.modules, {"match_check": types.SimpleNamespace(validate_report=validator)}):
            result = pipeline.record_stage(self.job_path, token, "passed", report_path=report)
        self.assertEqual(result["status"], "passed")
        self.assertGreaterEqual(validator.call_count, 1)
        validator.assert_any_call(self.job_path, report.resolve())

    def test_preview_recomputation_failure_overrides_passed_boolean(self):
        self._complete_to("light")
        token = pipeline.start_stage(self.job_path, "preview")["token"]
        report = self.artifacts / "match.json"
        common.write_json(report, {"job_id": self.job_data["job_id"], "passed": True})
        validator = mock.Mock(side_effect=ValueError("actual measurements failed"))
        with mock.patch.dict(sys.modules, {"match_check": types.SimpleNamespace(validate_report=validator)}):
            result = pipeline.record_stage(self.job_path, token, "passed", report_path=report)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["next_action"]["stage"], "preview")

    def test_preview_false_validator_result_also_stays_failed(self):
        report = self.artifacts / "match.json"
        common.write_json(report, {"job_id": self.job_data["job_id"], "passed": True})
        validator = mock.Mock(return_value={"passed": False})
        with mock.patch.dict(sys.modules, {"match_check": types.SimpleNamespace(validate_report=validator)}):
            with self.assertRaisesRegex(ValueError, "not passed"):
                pipeline._validate_evidence("preview", self.job_path, self.root, [], "artifacts/match.json")

    def test_preview_cache_revalidates_not_just_report_flag(self):
        self._complete_to("light")
        token = pipeline.start_stage(self.job_path, "preview")["token"]
        report = self.artifacts / "match.json"
        common.write_json(report, {"job_id": self.job_data["job_id"], "passed": True})
        validator = mock.Mock(return_value={"passed": True})
        with mock.patch.dict(sys.modules, {"match_check": types.SimpleNamespace(validate_report=validator)}):
            pipeline.record_stage(self.job_path, token, "passed", report_path=report)
        validator.side_effect = ValueError("changed evidence bytes")
        with mock.patch.dict(sys.modules, {"match_check": types.SimpleNamespace(validate_report=validator)}):
            self.assertEqual(self._rows()["preview"]["status"], "repair")

    def test_changed_input_during_attempt_preserves_unfinished_attempt(self):
        token = pipeline.start_stage(self.job_path, "observations")["token"]
        (self.root / "inputs" / "observations.json").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "changed during attempt"):
            pipeline.record_stage(self.job_path, token, "failed")
        self.assertEqual(pipeline.timing_summary(self.job_path)["stages"]["observations"]["running"], 1)

    def test_completed_attempt_not_reused(self):
        start = pipeline.start_stage(self.job_path, "observations")
        pipeline.record_stage(self.job_path, start["token"], "failed")
        with self.assertRaisesRegex(ValueError, "unfinished"):
            pipeline.record_stage(self.job_path, start["token"], "failed")

    def test_clock_discontinuity_rejected_without_inventing_duration(self):
        token = pipeline.start_stage(self.job_path, "observations")["token"]
        path = self.root / ".pipeline" / "attempts" / (token + ".json")
        attempt = common.read_json(path)
        attempt["started_epoch"] -= 1000
        common.write_json(path, attempt)
        with self.assertRaisesRegex(ValueError, "Clock discontinuity"):
            pipeline.record_stage(self.job_path, token, "failed")
        self.assertEqual(common.read_json(path)["status"], "running")

    def test_unfinished_timer_not_measured_on_another_device(self):
        token = pipeline.start_stage(self.job_path, "observations")["token"]
        with mock.patch.object(pipeline, "_clock_host", return_value="b" * 64):
            with self.assertRaisesRegex(ValueError, "another host"):
                pipeline.record_stage(self.job_path, token, "failed")

    def test_corrupt_timing_rejected(self):
        self._finish("observations")
        path = next((self.root / ".pipeline" / "attempts").glob("*.json"))
        data = common.read_json(path)
        data["measured_elapsed_seconds"] = -1
        common.write_json(path, data)
        with self.assertRaisesRegex(ValueError, "measured attempt duration"):
            pipeline.timing_summary(self.job_path)

    def test_changed_immutable_binding_rejected(self):
        self._config(lambda config: config["binding"].update(skill_revision="2" * 40))
        with self.assertRaisesRegex(ValueError, "another job/revision"):
            pipeline.plan(self.job_path)

    def test_actual_source_change_rejected(self):
        (self.root / "inputs" / "source.mp4").write_bytes(b"WRONG_SOURCE")
        with self.assertRaisesRegex(ValueError, "Asset identity mismatch"):
            pipeline.plan(self.job_path)

    def test_unknown_stage_graph_rejected(self):
        self._config(lambda config: config["stages"].pop("preview"))
        with self.assertRaisesRegex(ValueError, "Complete fixed stage graph"):
            pipeline.plan(self.job_path)

    def test_input_traversal_and_absolute_paths_not_used(self):
        for value in ("../secret", "/etc/passwd", "C:/secret", "inputs\\camera.json", "inputs/CON", "inputs/a:"):
            with self.subTest(value=value):
                self._config(lambda config: config["stages"]["observations"].update(inputs=[value]))
                self.assertEqual(self._rows()["observations"]["status"], "configuration_required")

    def test_output_escape_becomes_failure(self):
        token = pipeline.start_stage(self.job_path, "observations")["token"]
        result = pipeline.record_stage(self.job_path, token, "passed", ["../outside"])
        self.assertEqual(result["status"], "failed")

    def test_cache_dir_escape_rejected(self):
        self._config(lambda config: config.update(cache_dir="../outside"))
        with self.assertRaises(ValueError):
            pipeline.plan(self.job_path)

    def test_duplicate_input_paths_rejected(self):
        self._config(lambda config: config["stages"]["observations"].update(
            inputs=["inputs/observations.json", "inputs/observations.json"]))
        self.assertEqual(self._rows()["observations"]["status"], "configuration_required")

    def test_plan_no_shell_execution(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("no shell")):
            report = pipeline.plan(self.job_path)
        self.assertEqual(report["next_action"]["stage"], "observations")

    def test_cli_outputs_short_next_action(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = pipeline.main(["plan", "--job", str(self.job_path)])
        self.assertEqual(code, 0)
        value = json.loads(stream.getvalue())
        self.assertEqual(value["next_action"]["action"], "run")
        self.assertLess(len(stream.getvalue()), 3500)

    def test_native_partial_manifest_rejected(self):
        self._complete_to("preview")
        report = self.artifacts / "render.json"
        common.write_json(report, {"schema": "client-white-model-render.v1",
            "job_id": self.job_data["job_id"], "status": "RENDERING", "completed": []})
        with mock.patch.object(pipeline, "_validate_evidence", return_value={}):
            token = pipeline.start_stage(self.job_path, "render")["token"]
        with mock.patch.object(pipeline, "plan", return_value={"stages": [{"stage": "render", "key": common.read_json(self.root / ".pipeline" / "attempts" / (token + ".json"))["key"], "status": "run"}], "next_action": {"stage": "render", "action": "repair"}}):
            result = pipeline.record_stage(self.job_path, token, "passed", report_path=report)
        self.assertEqual(result["status"], "failed")
        self.assertIn("Full native-render", result["errors"][0])

    def test_native_manifest_complete_boolean_requires_actual_matching_blend(self):
        report = self.artifacts / "render.json"
        common.write_json(report, {"schema": "client-white-model-render.v1",
            "job_id": self.job_data["job_id"], "status": "COMPLETE", "completed": [],
            "blend_sha256": "a" * 64})
        with self.assertRaisesRegex(ValueError, "actual rendered blend"):
            pipeline._validate_evidence("render", self.job_path, self.root,
                pipeline._fingerprint(self.root, ["artifacts/render.json"]), "artifacts/render.json")

    def test_native_validator_is_delegated_with_actual_blend(self):
        scene = self.artifacts / "candidate.blend"
        scene.write_bytes(b"CONTRACT_ONLY_SAVED_SCENE")
        report = self.artifacts / "render.json"
        common.write_json(report, {"schema": "client-white-model-render.v1",
            "job_id": self.job_data["job_id"], "status": "COMPLETE", "completed": [],
            "blend_sha256": common.sha256(scene)})
        import media
        validator = mock.Mock(side_effect=ValueError("Actual native PNG set incomplete"))
        with mock.patch.object(media, "verify_native", validator):
            with self.assertRaisesRegex(ValueError, "PNG set incomplete"):
                pipeline._validate_evidence("render", self.job_path, self.root,
                    pipeline._fingerprint(self.root, ["artifacts/render.json", "artifacts/candidate.blend"]),
                    "artifacts/render.json")
        validator.assert_called_once_with(self.job_data, scene.resolve(), self.artifacts.resolve())

    def test_render_receipt_expands_and_hashes_frame_files(self):
        report = self.artifacts / "render.json"
        frame = self.artifacts / "frame_0001.png"
        frame.write_bytes(b"SYNTHETIC_FRAME_BYTES_NOT_RENDER")
        common.write_json(report, {"completed": [{"path": "frame_0001.png"}]})
        expanded = pipeline._expanded_outputs("render", self.root, [], "artifacts/render.json")
        self.assertEqual(expanded, ["artifacts/frame_0001.png", "artifacts/render.json"])
        actual = pipeline._fingerprint(self.root, expanded)
        self.assertEqual(next(row["sha256"] for row in actual if row["path"].endswith(".png")), common.sha256(frame))

    def test_media_pass_boolean_without_actual_four_outputs_rejected(self):
        report = self.artifacts / "media.json"
        common.write_json(report, {"schema": "client-white-model-media.v1",
            "job_id": self.job_data["job_id"], "standards_sha256": self.job_data["standards_sha256"],
            "passed": True, "artifacts": []})
        with self.assertRaisesRegex(ValueError, "exactly four actual"):
            pipeline._validate_evidence("media", self.job_path, self.root, [], "artifacts/media.json")

    def test_final_gate_is_reverified_not_taken_from_boolean(self):
        rows = []
        for kind in ("blender", "media", "visual"):
            path = self.artifacts / (kind + ".json")
            common.write_json(path, {"synthetic": kind})
            rows.append({"kind": kind, "path": path.relative_to(self.root).as_posix(), "sha256": common.sha256(path)})
        report = self.artifacts / "gate.json"
        common.write_json(report, {"schema": "client-white-model-gate.v1",
            "job_id": self.job_data["job_id"], "passed": True, "client_acceptance": False,
            "artifacts": rows})
        import gate
        validator = mock.Mock(return_value={"passed": False})
        with mock.patch.object(gate, "verify_bundle", validator):
            with self.assertRaisesRegex(ValueError, "no longer matches"):
                pipeline._validate_evidence("review", self.job_path, self.root, [], "artifacts/gate.json")
        validator.assert_called_once_with(self.job_path, (self.artifacts / "blender.json").resolve(),
                                         (self.artifacts / "media.json").resolve(), (self.artifacts / "visual.json").resolve())

    def test_final_gate_reverification_does_not_set_client_acceptance(self):
        rows = []
        for kind in ("blender", "media", "visual"):
            path = self.artifacts / (kind + ".json")
            common.write_json(path, {"synthetic": kind})
            rows.append({"kind": kind, "path": path.relative_to(self.root).as_posix(), "sha256": common.sha256(path)})
        report = self.artifacts / "gate.json"
        common.write_json(report, {"schema": "client-white-model-gate.v1",
            "job_id": self.job_data["job_id"], "passed": True, "client_acceptance": True,
            "artifacts": rows})
        import gate
        with mock.patch.object(gate, "verify_bundle", return_value={"passed": True}):
            with self.assertRaisesRegex(ValueError, "not client acceptance"):
                pipeline._validate_evidence("review", self.job_path, self.root, [], "artifacts/gate.json")

    def test_final_review_requires_preexisting_independent_gate(self):
        report = self.artifacts / "review.json"
        common.write_json(report, {"schema": "client-white-model-review.v1", "job_id": self.job_data["job_id"], "passed": True})
        with self.assertRaisesRegex(ValueError, "independently reviewed final gate"):
            pipeline._validate_evidence("review", self.job_path, self.root, [], "artifacts/review.json")


if __name__ == "__main__":
    unittest.main()

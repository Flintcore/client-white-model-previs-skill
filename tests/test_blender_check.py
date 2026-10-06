"""Real isolated Blender smoke/negative tests; no client files are loaded.

Set BLENDER_EXE (or the backward-compatible BLENDER_BIN) and run:
  python -m unittest discover -s tests -p test_blender_check.py -v
No installed Blender means SKIPPED, not a claim that Blender tests passed.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
BLENDER = os.environ.get("BLENDER_EXE") or os.environ.get("BLENDER_BIN") or shutil.which("blender")


@unittest.skipUnless(BLENDER, "Set BLENDER_EXE or BLENDER_BIN to execute real Blender tests")
class BlenderCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="client-previs-blender-tests-")
        cls.root = Path(cls.temporary.name)
        cls.skill = cls.root / "skill"
        (cls.skill / "scripts").mkdir(parents=True)
        (cls.skill / "references").mkdir()
        cls.checker = cls.skill / "scripts" / "blender_check.py"
        shutil.copyfile(REPO / "skills" / "client-white-model-previs" / "scripts" / "blender_check.py", cls.checker)
        cls.standards = cls.skill / "references" / "standards.json"
        cls.standards.write_text(json.dumps({"version": "1.0.0", "scope": "Synthetic Blender checker test fixture only"}) + "\n", encoding="utf-8")
        cls.fixture_script = REPO / "tests" / "blender_fixture.py"
        cls.outputs = []

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("BLENDER_TEST_REPORT"):
            path = Path(os.environ["BLENDER_TEST_REPORT"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"scope": "Actual isolated synthetic Blender tests; not client sample acceptance", "executed": cls.outputs}, indent=2) + "\n", encoding="utf-8")
        cls.temporary.cleanup()

    def run_blender(self, arguments):
        result = subprocess.run([str(BLENDER), "--background", "--disable-autoexec", *arguments], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
        return result

    def fixture(self, variant, render=False):
        root = self.root / (variant + ("-rendered" if render else ""))
        args = ["--python-exit-code", "1", "--python", str(self.fixture_script), "--", "--output", str(root), "--standards", str(self.standards), "--variant", variant]
        if render:
            args.append("--render")
        result = self.run_blender(args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        blend = root / "delivery" / "fixture" / "fixture.blend" if variant.startswith("final_") else root / "candidate.blend"
        self.assertTrue(blend.is_file())
        return root

    def check(self, root, renders=False):
        blend = root / "delivery" / "fixture" / "fixture.blend" if root.name.startswith("final_") else root / "candidate.blend"
        before = hashlib.sha256(blend.read_bytes()).hexdigest()
        args = [str(blend), "--python-exit-code", "1", "--python", str(self.checker), "--", "--job", str(root / "job.json"), "--report", str(root / "report.json")]
        if renders:
            args += ["--renders", str(root / "renders")]
        result = self.run_blender(args)
        self.assertTrue((root / "report.json").is_file(), result.stdout + result.stderr)
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256(blend.read_bytes()).hexdigest(), before)
        self.assertFalse(report["visual_approval"])
        self.outputs.append({"fixture": root.name, "process_exit_code": result.returncode,
                             "machine_passed": report["passed"], "checks": report["checks"],
                             "blend_sha256": before, "saved_blend_unchanged": True})
        return result, report

    def assert_gate_failed(self, variant, expected_gate):
        result, report = self.check(self.fixture(variant))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(report["passed"])
        gates = {row["name"]: row for row in report["checks"]}
        self.assertIn(expected_gate, gates)
        self.assertFalse(gates[expected_gate]["passed"], report)

    def test_positive_all_frames_with_real_mesh_bound_bones(self):
        result, report = self.check(self.fixture("positive"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["schema"], "client-white-model-blender-qa.v1")
        self.assertFalse(report["render_evidence"]["provided"])
        self.assertFalse(report["render_evidence"]["passed"])
        self.assertFalse(report["final_bundle_portability"]["applicable"])

    def test_negative_floating_actual_mesh(self):
        self.assert_gate_failed("floating", "actual_shin_cap_ground_support")

    def test_negative_actual_cap_penetration(self):
        self.assert_gate_failed("penetration", "actual_shin_cap_penetration")

    def test_negative_wrong_cap_is_not_relabelled_sole(self):
        self.assert_gate_failed("wrong_cap", "actor_L3_and_foot_metadata")

    def test_negative_stance_slide(self):
        self.assert_gate_failed("slide", "stance_pinned_vertex_drift")

    def test_negative_wrong_resolution(self):
        self.assert_gate_failed("wrong_resolution", "native_resolution_100_percent")

    def test_negative_wrong_frame_count(self):
        self.assert_gate_failed("wrong_frame_count", "source_frame_count")

    def test_positive_fractional_fps_base(self):
        result, report = self.check(self.fixture("fractional_fps"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(report["passed"], report)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is needed to build real final-bundle movieclip fixtures")
    def test_final_bundle_correct_original_movieclip(self):
        result, report = self.check(self.fixture("final_portable"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(report["passed"], report)
        self.assertTrue(report["final_bundle_portability"]["applicable"])
        self.assertTrue(report["final_bundle_portability"]["passed"])
        self.assertEqual(report["final_bundle_portability"]["allowed_unpacked_resource"], "fixture.mp4")

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is needed to build real final-bundle movieclip fixtures")
    def test_final_bundle_parent_input_dependency_rejected(self):
        self.assert_gate_failed("final_parent_dependency", "final_bundle_dependency_portability")

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is needed to build real final-bundle movieclip fixtures")
    def test_final_bundle_absolute_same_folder_path_rejected(self):
        self.assert_gate_failed("final_absolute_dependency", "final_bundle_dependency_portability")

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is needed to build real final-bundle movieclip fixtures")
    def test_final_bundle_unpacked_non_original_asset_rejected(self):
        self.assert_gate_failed("final_non_original_asset", "final_bundle_dependency_portability")

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is needed to build real final-bundle movieclip fixtures")
    def test_final_bundle_packed_relative_asset_passes(self):
        result, report = self.check(self.fixture("final_packed_asset"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(report["passed"], report)
        self.assertTrue(report["final_bundle_portability"]["passed"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is needed to build real final-bundle movieclip fixtures")
    def test_final_bundle_absolute_packed_asset_rejected(self):
        self.assert_gate_failed("final_absolute_packed_asset", "final_bundle_dependency_portability")

    def test_negative_low_samples(self):
        self.assert_gate_failed("low_samples", "eevee_exact_64_samples")

    def test_negative_high_128_samples_is_not_literal_64(self):
        self.assert_gate_failed("high_samples", "eevee_exact_64_samples")

    def test_negative_visible_source_exclusion(self):
        self.assert_gate_failed("visible_excluded_source", "excluded_source_objects_hidden")

    def test_negative_source_exclusion_becomes_visible_later(self):
        self.assert_gate_failed("source_becomes_visible", "excluded_source_objects_hidden")

    def test_positive_actual_hidden_source_exclusion(self):
        result, report = self.check(self.fixture("hidden_excluded_source"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(report["passed"], report)

    def test_negative_scaled_part(self):
        self.assert_gate_failed("scaled", "production_scale_one")

    def test_negative_joint_jump(self):
        self.assert_gate_failed("joint_jump", "real_bound_joint_position_continuity")

    def test_negative_object_jump_even_if_bound_bone_is_static(self):
        self.assert_gate_failed("object_jump_with_static_bone", "real_bound_joint_position_continuity")

    def test_negative_none_gait_is_not_exemption(self):
        self.assert_gate_failed("none_gait", "per_frame_gait_evidence")

    def test_negative_missing_foot_probe(self):
        self.assert_gate_failed("missing_probe", "actor_L3_and_foot_metadata")

    def test_negative_source_hash(self):
        self.assert_gate_failed("wrong_source_hash", "source_identity")

    def test_negative_standards_hash(self):
        self.assert_gate_failed("wrong_standards_hash", "standards_identity")

    def test_negative_missing_support(self):
        self.assert_gate_failed("missing_support", "support_inventory")

    def test_negative_missing_gait_frame(self):
        self.assert_gate_failed("missing_gait_frame", "per_frame_gait_evidence")

    def test_negative_hidden_actor_part(self):
        self.assert_gate_failed("hidden", "actor_continuous_render_presence")

    def test_negative_hidden_parent_collection(self):
        self.assert_gate_failed("hidden_parent_collection", "actor_continuous_render_presence")

    def test_negative_missing_external_image(self):
        self.assert_gate_failed("missing_external_image", "portable_external_dependencies")

    def test_negative_image_texture(self):
        self.assert_gate_failed("image_texture", "no_production_image_textures")

    def test_negative_static_modifier(self):
        self.assert_gate_failed("static_modifier", "static_modifiers_applied")

    def test_negative_linear_animation(self):
        self.assert_gate_failed("linear", "no_linear_animation")

    def test_actual_native_4k_render_and_manifest_then_tampered_manifest(self):
        root = self.fixture("positive", render=True)
        result, report = self.check(root, renders=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["render_evidence"]["count"], 1)
        self.assertTrue(report["render_evidence"]["passed"])
        manifest_path = root / "renders" / "render_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["blend_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        result, report = self.check(root, renders=True)
        self.assertNotEqual(result.returncode, 0)
        gates = {row["name"]: row for row in report["checks"]}
        self.assertFalse(gates["render_scene_provenance"]["passed"])
        self.assertFalse(report["render_evidence"]["passed"])


if __name__ == "__main__":
    unittest.main()

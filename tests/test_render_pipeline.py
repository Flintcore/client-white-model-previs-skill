"""Real isolated render-entry smoke, not client reconstruction/acceptance.

Uses a real clean temporary Git checkout, decoded synthetic H.264 source,
10-part bound Blender fixture, engineering checker, actual full previews and
native renders. Numerical equal-source annotations are explicitly synthetic
contract data, not annotations of the synthetic plain-color source pixels.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
BLENDER = os.environ.get('BLENDER_EXE') or os.environ.get('BLENDER_BIN') or shutil.which('blender')
REQUIRED = BLENDER and shutil.which('git') and shutil.which('ffmpeg') and shutil.which('ffprobe')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')


@unittest.skipUnless(REQUIRED, 'Real Blender/Git/FFmpeg/FFprobe are required')
class RenderPipelineTests(unittest.TestCase):
    @classmethod
    def command(cls, args, expected=0):
        result = subprocess.run([str(a) for a in args], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=240)
        if expected is not None and result.returncode != expected:
            raise AssertionError(result.stdout[-6000:]+'\n'+result.stderr[-6000:])
        return result

    @classmethod
    def bpy(cls, script, args, blend=None, expected=0):
        return cls.command([BLENDER, '--background', '--disable-autoexec',
            *([blend] if blend else []), '--python-exit-code', '1', '--python', script,
            '--', *args], expected)

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='previs-render-contract-')
        cls.root = Path(cls.temp.name).resolve()
        checkout = cls.root/'checkout'
        skill = checkout/'skills'/'client-white-model-previs'
        shutil.copytree(REPO/'skills'/'client-white-model-previs', skill,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'installation.json'))
        (checkout/'.gitignore').write_text('__pycache__/\n*.pyc\n', encoding='utf-8')
        cls.scripts = skill/'scripts'
        cls.command(['git', '-C', checkout, 'init', '-q'])
        cls.command(['git', '-C', checkout, 'config', 'user.name', 'Isolated Fixture'])
        cls.command(['git', '-C', checkout, 'config', 'user.email', 'fixture@example.invalid'])
        cls.command(['git', '-C', checkout, 'config', 'core.autocrlf', 'false'])
        cls.command(['git', '-C', checkout, 'add', '.'])
        cls.command(['git', '-C', checkout, 'commit', '-qm', 'Isolated synthetic render contract'])
        fixture = cls.root/'fixture'
        cls.bpy(REPO/'tests'/'blender_fixture.py', ['--output', fixture,
            '--standards', skill/'references'/'standards.json'])
        source = cls.root/'synthetic.mp4'
        cls.command(['ffmpeg', '-hide_banner', '-v', 'error', '-f', 'lavfi', '-i',
            'color=c=white:s=3840x2160:r=24', '-frames:v', '3', '-an', '-c:v',
            'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', source])
        cls.job_root = cls.root/'job'
        cls.job_path = cls.job_root/'job.json'
        cls.command([sys.executable, '-X', 'utf8', cls.scripts/'job.py', 'init',
            '--source', source, '--template', fixture/'template.blend', '--output', cls.job_root,
            '--name', 'fixture', '--worker-id', 'synthetic-worker', '--palette-decision',
            'SYNTHETIC_CONTRACT_NOT_CLIENT', '--dark-scene'])
        cls.job = read(cls.job_path)
        cls.job['scene'] = read(fixture/'job.json')['scene']
        cls.job['scene']['shots'] = [{'id': 'S01', 'start': 1, 'end': 3}]
        write(cls.job_path, cls.job)
        cls.blend = cls.job_root/'candidate.blend'
        shutil.copy2(fixture/'candidate.blend', cls.blend)
        cls.blend_hash = sha(cls.blend)
        cls.engineering = cls.job_root/'engineering.json'
        cls.bpy(cls.scripts/'blender_check.py', ['--job', cls.job_path, '--report', cls.engineering], cls.blend)
        if read(cls.engineering)['passed'] is not True:
            raise AssertionError('Actual engineering fixture must pass first')
        cls.preview = cls.job_root/'preview'
        cls.bpy(cls.scripts/'render_preview.py', ['--job', cls.job_path, '--output', cls.preview], cls.blend)
        cls.evidence = cls.make_evidence()
        cls.report = cls.job_root/'match_qa.json'
        cls.command([sys.executable, '-X', 'utf8', cls.scripts/'match_check.py', 'check',
            '--job', cls.job_path, '--evidence', cls.evidence, '--output', cls.report])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def make_evidence(cls):
        s = cls.job['source']
        obs = {'schema': 'client-white-model-match-observations.v1',
            'source_sha256': s['sha256'], 'space': {'width': 3840, 'height': 2160,
            'to_source': [1, 0, 0, 0, 1, 0]}, 'frames': [],
            'scope': 'source_observations_not_visual_acceptance',
            'annotation_basis': 'SYNTHETIC_NUMERICAL_CONTRACT_NOT_REAL_PIXEL_ANNOTATIONS'}
        for f in range(1, 4):
            obs['frames'].append({'frame': f, 'source_frame': f-1, 'pts_seconds': (f-1)/24,
                'background': [{'id': str(i), 'xy': [100+i*300, 100+i*200],
                    'split': 'fit' if i == 0 else 'holdout', 'confidence': 1, 'provenance': 'observed'}
                    for i in range(3)],
                'actors': {'A': {'bbox': [1500, 500, 500, 1000],
                    'bbox_provenance': 'observed', 'bbox_confidence': 1,
                    'visibility': {'state': 'visible', 'confidence': 1, 'provenance': 'observed'},
                    'joints': [{'id': 'head', 'xy': [1750, 550+[0, 10, 0][f-1]],
                        'state': 'visible', 'confidence': 1, 'provenance': 'observed'}]}}, 'occlusion': []})
        candidate = copy.deepcopy(obs)
        candidate['schema'] = 'client-white-model-match-candidate.v1'
        candidate['blend_sha256'] = cls.blend_hash
        candidate.update(scope='production_candidate_measurements_not_visual_acceptance',
            job_id=cls.job['job_id'], standards_sha256=cls.job['standards_sha256'],
            skill_revision=cls.job['skill_revision'])
        thresholds = {'min_confidence': .9, 'min_background_points_per_frame': 3,
            'min_holdout_points_per_frame': 2, 'min_visible_joints_per_actor': 1,
            'max_background_p90_px': 1, 'max_background_frame_rms_px': 1, 'max_joint_nme': .001,
            'max_motion_amplitude_relative_error': .01, 'max_motion_amplitude_nme': .001,
            'max_peak_lag_frames': 0, 'min_handheld_amplitude_ratio': .99,
            'max_handheld_amplitude_ratio': 1.01, 'max_handheld_lag_frames': 0,
            'min_handheld_correlation': .99, 'max_static_residual_px': .1,
            'min_handheld_signal_px': .1, 'handheld_search_frames': 0,
            'max_visibility_event_lag_frames': 0}
        calibration = {'schema': 'client-white-model-match-calibration.v1',
            'source_sha256': s['sha256'], 'status': 'diagnostic_calibrated', 'actors': ['A'],
            'calibrator': 'synthetic-test', 'basis': 'Declared numerical contract only, not client tolerance',
            'thresholds': thresholds, 'motion_checks': [{'id': 'head-peak', 'actor': 'A',
                'joint': 'head', 'axis': 'y', 'start': 1, 'end': 3, 'event': 'max'}],
            'handheld_checks': [{'id': 'static-bg', 'background_ids': ['0', '1', '2'], 'start': 1, 'end': 3}]}
        for name, data in [('observations.json', obs), ('candidate.json', candidate), ('calibration.json', calibration)]:
            write(cls.job_root/name, data)
        def ref(relative):
            return {'path': relative, 'sha256': sha(cls.job_root/relative)}
        evidence = {'schema': 'client-white-model-match-evidence.v1', 'scene_path': 'candidate.blend',
            'bindings': {'job_id': cls.job['job_id'], 'source_sha256': s['sha256'],
                'template_sha256': cls.job['template']['sha256'],
                'standards_sha256': cls.job['standards_sha256'], 'skill_revision': cls.job['skill_revision'],
                'blend_sha256': cls.blend_hash}, 'observations': ref('observations.json'),
            'candidate': ref('candidate.json'), 'calibration': ref('calibration.json'),
            'preview': ref('preview/preview_manifest.json')}
        path = cls.job_root/'evidence.json'
        write(path, evidence)
        return path

    def native(self, output, report, engineering=None):
        return self.bpy(self.scripts/'render_native.py', ['--job', self.job_path,
            '--diagnostic-report', engineering or self.engineering, '--match-report', report,
            '--output', output], self.blend, expected=None)

    def test_actual_full_frame_preview_preserves_scene(self):
        manifest = read(self.preview/'preview_manifest.json')
        self.assertEqual(manifest['status'], 'COMPLETE')
        self.assertEqual((manifest['width'], manifest['height']), (960, 540))
        self.assertEqual([r['source_frame'] for r in manifest['frames']], [0, 1, 2])
        self.assertEqual(sha(self.blend), self.blend_hash)
        self.assertFalse(manifest['client_acceptance'])

    def test_missing_match_report_stops_before_render_directory(self):
        out = self.job_root/'native-missing'
        result = self.native(out, self.job_root/'missing.json')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(out.exists())
        self.assertEqual(sha(self.blend), self.blend_hash)

    def test_spoofed_pass_bit_stops_before_render_directory(self):
        report = read(self.report)
        report['measured']['background']['holdout_p90_source_px'] = 99
        fake = self.job_root/'spoofed.json'
        write(fake, report)
        out = self.job_root/'native-spoofed'
        result = self.native(out, fake)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not equal current measured evidence', result.stdout+result.stderr)
        self.assertFalse(out.exists())

    def test_actual_measured_mismatch_blocks_native_render(self):
        candidate = read(self.job_root/'candidate.json')
        for row in candidate['frames']:
            row['actors']['A']['joints'][0]['xy'][0] += 100
        bad_candidate = self.job_root/'candidate-bad.json'
        write(bad_candidate, candidate)
        evidence = read(self.evidence)
        evidence['candidate'] = {'path': bad_candidate.name, 'sha256': sha(bad_candidate)}
        bad_evidence = self.job_root/'evidence-bad.json'
        bad_report = self.job_root/'match-bad.json'
        write(bad_evidence, evidence)
        self.command([sys.executable, '-X', 'utf8', self.scripts/'match_check.py', 'check',
            '--job', self.job_path, '--evidence', bad_evidence, '--output', bad_report], expected=2)
        self.assertFalse(read(bad_report)['passed'])
        out = self.job_root/'native-mismatch'
        result = self.native(out, bad_report)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(out.exists())

    def test_stale_physical_qa_after_job_mapping_change_blocks_native(self):
        original = self.job_path.read_bytes()
        out = self.job_root/'native-stale-physical'
        try:
            job = read(self.job_path)
            job['engineering_thresholds']['contact_gap_m'] += .01
            write(self.job_path, job)
            result = self.native(out, self.report)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('engineering/physical QA first', result.stdout+result.stderr)
            self.assertFalse(out.exists())
        finally:
            self.job_path.write_bytes(original)
        self.assertEqual(sha(self.blend), self.blend_hash)

    def test_incomplete_physical_checks_do_not_trust_pass_bit(self):
        qa = read(self.engineering)
        qa['checks'] = []
        path = self.job_root/'physical-spoofed.json'
        write(path, qa)
        out = self.job_root/'native-incomplete-physical'
        result = self.native(out, self.report, path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Complete passed actual engineering', result.stdout+result.stderr)
        self.assertFalse(out.exists())

    def test_native_output_outside_job_is_rejected(self):
        out = self.root/'outside-native'
        result = self.native(out, self.report)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(out.exists())

    def projection_config(self, name='projection-config.json'):
        path = self.job_root/name
        write(path, {'schema': 'client-white-model-projection-config.v1', 'camera': 'CAMERA',
            'space': {'width': 960, 'height': 540},
            'background': [{'id': 'anchor', 'split': 'holdout', 'world_xyz_m': [0, 0, 0],
                            'position_evidence': 'synthetic-known-static-world-point'}],
            'actors': {'A': {'object': 'ACTOR_A_rig', 'joints': [
                {'id': 'head', 'bone': 'head', 'endpoint': 'head'},
                {'id': 'ankle_l', 'bone': 'shin_l', 'endpoint': 'tail'}]}}})
        return path

    def test_actual_production_projection_not_fake_visibility(self):
        config = self.projection_config()
        output = self.job_root/'projection.json'
        self.bpy(self.scripts/'export_projection.py', ['--job', self.job_path,
            '--config', config, '--output', output, '--expected-blend-sha', self.blend_hash], self.blend)
        data = read(output)
        self.assertEqual(data['job_id'], self.job['job_id'])
        self.assertEqual(len(data['frames']), 3)
        self.assertEqual(data['space']['to_source'], [4, 0, 0, 0, 4, 0])
        self.assertEqual(data['extraction']['before'], data['extraction']['after'])
        self.assertFalse(data['extraction']['saved_scene'])
        self.assertFalse(data['extraction']['pixel_occlusion_verified'])
        for row in data['frames']:
            self.assertEqual(row['actors']['A']['visibility']['state'], 'unknown')
            self.assertEqual(row['actors']['A']['visibility']['confidence'], 0)
        self.assertEqual(sha(self.blend), self.blend_hash)

    def test_projection_missing_bone_fails_without_artifact(self):
        config = self.projection_config('projection-missing-config.json')
        data = read(config)
        data['actors']['A']['joints'][0]['bone'] = 'NONEXISTENT'
        write(config, data)
        output = self.job_root/'projection-missing.json'
        result = self.bpy(self.scripts/'export_projection.py', ['--job', self.job_path,
            '--config', config, '--output', output], self.blend, expected=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertEqual(sha(self.blend), self.blend_hash)

    def projection_variant_fails(self, variant, expected_message):
        blend = self.job_root/(variant+'.blend')
        self.bpy(REPO/'tests'/'projection_variant.py', ['--variant', variant, '--output', blend], self.blend)
        config = self.projection_config('projection-'+variant+'-config.json')
        output = self.job_root/('projection-'+variant+'.json')
        before = sha(blend)
        result = self.bpy(self.scripts/'export_projection.py', ['--job', self.job_path,
            '--config', config, '--output', output], blend, expected=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(expected_message, result.stdout+result.stderr)
        self.assertFalse(output.exists())
        self.assertEqual(sha(blend), before)
        self.assertEqual(sha(self.blend), self.blend_hash)

    def test_projection_non_metric_units_fail(self):
        self.projection_variant_fails('centimetres', 'scale_length')

    def test_projection_camera_marker_change_fails(self):
        self.projection_variant_fails('camera-marker', 'camera changed')

    def test_actual_native_render_positive_binds_both_prechecks(self):
        out = self.job_root/'native-positive'
        result = self.native(out, self.report)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        manifest = read(out/'render_manifest.json')
        self.assertEqual(manifest['status'], 'COMPLETE')
        self.assertEqual(len(manifest['completed']), 3)
        self.assertEqual((manifest['width'], manifest['height']), (3840, 2160))
        self.assertEqual(manifest['engineering_report_sha256'], sha(self.engineering))
        self.assertEqual(manifest['match_report_sha256'], sha(self.report))
        self.assertEqual(sha(self.blend), self.blend_hash)


if __name__ == '__main__':
    unittest.main()

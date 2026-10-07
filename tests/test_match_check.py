"""Numerical/identity gates on synthetic measurements; not real client approval.

The PNG payloads and hashes are real temporary files. Source/blend bytes are
explicitly contract fixtures, not videos/3D scenes. Only the skill revision
reader is patched; numerical metrics, CRC/deflate, identity checks, evidence
rewrites, frame mapping and report recomputation actually run.
"""
import copy
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib


SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'client-white-model-previs' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import common
import match_check as mc
import team_queue


PIN = '1' * 40


def png(path, width=16, height=9):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    data = b'\x89PNG\r\n\x1a\n'
    data += chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    data += chunk(b'IDAT', zlib.compress((b'\x00' + b'\x80\x90\xA0' * width) * height))
    data += chunk(b'IEND', b'')
    Path(path).write_bytes(data)


def numeric_fixture(frame_count=21):
    source = {'width': 3840, 'height': 2160, 'fps_num': 24, 'fps_den': 1,
              'frame_count': frame_count, 'sha256': 'a' * 64}
    observed = {'schema': 'client-white-model-match-observations.v1', 'source_sha256': source['sha256'],
                'scope': 'source_observations_not_visual_acceptance',
                'space': {'width': 1920, 'height': 1080, 'to_source': [2, 0, 0, 0, 2, 0]}, 'frames': []}
    for f in range(1, frame_count + 1):
        phase = (f - 1) * 2 * math.pi / 10
        shake = 4 * math.sin(phase)
        row = {'frame': f, 'source_frame': f - 1, 'pts_seconds': (f - 1) / 24,
               'background': [], 'actors': {},
               'occlusion': [{'front': 'A', 'back': 'B', 'confidence': 1, 'provenance': 'observed'}]}
        for i, (x, y) in enumerate([(50, 50), (1000, 80), (1700, 700)]):
            row['background'].append({'id': 'p' + str(i), 'xy': [x + shake, y], 'split': 'holdout' if i < 2 else 'fit', 'confidence': 1, 'provenance': 'observed'})
        for actor, x in [('A', 400), ('B', 600)]:
            row['actors'][actor] = {'bbox': [x - 100, 250, 350, 450],
                'bbox_provenance': 'observed', 'bbox_confidence': 1,
                'visibility': {'state': 'visible', 'confidence': 1, 'provenance': 'observed'},
                'joints': [{'id': 'head', 'xy': [x, 300 + 10 * math.sin(phase)], 'state': 'visible', 'confidence': 1, 'provenance': 'observed'},
                           {'id': 'wrist', 'xy': [x + 50, 500 + 30 * math.sin(phase)], 'state': 'visible', 'confidence': 1, 'provenance': 'observed'}]}
        observed['frames'].append(row)
    candidate = copy.deepcopy(observed)
    candidate['schema'] = 'client-white-model-match-candidate.v1'
    candidate['scope'] = 'production_candidate_measurements_not_visual_acceptance'
    candidate['blend_sha256'] = 'b' * 64
    for row in candidate['frames']:
        for p in row['background'] + row['occlusion']:
            p['provenance'] = 'extracted'
        for actor in row['actors'].values():
            actor['visibility']['provenance'] = 'extracted'
            for joint in actor['joints']:
                joint['provenance'] = 'extracted'
    calibration = {'schema': 'client-white-model-match-calibration.v1', 'status': 'diagnostic_calibrated',
        'source_sha256': source['sha256'], 'calibrator': 'synthetic-fixture', 'basis': 'Synthetic known ground truth; NOT client tolerances',
        'actors': ['A', 'B'], 'thresholds': {
            'min_confidence': .75, 'min_background_points_per_frame': 3,
            'min_holdout_points_per_frame': 2, 'min_visible_joints_per_actor': 2,
            'max_background_p90_px': 4, 'max_background_frame_rms_px': 4,
            'max_joint_nme': .03, 'max_motion_amplitude_relative_error': .05,
            'max_motion_amplitude_nme': .02, 'max_peak_lag_frames': 0,
            'min_handheld_amplitude_ratio': .9, 'max_handheld_amplitude_ratio': 1.1,
            'max_handheld_lag_frames': 0, 'max_static_residual_px': 2,
            'min_handheld_signal_px': 1, 'handheld_search_frames': 4,
            'min_handheld_correlation': .95, 'max_visibility_event_lag_frames': 0,
        }, 'motion_checks': [{'id': a + '-wrist-y', 'actor': a, 'joint': 'wrist', 'axis': 'y', 'start': 1, 'end': frame_count, 'event': 'max'} for a in ['A', 'B']],
        'handheld_checks': [{'id': 'shot1', 'start': 1, 'end': frame_count, 'background_ids': ['p0', 'p1', 'p2']}]}
    return source, observed, candidate, calibration


class NumericMatchTests(unittest.TestCase):
    def setUp(self):
        self.source, self.observed, self.candidate, self.calibration = numeric_fixture()

    def measure(self):
        return mc.evaluate(self.observed, self.candidate, self.calibration, self.source)

    def codes(self):
        return {row['code'] for row in self.measure()['failures']}

    def test_exact_known_numeric_fixture_passes_with_zero_error(self):
        report = self.measure()
        self.assertTrue(report['passed'])
        self.assertEqual(report['measured']['background']['holdout_count'], 42)
        self.assertEqual(report['measured']['visible_pose']['count'], 84)
        self.assertEqual(report['measured']['background']['holdout_p90_source_px'], 0)
        self.assertEqual(report['measured']['visible_pose']['nme_p90'], 0)
        self.assertAlmostEqual(report['measured']['camera_residual'][0]['amplitude_ratio'], 1)
        self.assertEqual(report['measured']['camera_residual'][0]['best_lag_frames'], 0)
        self.assertEqual(report['scope'], 'NUMERIC_DIAGNOSTIC_ONLY_NO_ARTIFACT_VALIDATION')

    def test_proxy_error_reported_in_native_source_pixels(self):
        for f in self.candidate['frames']:
            for p in f['background']:
                p['xy'][0] += 5
        result = self.measure()
        self.assertFalse(result['passed'])
        self.assertAlmostEqual(result['measured']['background']['holdout_p90_source_px'], 10)
        self.assertIn('background_frame_reprojection', self.codes())

    def test_candidate_native_coordinates_match_proxy_observations(self):
        self.candidate['space'] = {'width': 3840, 'height': 2160, 'to_source': [1, 0, 0, 0, 1, 0]}
        for f in self.candidate['frames']:
            for p in f['background']:
                p['xy'] = [2 * v for v in p['xy']]
            for a in f['actors'].values():
                a['bbox'] = [2 * v for v in a['bbox']]
                for j in a['joints']:
                    j['xy'] = [2 * v for v in j['xy']]
        self.assertTrue(self.measure()['passed'])

    def test_fit_points_do_not_replace_independent_holdouts(self):
        for f in self.observed['frames']:
            for p in f['background']:
                p['split'] = 'fit'
        self.assertIn('background_holdout_unmeasured', self.codes())
        self.assertIn('background_observation_coverage', self.codes())

    def test_missing_source_rows_report_precise_failed_ranges(self):
        self.observed['frames'] = [f for f in self.observed['frames'] if f['frame'] not in {4, 5, 9}]
        row = next(f for f in self.measure()['failures'] if f['code'] == 'source_frame_coverage')
        self.assertEqual(row['frame_ranges'], [{'start': 4, 'end': 5}, {'start': 9, 'end': 9}])

    def test_missing_candidate_rows_are_not_interpolated(self):
        del self.candidate['frames'][7]
        self.assertIn('candidate_frame_coverage', self.codes())

    def test_inferred_background_not_silently_ground_truth(self):
        for f in self.observed['frames']:
            f['background'][0]['provenance'] = 'inferred'
        report = self.measure()
        self.assertEqual(report['measured']['inferred_entries_excluded']['background'], 21)
        self.assertFalse(report['passed'])

    def test_hidden_inferred_feet_ignored_not_labeled_ground_truth(self):
        for f in self.observed['frames']:
            for a in f['actors'].values():
                a['joints'].append({'id': 'hidden_foot', 'xy': [10, 999], 'state': 'occluded', 'confidence': 1, 'provenance': 'inferred'})
        result = self.measure()
        self.assertTrue(result['passed'])
        self.assertEqual(result['measured']['inferred_entries_excluded']['joint'], 42)
        self.assertEqual(result['measured']['visible_pose']['count'], 84)

    def test_low_confidence_visible_joints_block_coverage(self):
        for f in self.observed['frames']:
            for j in f['actors']['B']['joints']:
                j['confidence'] = .2
        self.assertIn('source_joint_coverage', self.codes())
        self.assertIn('motion_observation_coverage', self.codes())

    def test_missing_bbox_keeps_pixel_errors_but_does_not_invent_nme(self):
        for f in self.observed['frames']:
            del f['actors']['A']['bbox']
        report = self.measure()
        self.assertEqual(report['measured']['visible_pose']['count'], 84)
        self.assertEqual(report['measured']['visible_pose']['normalized_sample_count'], 42)
        self.assertIsNone(report['measured']['visible_pose']['samples'][0]['nme'])
        self.assertEqual(report['measured']['visible_pose']['samples'][0]['error_source_px'], 0)
        self.assertIn('pose_normalization_unmeasured', self.codes())
        self.assertIn('motion_normalization_unmeasured', self.codes())

    def test_inferred_bbox_does_not_turn_pose_pixels_into_calibrated_nme(self):
        for f in self.observed['frames']:
            f['actors']['A']['bbox_provenance'] = 'inferred'
        self.assertIn('pose_normalization_unmeasured', self.codes())
        self.assertEqual(self.measure()['measured']['visible_pose']['normalized_sample_count'], 42)

    def test_missing_actor_visibility_becomes_missing_evidence_not_fake_true(self):
        del self.candidate['frames'][5]['actors']['B']
        self.assertIn('candidate_visibility_coverage', self.codes())

    def test_joint_nme_uses_observed_bbox_not_candidate_size(self):
        for f in self.candidate['frames']:
            a = f['actors']['A']
            a['bbox'] = [0, 0, 100000, 100000]
            for j in a['joints']:
                j['xy'][0] += 100
        result = self.measure()
        self.assertIn('visible_joint_nme', {r['code'] for r in result['failures']})
        expected = 200 / math.hypot(700, 900)
        self.assertAlmostEqual(result['measured']['visible_pose']['samples'][0]['nme'], expected)

    def test_same_peak_time_but_overlarge_motion_amplitude_fails(self):
        for f in self.candidate['frames']:
            j = f['actors']['A']['joints'][1]
            j['xy'][1] = 500 + 2 * (j['xy'][1] - 500)
        result = self.measure()
        row = result['measured']['motion'][0]
        self.assertAlmostEqual(row['relative_amplitude_error'], 1)
        self.assertEqual(row['peak_lag_frames'], 0)
        self.assertIn('motion_amplitude_mismatch', self.codes())

    def test_same_amplitude_with_delayed_strike_peak_fails(self):
        for f in self.candidate['frames']:
            f['actors']['A']['joints'][1]['xy'][1] = 500 + 30 * math.sin((f['frame'] - 3) * 2 * math.pi / 10)
        self.assertIn('motion_peak_timing', self.codes())
        self.assertGreater(self.measure()['measured']['motion'][0]['peak_lag_frames'], 0)

    def test_still_pose_does_not_get_an_invented_walk_cycle(self):
        for f in self.observed['frames']:
            f['actors']['A']['joints'][1]['xy'][1] = 500
        self.assertIn('motion_amplitude_mismatch', self.codes())

    def test_no_motion_windows_is_unmeasured_not_pass(self):
        self.calibration['motion_checks'] = []
        self.assertIn('motion_amplitude_phase_unmeasured', self.codes())

    def test_visible_actor_cannot_omit_all_motion_checks(self):
        self.calibration['motion_checks'] = self.calibration['motion_checks'][:1]
        self.assertIn('actor_motion_window_unmeasured', self.codes())

    def test_camera_overshake_is_computed_source_relative(self):
        for f in self.candidate['frames']:
            delta = 4 * math.sin((f['frame'] - 1) * 2 * math.pi / 10)
            for p in f['background']:
                p['xy'][0] += delta
        report = self.measure()
        self.assertAlmostEqual(report['measured']['camera_residual'][0]['amplitude_ratio'], 2)
        self.assertIn('camera_residual_amplitude_mismatch', self.codes())

    def test_filtering_all_handheld_motion_flat_is_also_failure(self):
        for f in self.candidate['frames']:
            for p, x in zip(f['background'], [50, 1000, 1700]):
                p['xy'][0] = x
        row = self.measure()['measured']['camera_residual'][0]
        self.assertEqual(row['candidate_residual_rms_px'], 0)
        self.assertIn('camera_residual_amplitude_mismatch', self.codes())

    def test_handheld_phase_lag_is_measured_not_just_rms(self):
        for f in self.candidate['frames']:
            for p, x in zip(f['background'], [50, 1000, 1700]):
                p['xy'][0] = x + 4 * math.sin((f['frame'] - 3) * 2 * math.pi / 10)
        row = self.measure()['measured']['camera_residual'][0]
        self.assertNotEqual(row['best_lag_frames'], 0)
        self.assertIn('camera_residual_phase_mismatch', self.codes())

    def test_same_rms_randomized_trajectory_rejected(self):
        for f in self.candidate['frames']:
            for p, x in zip(f['background'], [50, 1000, 1700]):
                p['xy'][0] = x + 4 * math.sin((f['frame'] - 1) * 2 * math.pi / 5)
        row = self.measure()['measured']['camera_residual'][0]
        self.assertAlmostEqual(row['amplitude_ratio'], 1)
        self.assertLess(row['correlation'], .95)
        self.assertIn('camera_residual_trajectory_mismatch', self.codes())

    def test_static_source_does_not_receive_handheld_noise(self):
        for f in self.observed['frames']:
            for p, x in zip(f['background'], [50, 1000, 1700]):
                p['xy'][0] = x
        self.assertIn('camera_invented_shake', self.codes())

    def test_static_source_and_static_candidate_can_pass_camera_check(self):
        for data in [self.observed, self.candidate]:
            for f in data['frames']:
                for p, x in zip(f['background'], [50, 1000, 1700]):
                    p['xy'][0] = x
        report = self.measure()
        self.assertTrue(report['passed'])
        self.assertIsNone(report['measured']['camera_residual'][0]['amplitude_ratio'])

    def test_no_camera_windows_is_unmeasured(self):
        self.calibration['handheld_checks'] = []
        self.assertIn('camera_residual_amplitude_phase_unmeasured', self.codes())

    def test_occlusion_order_reversal_reported_at_actual_frames(self):
        for f in self.candidate['frames'][5:8]:
            f['occlusion'] = [{'front': 'B', 'back': 'A', 'confidence': 1, 'provenance': 'extracted'}]
        rows = [f for f in self.measure()['failures'] if f['code'] == 'occlusion_order_mismatch']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['frame_ranges'], [{'start': 6, 'end': 8}])

    def test_source_bbox_overlap_without_semantic_annotation_is_missing_evidence(self):
        self.observed['frames'][5]['occlusion'] = []
        self.assertIn('source_occlusion_coverage', self.codes())

    def test_inferred_source_order_does_not_certify_occlusion(self):
        self.observed['frames'][5]['occlusion'][0]['provenance'] = 'inferred'
        self.assertIn('source_occlusion_coverage', self.codes())

    def test_character_early_appearance_and_event_lag(self):
        for data, last_offscreen in [(self.observed, 4), (self.candidate, 2)]:
            for f in data['frames'][:last_offscreen]:
                f['actors']['B']['visibility']['state'] = 'offscreen'
                f['actors']['B']['joints'] = []
        self.calibration['motion_checks'][1]['start'] = 5
        result = self.measure()
        events = [e for e in result['measured']['visibility_events'] if e['actor'] == 'B']
        self.assertEqual(events[0]['source_frame'], 5)
        self.assertEqual(events[0]['candidate_frame'], 3)
        self.assertEqual(events[0]['lag_frames'], 2)
        self.assertIn('visibility_event_timing', self.codes())

    def test_unknown_source_visibility_not_certified(self):
        self.observed['frames'][6]['actors']['B']['visibility']['state'] = 'unknown'
        self.assertIn('source_visibility_coverage', self.codes())

    def test_actor_identity_swap_in_candidate_is_not_hidden_by_smoothing(self):
        for f in self.candidate['frames'][4:7]:
            f['actors']['A'], f['actors']['B'] = f['actors']['B'], f['actors']['A']
        self.assertIn('visible_joint_nme', self.codes())

    def test_unmapped_actor_is_not_silently_dropped(self):
        self.candidate['frames'][7]['actors']['extra'] = copy.deepcopy(self.candidate['frames'][7]['actors']['A'])
        self.assertIn('unmapped_actor_identity', self.codes())

    def test_uncalibrated_thresholds_do_not_pass(self):
        self.calibration['status'] = 'uncalibrated'
        self.assertIn('uncalibrated_thresholds', self.codes())

    def test_missing_numeric_thresholds_rejected(self):
        del self.calibration['thresholds']['max_joint_nme']
        with self.assertRaisesRegex(ValueError, 'complete calibrated'):
            self.measure()

    def test_bad_source_frame_origin_rejected(self):
        self.observed['frames'][0]['source_frame'] = 1
        with self.assertRaisesRegex(ValueError, 'Source frame 0'):
            self.measure()

    def test_30fps_pts_for_24fps_source_rejected(self):
        self.candidate['frames'][1]['pts_seconds'] = 1 / 30
        with self.assertRaisesRegex(ValueError, 'PTS mismatch'):
            self.measure()

    def test_fractional_source_fps_not_forced_to_24(self):
        self.source['fps_num'], self.source['fps_den'] = 30000, 1001
        for data in [self.observed, self.candidate]:
            for f in data['frames']:
                f['pts_seconds'] = (f['frame'] - 1) * 1001 / 30000
        self.assertTrue(self.measure()['passed'])

    def test_duplicate_frame_or_joint_ids_rejected(self):
        self.candidate['frames'].append(copy.deepcopy(self.candidate['frames'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate/out-of-range'):
            self.measure()
        self.candidate['frames'].pop()
        joints = self.candidate['frames'][0]['actors']['A']['joints']
        joints.append(copy.deepcopy(joints[0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate joint'):
            self.measure()

    def test_conflicting_candidate_occlusion_orders_rejected(self):
        self.candidate['frames'][0]['occlusion'].append({'front': 'B', 'back': 'A', 'confidence': 1, 'provenance': 'extracted'})
        with self.assertRaisesRegex(ValueError, 'Conflicting duplicate'):
            self.measure()

    def test_nan_coordinate_and_boolean_confidence_rejected(self):
        self.candidate['frames'][0]['background'][0]['xy'][0] = float('nan')
        with self.assertRaisesRegex(ValueError, 'Finite numeric'):
            self.measure()
        self.candidate['frames'][0]['background'][0]['xy'][0] = 50
        self.observed['frames'][0]['background'][0]['confidence'] = True
        with self.assertRaisesRegex(ValueError, 'Finite numeric'):
            self.measure()

    def test_singular_proxy_transform_rejected(self):
        self.observed['space']['to_source'] = [0, 0, 0, 0, 0, 0]
        with self.assertRaisesRegex(ValueError, 'Singular'):
            self.measure()

    def test_wrong_proxy_plane_cannot_hide_error_as_unit_coordinates(self):
        self.observed['space']['to_source'] = [10, 0, 0, 0, 10, 0]
        with self.assertRaisesRegex(ValueError, 'exceeds source'):
            self.measure()

    def test_implicit_unit_normalization_cannot_shrink_native_pixel_errors(self):
        self.observed['space']['to_source'] = [1 / 1920, 0, 0, 0, 1 / 1080, 0]
        self.candidate['space']['to_source'] = [1 / 1920, 0, 0, 0, 1 / 1080, 0]
        with self.assertRaisesRegex(ValueError, 'does not map declared source-pixel'):
            self.measure()

    def test_explicit_cropped_proxy_maps_source_pixels_without_unit_shrink(self):
        for data in [self.observed, self.candidate]:
            data['space']['source_rect'] = [100, 100, 1920, 1080]
            data['space']['to_source'] = [1, 0, 100, 0, 1, 100]
        self.assertTrue(self.measure()['passed'])


class MatchContractFixture(unittest.TestCase):
    """Reusable setup only; instantiate/run setUp and register doCleanups.

    This isolates numeric report integration tests. Its blend/source bytes are
    intentionally fake and must be replaced before actual Blender/media tests.
    """
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='numeric-match-contract-only-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patch = mock.patch.object(common, 'current_revision', return_value=PIN)
        patch.start()
        self.addCleanup(patch.stop)
        self.source, self.observed, self.candidate, self.calibration = numeric_fixture()
        (self.root / 'inputs').mkdir()
        (self.root / 'inputs/source.mp4').write_bytes(b'CONTRACT_ONLY_NOT_VIDEO')
        (self.root / 'inputs/template.blend').write_bytes(b'CONTRACT_ONLY_NOT_TEMPLATE')
        (self.root / 'scene.blend').write_bytes(b'CONTRACT_ONLY_NOT_REAL_BLEND')
        self.source.update(path='inputs/source.mp4', sha256=common.sha256(self.root / 'inputs/source.mp4'), audio_present=False)
        template = {'path': 'inputs/template.blend', 'sha256': common.sha256(self.root / 'inputs/template.blend'), 'level': 'L3'}
        render = {'width': 3840, 'height': 2160, 'percentage': 100, 'samples': 64, 'dark_scene': False}
        timeline = {'frame_start': 1, 'frame_end': self.source['frame_count']}
        task = {'source_sha256': self.source['sha256'], 'template_sha256': template['sha256'],
                'standards_sha256': common.sha256(common.RULES_PATH), 'skill_revision': PIN,
                'spec': {'render': render, 'fps': '24/1', 'profile': 'client-4k', 'palette_decision': 'fixture blue/orange', 'uniform_character_level': 'L3'}, 'timeline': timeline}
        task['job_id'] = team_queue.task_id(task)
        self.job = {'schema': 'client-white-model-job.v1', 'job_id': task['job_id'],
                    'standards_version': common.VERSION, 'standards_sha256': task['standards_sha256'],
                    'skill_revision': PIN, 'source': self.source, 'template': template, 'render': render,
                    'timeline': timeline, 'profile': 'client-4k', 'palette_decision': 'fixture blue/orange',
                    'queue_task': task, 'project_name': 'fixture', 'producer': 'fixture-only',
                    'scene': {'actors': [{'id': 'A'}, {'id': 'B'}], 'shots': [{'id': 's1', 'start': 1, 'end': 21}]}}
        self.job_path = self.root / 'job.json'
        common.write_json(self.job_path, self.job)
        blend_hash = common.sha256(self.root / 'scene.blend')
        for data in [self.observed, self.candidate, self.calibration]:
            data['source_sha256'] = self.source['sha256']
        self.candidate['blend_sha256'] = blend_hash
        self.candidate.update(job_id=self.job['job_id'], standards_sha256=self.job['standards_sha256'], skill_revision=PIN)
        self.preview = {'schema': 'client-white-model-match-preview.v1', 'source_sha256': self.source['sha256'],
                        'blend_sha256': blend_hash, 'width': 16, 'height': 9, 'fps_num': 24, 'fps_den': 1,
                        'status': 'COMPLETE', 'job_id': self.job['job_id'], 'standards_sha256': self.job['standards_sha256'],
                        'skill_revision': PIN, 'frames': []}
        (self.root / 'qa').mkdir()
        for f in range(1, 22):
            p = self.root / 'qa' / ('frame_%04d.png' % f)
            png(p)
            self.preview['frames'].append({'frame': f, 'source_frame': f - 1, 'pts_seconds': (f - 1) / 24,
                'path': p.name, 'sha256': common.sha256(p), 'width': 16, 'height': 9})
        self.evidence = {'schema': 'client-white-model-match-evidence.v1', 'scene_path': 'scene.blend',
            'bindings': {'job_id': self.job['job_id'], 'source_sha256': self.source['sha256'],
                         'template_sha256': template['sha256'], 'standards_sha256': task['standards_sha256'],
                         'skill_revision': PIN, 'blend_sha256': blend_hash}}
        self.evidence_path = self.root / 'qa/evidence.json'
        self.report_path = self.root / 'match_qa.json'
        self.save()

    def save(self):
        for kind, data in [('observations', self.observed), ('candidate', self.candidate), ('calibration', self.calibration), ('preview', self.preview)]:
            p = self.root / 'qa' / (kind + '.json')
            common.write_json(p, data)
            self.evidence[kind] = {'path': p.relative_to(self.root).as_posix(), 'sha256': common.sha256(p)}
        common.write_json(self.evidence_path, self.evidence)

    def check(self, output=None):
        return mc.check(self.job_path, self.evidence_path, output=output)


class ArtifactMatchTests(MatchContractFixture):
    def test_actual_hashed_artifacts_report_and_recomputation(self):
        report = self.check(self.report_path)
        self.assertTrue(report['passed'])
        self.assertEqual(report['scope'], mc.SCOPE)
        self.assertEqual(report['frame_count'], 21)
        self.assertTrue(report['preview']['all_frame_hashes_verified'])
        self.assertTrue(report['preview']['png_payload_structure_verified'])
        self.assertFalse(report['approval']['client_acceptance'])
        self.assertFalse(report['approval']['independent_visual'])
        self.assertEqual(mc.validate_report(self.job_path, self.report_path, self.root / 'scene.blend'), report)

    def test_unhashed_pass_boolean_is_rejected(self):
        self.evidence['observations'] = {'passed': True}
        common.write_json(self.evidence_path, self.evidence)
        with self.assertRaisesRegex(ValueError, 'hashed artifact'):
            self.check()

    def test_changed_source_input_rejected_even_report_is_green(self):
        self.check(self.report_path)
        (self.root / 'inputs/source.mp4').write_bytes(b'NEW_SOURCE')
        with self.assertRaisesRegex(ValueError, 'Asset identity mismatch'):
            mc.validate_report(self.job_path, self.report_path)

    def test_changed_scene_rejects_cached_report(self):
        self.check(self.report_path)
        (self.root / 'scene.blend').write_bytes(b'CHANGED_SCENE')
        with self.assertRaisesRegex(ValueError, 'binding mismatch'):
            mc.validate_report(self.job_path, self.report_path)

    def test_wrong_revision_template_or_rules_binding_rejected(self):
        for key in ['skill_revision', 'template_sha256', 'standards_sha256', 'job_id']:
            with self.subTest(key=key):
                original = self.evidence['bindings'][key]
                self.evidence['bindings'][key] = '0' * len(original)
                self.save()
                with self.assertRaisesRegex(ValueError, 'binding mismatch'):
                    self.check()
                self.evidence['bindings'][key] = original

    def test_changed_observation_bytes_reject_cached_report(self):
        self.check(self.report_path)
        p = self.root / 'qa/observations.json'
        p.write_text(p.read_text() + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Changed evidence'):
            mc.validate_report(self.job_path, self.report_path)

    def test_modified_calibration_requires_new_evidence_and_report(self):
        self.check(self.report_path)
        self.calibration['thresholds']['max_joint_nme'] = .04
        self.save()
        with self.assertRaisesRegex(ValueError, 'Changed evidence artifact: match evidence'):
            mc.validate_report(self.job_path, self.report_path)

    def test_forged_measured_values_not_accepted(self):
        report = self.check(self.report_path)
        report['measured']['background']['holdout_p90_source_px'] = 999
        common.write_json(self.report_path, report)
        with self.assertRaisesRegex(ValueError, 'does not equal current'):
            mc.validate_report(self.job_path, self.report_path)

    def test_real_failed_report_cannot_just_flip_passed(self):
        for f in self.candidate['frames']:
            for p in f['background']:
                p['xy'][0] += 100
        self.save()
        report = self.check(self.report_path)
        self.assertFalse(report['passed'])
        with self.assertRaisesRegex(ValueError, 'precheck failed'):
            mc.validate_report(self.job_path, self.report_path)
        report['passed'] = True
        common.write_json(self.report_path, report)
        with self.assertRaisesRegex(ValueError, 'does not equal current'):
            mc.validate_report(self.job_path, self.report_path)

    def test_report_evidence_relabelled_false_cannot_approve(self):
        report = self.check(self.report_path)
        report['scope'] = 'CLIENT_ACCEPTED'
        report['approval']['client_acceptance'] = True
        common.write_json(self.report_path, report)
        with self.assertRaisesRegex(ValueError, 'does not equal current'):
            mc.validate_report(self.job_path, self.report_path)

    def test_all_locked_actor_ids_required(self):
        self.calibration['actors'] = ['A']
        self.save()
        with self.assertRaisesRegex(ValueError, 'all locked scene actor'):
            self.check()

    def test_diagnostic_candidate_cannot_be_wrapped_in_formal_evidence(self):
        self.candidate['scope'] = 'diagnostic_outside_production_gates'
        self.save()
        with self.assertRaisesRegex(ValueError, 'outside production gates'):
            self.check()

    def test_diagnostic_source_scope_cannot_be_promoted_by_manifest_wrapper(self):
        self.observed['diagnostic_scope'] = 'existing_manual_observation_subset_no_dense_ground_truth'
        self.save()
        with self.assertRaisesRegex(ValueError, 'cannot be wrapped'):
            self.check()

    def test_candidate_numeric_data_must_pin_job_rules_revision(self):
        for key in ['job_id', 'standards_sha256', 'skill_revision']:
            with self.subTest(key=key):
                original = self.candidate[key]
                del self.candidate[key]
                self.save()
                with self.assertRaisesRegex(ValueError, 'Candidate numerical job/rules/revision'):
                    self.check()
                self.candidate[key] = original

    def test_missing_preview_frame_blocks_full_range_claim(self):
        self.preview['frames'].pop()
        self.save()
        with self.assertRaisesRegex(ValueError, 'Full assigned-range'):
            self.check()

    def test_preview_partial_status_rejected_even_all_files_present(self):
        self.preview['status'] = 'RENDERING'
        self.save()
        with self.assertRaisesRegex(ValueError, 'status must be COMPLETE'):
            self.check()

    def test_preview_job_rules_revision_binding_rejected(self):
        for key in ['job_id', 'standards_sha256', 'skill_revision']:
            with self.subTest(key=key):
                original = self.preview[key]
                self.preview[key] = '0' * len(original)
                self.save()
                with self.assertRaisesRegex(ValueError, 'job/rules/revision binding'):
                    self.check()
                self.preview[key] = original

    def test_preview_changed_image_bytes_rejected(self):
        (self.root / 'qa/frame_0003.png').write_bytes(b'DIFFERENT_IMAGE')
        with self.assertRaisesRegex(ValueError, 'Changed evidence artifact: preview PNG'):
            self.check()

    def test_png_header_only_is_not_an_actual_preview(self):
        p = self.root / 'qa/frame_0003.png'
        p.write_bytes(p.read_bytes()[:24])
        self.preview['frames'][2]['sha256'] = common.sha256(p)
        self.save()
        with self.assertRaisesRegex(ValueError, 'Truncated preview PNG'):
            self.check()

    def test_png_crc_corruption_rejected_even_new_sha_matches(self):
        p = self.root / 'qa/frame_0003.png'
        data = bytearray(p.read_bytes())
        data[20] ^= 1
        p.write_bytes(data)
        self.preview['frames'][2]['sha256'] = common.sha256(p)
        self.save()
        with self.assertRaisesRegex(ValueError, 'CRC'):
            self.check()

    def test_png_wrong_dimensions_rejected(self):
        p = self.root / 'qa/frame_0003.png'
        png(p, 32, 18)
        self.preview['frames'][2]['sha256'] = common.sha256(p)
        self.save()
        with self.assertRaisesRegex(ValueError, 'dimensions mismatch'):
            self.check()

    def test_preview_source_pts_shift_rejected(self):
        self.preview['frames'][2]['pts_seconds'] += 1 / 24
        self.save()
        with self.assertRaisesRegex(ValueError, 'PTS mismatch'):
            self.check()

    def test_preview_native_upscale_or_changed_aspect_rejected(self):
        for w, h in [(7680, 4320), (16, 10)]:
            with self.subTest(size=(w, h)):
                self.preview['width'], self.preview['height'] = w, h
                self.save()
                with self.assertRaisesRegex(ValueError, 'aspect ratio without upscale'):
                    self.check()

    def test_artifact_path_traversal_and_windows_drive_rejected(self):
        for value in ['../outside.json', 'C:/fake/data.json', 'qa/data.json:secret']:
            with self.subTest(path=value):
                self.evidence['observations']['path'] = value
                common.write_json(self.evidence_path, self.evidence)
                with self.assertRaisesRegex(ValueError, 'relative artifact path'):
                    self.check()

    def test_output_cannot_overwrite_inputs(self):
        with self.assertRaisesRegex(ValueError, 'overwrite an input'):
            self.check(self.root / 'qa/observations.json')

    def test_wrong_saved_scene_path_rejected(self):
        self.check(self.report_path)
        p = self.root / 'other.blend'
        p.write_bytes((self.root / 'scene.blend').read_bytes())
        with self.assertRaisesRegex(ValueError, 'different scene path'):
            mc.validate_report(self.job_path, self.report_path, p)


if __name__ == '__main__':
    unittest.main()

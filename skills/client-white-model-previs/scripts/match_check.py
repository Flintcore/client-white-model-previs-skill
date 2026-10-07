"""Numerical source-match precheck, not a tracker, solver or visual approval.

``check --job JOB --evidence JOB/qa/evidence.json`` writes match_qa.json.
All evidence references are bounded job-relative paths with actual SHA-256.
The evidence schema ``client-white-model-match-evidence.v1`` contains
``bindings`` (job/source/template/standards/revision/blend), ``scene_path``
and observations/candidate/calibration/preview artifact descriptors.

Observations/candidate schemas end in match-observations.v1/match-candidate.v1:
  source_sha256; candidate also blend_sha256;
  production observations scope 'source_observations_not_visual_acceptance';
  production candidate scope 'production_candidate_measurements_not_visual_acceptance'
  or 'production_projection_only_not_visual_acceptance', plus job_id,
  standards_sha256 and skill_revision. Diagnostic exports stay diagnostic;
  space: {width, height, to_source: [a,b,c,d,e,f],
          source_rect:[x,y,width,height] (optional explicit crop)};
  frames: [{frame:1, source_frame:0, pts_seconds:0, background:[
    {id, xy:[x,y], split:'fit'|'holdout', confidence, provenance:'observed'}],
    actors:{A:{bbox:[x,y,w,h],bbox_provenance:'observed',bbox_confidence:1,
      visibility:{state:'visible'|'occluded'|
      'offscreen'|'unknown',confidence,provenance}, joints:[
      {id,xy,state,confidence,provenance}]}},
    occlusion:[{front:'A',back:'B',confidence,provenance}]}].
Only confident *observed* source entries are ground truth; inferred entries
remain diagnostic. Candidate provenance is 'extracted' or 'observed'. Source
frame 0 maps to Blender frame 1. PTS is assigned-clip-relative, not 30 fps.

Calibration (match-calibration.v1) declares status diagnostic_calibrated or
client_calibrated, calibrator, basis, source_sha256, actors, thresholds and
motion_checks/handheld_checks. It does not invent client-approved tolerances.
See REQUIRED_THRESHOLDS and evaluate() below for the exact metric contract.
Preview (match-preview.v1) binds the source/scene and actual complete PNGs:
  width,height,fps_num,fps_den; frames:[{frame,source_frame,pts_seconds,
    path:relative-to-preview-manifest,sha256,width,height}].
Lower-resolution, same-aspect previews are allowed; upscales/resampling are not.

Hashes attest the evidence bytes/identity, not the honesty of annotations.
This module measures declared numerical evidence and its coverage. It never
certifies hidden source limbs, 3D contact, light matching, human viewing or
client acceptance. Those require the other gates and independent review.
"""
import argparse
import json
import math
import re
import struct
import sys
import zlib
from pathlib import Path

from common import load_job, read_json, sha256, write_json


PREFIX = 'client-white-model-'
SCOPE = 'TECHNICAL_PRECHECK_NOT_VISUAL_OR_CLIENT_ACCEPTANCE'
CANDIDATE_SCOPES = {'production_candidate_measurements_not_visual_acceptance',
                    'production_projection_only_not_visual_acceptance'}
REQUIRED_THRESHOLDS = {
    'min_confidence', 'min_background_points_per_frame',
    'min_holdout_points_per_frame', 'min_visible_joints_per_actor',
    'max_background_p90_px', 'max_background_frame_rms_px', 'max_joint_nme',
    'max_motion_amplitude_relative_error', 'max_motion_amplitude_nme',
    'max_peak_lag_frames', 'min_handheld_amplitude_ratio',
    'max_handheld_amplitude_ratio', 'max_handheld_lag_frames',
    'max_static_residual_px', 'min_handheld_signal_px',
    'handheld_search_frames', 'min_handheld_correlation',
    'max_visibility_event_lag_frames',
}
INTEGER_THRESHOLDS = {
    'min_background_points_per_frame', 'min_holdout_points_per_frame',
    'min_visible_joints_per_actor', 'max_peak_lag_frames',
    'max_handheld_lag_frames', 'handheld_search_frames',
    'max_visibility_event_lag_frames',
}
STATES = {'visible', 'occluded', 'offscreen', 'unknown'}


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Finite numeric value required: ' + label)
    return float(value)


def integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('Integer required: ' + label)
    return value


def bounded(root, value):
    # Explicitly reject drive/ADS syntax even when this code runs on POSIX.
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('Portable bounded relative artifact path required')
    p = Path(value)
    if p.is_absolute() or '..' in p.parts:
        raise ValueError('Bounded relative artifact path required')
    root = Path(root).resolve()
    target = (root / p).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError('Artifact escapes job directory') from error
    if not target.is_file():
        raise FileNotFoundError(target)
    return target


def descriptor(root, value, label):
    if not isinstance(value, dict) or not re.fullmatch('[0-9a-f]{64}', value.get('sha256', '')):
        raise ValueError('Actual hashed artifact required: ' + label)
    p = bounded(root, value.get('path'))
    if sha256(p) != value['sha256']:
        raise ValueError('Changed evidence artifact: ' + label)
    return p


def artifact_ref(root, path):
    p = Path(path).resolve()
    return {'path': p.relative_to(Path(root).resolve()).as_posix(), 'sha256': sha256(p)}


def schema(data, suffix):
    if not isinstance(data, dict) or data.get('schema') != PREFIX + suffix + '.v1':
        raise ValueError('Unsupported schema: ' + suffix)


def space(data, source):
    s = data.get('space', {})
    w = integer(s.get('width'), 'space.width', 1)
    h = integer(s.get('height'), 'space.height', 1)
    matrix = s.get('to_source')
    if not isinstance(matrix, list) or len(matrix) != 6:
        raise ValueError('Explicit proxy-to-source affine transform required')
    m = [number(v, 'to_source') for v in matrix]
    if abs(m[0] * m[4] - m[1] * m[3]) < 1e-12:
        raise ValueError('Singular proxy transform')
    # A tiny, unit-normalized transform can otherwise falsify native-pixel
    # tolerances. Full-frame defaults must map the full source exactly; an
    # explicit crop maps its declared source-pixel rectangle, never an implicit
    # unit plane. Rotation/shear/letterbox need an explicit adapter first.
    rect = s.get('source_rect', [0, 0, source['width'], source['height']])
    if not isinstance(rect, list) or len(rect) != 4:
        raise ValueError('Explicit source pixel crop rectangle required')
    rx, ry, rw, rh = [number(v, 'source_rect') for v in rect]
    if min(rw, rh) <= 0 or min(rx, ry) < 0 or rx + rw > source['width'] or ry + rh > source['height']:
        raise ValueError('Source crop rectangle exceeds source pixel plane')
    expected = [(rx, ry), (rx + rw, ry), (rx, ry + rh), (rx + rw, ry + rh)]
    for (x, y), target in zip([(0, 0), (w, 0), (0, h), (w, h)], expected):
        px, py = project([x, y], m)
        if not -1e-6 <= px <= source['width'] + 1e-6 or not -1e-6 <= py <= source['height'] + 1e-6:
            raise ValueError('Proxy transform exceeds source pixel plane')
        if math.hypot(px - target[0], py - target[1]) > 1e-6:
            raise ValueError('Proxy transform does not map declared source-pixel rectangle')
    return m


def project(xy, matrix):
    if not isinstance(xy, list) or len(xy) != 2:
        raise ValueError('Two finite pixel coordinates required')
    x, y = [number(v, 'pixel coordinate') for v in xy]
    a, b, c, d, e, f = matrix
    return (a * x + b * y + c, d * x + e * y + f)


def indexing(data, source):
    result = {}
    matrix = space(data, source)
    n = source['frame_count']
    rows = data.get('frames')
    if not isinstance(rows, list):
        raise ValueError('Numerical per-frame observations required, not pass booleans')
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Frame row must be an object')
        f = integer(row.get('frame'), 'frame', 1)
        if f > n or f in result:
            raise ValueError('Duplicate/out-of-range frame')
        if row.get('source_frame') != f - 1 or type(row.get('source_frame')) is not int:
            raise ValueError('Source frame 0 must map to Blender frame 1')
        pts = number(row.get('pts_seconds'), 'pts_seconds')
        expected = (f - 1) * source['fps_den'] / source['fps_num']
        if abs(pts - expected) > 1e-7:
            raise ValueError('Source PTS mismatch; never silently resample')
        if not isinstance(row.get('actors', {}), dict):
            raise ValueError('Actor identity mapping required')
        # Detect conflicting duplicate IDs before numerical matching.
        ids(row.get('background', []), 'background')
        for actor in row.get('actors', {}).values():
            if not isinstance(actor, dict):
                raise ValueError('Actor row must be an object')
            ids(actor.get('joints', []), 'joint')
        result[f] = row
    return result, matrix


def ids(rows, label):
    if not isinstance(rows, list):
        raise ValueError(label + ' observations must be a list')
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
            raise ValueError('Named numerical ' + label + ' observation required')
        if row['id'] in result:
            raise ValueError('Duplicate ' + label + ' identity')
        result[row['id']] = row
    return result


def confident(row, threshold, source=True):
    if not isinstance(row, dict) or not row:
        return False
    confidence = number(row.get('confidence'), 'confidence')
    if not 0 <= confidence <= 1:
        raise ValueError('Confidence must be within [0,1]')
    origin = row.get('provenance')
    allowed = {'observed'} if source else {'observed', 'extracted'}
    return confidence >= threshold and origin in allowed


def state(row):
    value = row.get('state')
    if value not in STATES:
        raise ValueError('Explicit visibility state required')
    return value


def rms(values):
    return math.sqrt(sum(v * v for v in values) / len(values)) if values else None


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def ranges(frames):
    result = []
    for f in sorted(set(frames)):
        if result and f == result[-1]['end'] + 1:
            result[-1]['end'] = f
        else:
            result.append({'start': f, 'end': f})
    return result


def normalization(actor, matrix):
    box = actor.get('bbox')
    if not isinstance(box, list) or len(box) != 4:
        raise ValueError('Observed actor bbox required for NME normalization')
    x, y, w, h = [number(v, 'bbox') for v in box]
    if w <= 0 or h <= 0:
        raise ValueError('Positive observed bbox width/height required')
    a = project([x, y], matrix)
    b = project([x + w, y + h], matrix)
    norm = math.hypot(b[0] - a[0], b[1] - a[1])
    if norm <= 1e-9:
        raise ValueError('Degenerate observed body normalization')
    return norm


def observed_bbox(actor, threshold):
    if actor.get('bbox') is None or actor.get('bbox_provenance') != 'observed':
        return False
    confidence = number(actor.get('bbox_confidence'), 'bbox_confidence')
    if not 0 <= confidence <= 1:
        raise ValueError('BBox confidence must be within [0,1]')
    return confidence >= threshold


def calibration_thresholds(calibration, source):
    schema(calibration, 'match-calibration')
    if calibration.get('source_sha256') != source.get('sha256'):
        raise ValueError('Calibration refers to another source')
    values = calibration.get('thresholds', {})
    if set(values) != REQUIRED_THRESHOLDS:
        raise ValueError('Explicit complete calibrated thresholds required')
    t = {}
    for k, v in values.items():
        t[k] = integer(v, k) if k in INTEGER_THRESHOLDS else number(v, k)
        if t[k] < 0:
            raise ValueError('Nonnegative threshold required: ' + k)
    if not 0 <= t['min_confidence'] <= 1:
        raise ValueError('Invalid confidence calibration')
    if not 0 <= t['min_handheld_correlation'] <= 1:
        raise ValueError('Invalid handheld correlation calibration')
    if not 0 < t['min_handheld_amplitude_ratio'] <= 1 <= t['max_handheld_amplitude_ratio']:
        raise ValueError('Source-relative handheld bounds must enclose 1, with positive lower bound')
    if min(t['min_background_points_per_frame'], t['min_holdout_points_per_frame'], t['min_visible_joints_per_actor']) < 1:
        raise ValueError('Coverage requirements must be positive')
    if t['min_holdout_points_per_frame'] > t['min_background_points_per_frame']:
        raise ValueError('Holdout requirement exceeds total background requirement')
    if t['handheld_search_frames'] < t['max_handheld_lag_frames']:
        raise ValueError('Handheld lag search must cover accepted tolerance')
    return t


def evaluate(observed, candidate, calibration, source):
    """Compute numbers/coverage from declared inputs; no artifact/job validation.

    Useful for *diagnostic* legacy adapters. Call check()/validate_report() for
    the production gate. A returned passed value here is NOT production proof.
    """
    schema(observed, 'match-observations')
    schema(candidate, 'match-candidate')
    for data in [observed, candidate]:
        if data.get('source_sha256') != source.get('sha256'):
            raise ValueError('Numerical observations refer to another source')
    t = calibration_thresholds(calibration, source)
    o, om = indexing(observed, source)
    c, cm = indexing(candidate, source)
    n = source['frame_count']
    failures = {}
    failure_frames = {}

    def fail(code, frames, detail):
        frames = sorted(set(frames))
        failures.setdefault((code, detail), set()).update(frames)
        failure_frames.setdefault(code, set()).update(frames)

    if calibration.get('status') not in {'diagnostic_calibrated', 'client_calibrated'} or not calibration.get('basis') or not calibration.get('calibrator'):
        fail('uncalibrated_thresholds', range(1, n + 1), 'Declared thresholds have no calibration status, basis or calibrator')
    actors = calibration.get('actors', [])
    if not isinstance(actors, list) or not actors or len(set(actors)) != len(actors) or any(not isinstance(a, str) or not a for a in actors):
        raise ValueError('Explicit expected actor identities required')
    if set(range(1, n + 1)) != set(o):
        fail('source_frame_coverage', set(range(1, n + 1)) - set(o), 'Missing actual source observation rows')
    if set(range(1, n + 1)) != set(c):
        fail('candidate_frame_coverage', set(range(1, n + 1)) - set(c), 'Missing actual candidate measurement rows')
    bg_samples, pose_samples, frame_metrics = [], [], []
    source_visibility, candidate_visibility = {a: {} for a in actors}, {a: {} for a in actors}
    source_joint_lookup, candidate_joint_lookup = {}, {}
    bg_lookup = {}
    occlusions = []
    inferred = {'background': 0, 'joint': 0, 'visibility': 0, 'occlusion': 0}
    for f in sorted(set(o) & set(c)):
        so, sc = o[f], c[f]
        if (set(so.get('actors', {})) | set(sc.get('actors', {}))) - set(actors):
            fail('unmapped_actor_identity', [f], 'Detected/candidate actor outside locked identity map')
        sb, cb = ids(so.get('background', []), 'background'), ids(sc.get('background', []), 'background')
        errors, holdout = [], []
        for point, row in sb.items():
            if row.get('provenance') == 'inferred':
                inferred['background'] += 1
            if not confident(row, t['min_confidence']):
                continue
            if row.get('split') not in {'fit', 'holdout'}:
                raise ValueError('Background source point needs fixed fit/holdout split')
            if point not in cb or not confident(cb[point], t['min_confidence'], False):
                fail('background_missing_candidate', [f], point)
                continue
            a, b = project(row['xy'], om), project(cb[point]['xy'], cm)
            error = math.hypot(a[0] - b[0], a[1] - b[1])
            errors.append(error)
            if row['split'] == 'holdout':
                holdout.append(error)
            bg_lookup[(f, point)] = (a, b)
            bg_samples.append({'frame': f, 'id': point, 'split': row['split'], 'error_source_px': error})
        if len(errors) < t['min_background_points_per_frame'] or len(holdout) < t['min_holdout_points_per_frame']:
            fail('background_observation_coverage', [f], 'Too few independent confident fit/holdout point matches')
        frms = rms(errors)
        if frms is not None and frms > t['max_background_frame_rms_px']:
            fail('background_frame_reprojection', [f], 'RMS source-pixel error exceeds calibrated bound')
        frame_pose = []
        for a in actors:
            oa, ca = so.get('actors', {}).get(a, {}), sc.get('actors', {}).get(a, {})
            ov, cv = oa.get('visibility', {}), ca.get('visibility', {})
            if ov.get('provenance') == 'inferred':
                inferred['visibility'] += 1
            if not confident(ov, t['min_confidence']) or state(ov) == 'unknown':
                fail('source_visibility_coverage', [f], a + ': visibility unobserved/uncertain')
                continue
            source_visibility[a][f] = state(ov)
            if not confident(cv, t['min_confidence'], False) or state(cv) == 'unknown':
                fail('candidate_visibility_coverage', [f], a)
            else:
                candidate_visibility[a][f] = state(cv)
                if state(ov) != state(cv):
                    fail('visibility_state_mismatch', [f], a)
            if state(ov) != 'visible':
                continue
            oj, cj = ids(oa.get('joints', []), 'joint'), ids(ca.get('joints', []), 'joint')
            norm = normalization(oa, om) if observed_bbox(oa, t['min_confidence']) else None
            if norm is None:
                fail('pose_normalization_unmeasured', [f], a + ': no observed bbox for normalized pose accuracy')
            matched = 0
            for j, row in oj.items():
                if row.get('provenance') == 'inferred':
                    inferred['joint'] += 1
                if state(row) != 'visible' or not confident(row, t['min_confidence']):
                    continue
                if j not in cj or not confident(cj[j], t['min_confidence'], False) or state(cj[j]) != 'visible':
                    fail('joint_missing_candidate', [f], a + '/' + j)
                    continue
                p, q = project(row['xy'], om), project(cj[j]['xy'], cm)
                error = math.hypot(p[0] - q[0], p[1] - q[1])
                nme = error / norm if norm is not None else None
                matched += 1
                if nme is not None:
                    frame_pose.append(nme)
                pose_samples.append({'frame': f, 'actor': a, 'joint': j, 'error_source_px': error, 'normalization_source_px': norm, 'nme': nme})
                source_joint_lookup[(f, a, j)] = (p, norm)
                candidate_joint_lookup[(f, a, j)] = (q, norm)
                if nme is not None and nme > t['max_joint_nme']:
                    fail('visible_joint_nme', [f], a + '/' + j)
            if matched < t['min_visible_joints_per_actor']:
                fail('source_joint_coverage', [f], a + ': too few confident visible joint matches')
        co = {}
        for row in sc.get('occlusion', []):
            pair = (row.get('front'), row.get('back'))
            if pair[0] not in actors or pair[1] not in actors or pair[0] == pair[1]:
                raise ValueError('Candidate occlusion must name two distinct known actors')
            if pair in co or tuple(reversed(pair)) in co:
                raise ValueError('Conflicting duplicate candidate occlusion pair')
            co[pair] = row
        seen_pairs = set()
        for row in so.get('occlusion', []):
            if row.get('provenance') == 'inferred':
                inferred['occlusion'] += 1
            if not confident(row, t['min_confidence']):
                continue
            pair = (row.get('front'), row.get('back'))
            if pair[0] not in actors or pair[1] not in actors or pair[0] == pair[1]:
                raise ValueError('Occlusion must name two distinct known actors')
            if pair in seen_pairs or tuple(reversed(pair)) in seen_pairs:
                raise ValueError('Conflicting duplicate source occlusion pair')
            seen_pairs.add(pair)
            matched = pair in co and confident(co[pair], t['min_confidence'], False)
            occlusions.append({'frame': f, 'front': pair[0], 'back': pair[1], 'matches': matched})
            if not matched:
                fail('occlusion_order_mismatch', [f], pair[0] + ' must be in front of ' + pair[1])
        # A bounding-box overlap is not silhouette ground truth. It is a signal
        # to request an observed semantic overlap/order annotation, not to guess.
        visible = [a for a in actors if source_visibility[a].get(f) == 'visible']
        for i, a in enumerate(visible):
            for b in visible[i + 1:]:
                aa = so['actors'][a].get('bbox') if observed_bbox(so['actors'][a], t['min_confidence']) else None
                bb = so['actors'][b].get('bbox') if observed_bbox(so['actors'][b], t['min_confidence']) else None
                if aa and bb and boxes_overlap(aa, bb) and (a, b) not in seen_pairs and (b, a) not in seen_pairs:
                    fail('source_occlusion_coverage', [f], a + '/' + b + ': source bbox overlap needs semantic order annotation')
        frame_metrics.append({'frame': f, 'background_matches': len(errors), 'holdout_matches': len(holdout), 'background_rms_source_px': frms, 'joint_samples': len(frame_pose), 'joint_nme_max': max(frame_pose) if frame_pose else None})
    holdout_errors = [r['error_source_px'] for r in bg_samples if r['split'] == 'holdout']
    fit_errors = [r['error_source_px'] for r in bg_samples if r['split'] == 'fit']
    for label, values in [('fit', fit_errors), ('holdout', holdout_errors)]:
        if not values:
            fail('background_' + label + '_unmeasured', range(1, n + 1), 'No independently declared ' + label + ' samples')
        elif percentile(values, .9) > t['max_background_p90_px']:
            fail('background_' + label + '_p90', [r['frame'] for r in bg_samples if r['split'] == label and r['error_source_px'] > t['max_background_p90_px']], 'P90 error exceeds native source-pixel threshold')

    motions = []
    checks = calibration.get('motion_checks', [])
    if not isinstance(checks, list):
        raise ValueError('motion_checks must be a list')
    if not checks:
        fail('motion_amplitude_phase_unmeasured', range(1, n + 1), 'No observable source-derived motion windows')
    motion_actors = {item.get('actor') for item in checks if isinstance(item, dict)}
    for actor in actors:
        if 'visible' in source_visibility[actor].values() and actor not in motion_actors:
            fail('actor_motion_window_unmeasured', [f for f, value in source_visibility[actor].items() if value == 'visible'], actor)
    seen = set()
    for item in checks:
        cid, a, j = item.get('id'), item.get('actor'), item.get('joint')
        start, end = window(item, n)
        axis = {'x': 0, 'y': 1}.get(item.get('axis'))
        event = item.get('event')
        if not cid or cid in seen or a not in actors or not j or axis is None or event not in {'max', 'min'}:
            raise ValueError('Unique motion ID, known actor/joint, pixel axis and max/min event required')
        seen.add(cid)
        frames = list(range(start, end + 1))
        if any((f, a, j) not in source_joint_lookup for f in frames):
            fail('motion_observation_coverage', frames, cid)
            motions.append({'id': cid, 'measured': False})
            continue
        sv = [source_joint_lookup[(f, a, j)][0][axis] for f in frames]
        cv = [candidate_joint_lookup[(f, a, j)][0][axis] for f in frames]
        norms = [source_joint_lookup[(f, a, j)][1] for f in frames]
        norm = sum(norms) / len(norms) if all(v is not None for v in norms) else None
        sa, ca = max(sv) - min(sv), max(cv) - min(cv)
        rel = abs(ca - sa) / sa if sa > 1e-9 else None
        abs_nme = abs(ca - sa) / norm if norm is not None else None
        if norm is None:
            fail('motion_normalization_unmeasured', frames, cid)
        fun = max if event == 'max' else min
        sp = [f for f, v in zip(frames, sv) if abs(v - fun(sv)) <= 1e-9]
        cp = [f for f, v in zip(frames, cv) if abs(v - fun(cv)) <= 1e-9]
        lag = min(abs(p - q) for p in sp for q in cp)
        motions.append({'id': cid, 'measured': True, 'source_amplitude_px': sa, 'candidate_amplitude_px': ca, 'relative_amplitude_error': rel, 'amplitude_error_nme': abs_nme, 'source_peak_frames': sp, 'candidate_peak_frames': cp, 'peak_lag_frames': lag})
        if (rel is not None and rel > t['max_motion_amplitude_relative_error']) or (abs_nme is not None and abs_nme > t['max_motion_amplitude_nme']):
            fail('motion_amplitude_mismatch', frames, cid)
        if lag > t['max_peak_lag_frames']:
            fail('motion_peak_timing', sorted(set(sp + cp)), cid)

    handheld = []
    checks = calibration.get('handheld_checks', [])
    if not isinstance(checks, list):
        raise ValueError('handheld_checks must be a list')
    if not checks:
        fail('camera_residual_amplitude_phase_unmeasured', range(1, n + 1), 'No source-derived static-background trajectory windows')
    seen = set()
    for item in checks:
        start, end = window(item, n)
        point_ids = item.get('background_ids', [])
        cid = item.get('id')
        if not cid or cid in seen or not isinstance(point_ids, list) or len(set(point_ids)) != len(point_ids) or len(point_ids) < 3:
            raise ValueError('Unique handheld ID and at least three distinct static background identities required')
        seen.add(cid)
        frames = list(range(start, end + 1))
        if len(frames) < 3 or any((f, p) not in bg_lookup for f in frames for p in point_ids):
            fail('camera_residual_observation_coverage', frames, cid)
            handheld.append({'id': cid, 'measured': False})
            continue
        sa = residuals([[bg_lookup[(f, p)][0] for p in point_ids] for f in frames])
        ca = residuals([[bg_lookup[(f, p)][1] for p in point_ids] for f in frames])
        sr, cr = vector_rms(sa), vector_rms(ca)
        ratio = cr / sr if sr >= t['min_handheld_signal_px'] and sr > 1e-9 else None
        lag, correlation = best_lag(sa, ca, t['handheld_search_frames']) if ratio is not None and cr > 1e-9 else (None, None)
        handheld.append({'id': cid, 'measured': True, 'metric': 'detrended_static_background_screen_trajectory', 'source_residual_rms_px': sr, 'candidate_residual_rms_px': cr, 'amplitude_ratio': ratio, 'best_lag_frames': lag, 'correlation': correlation})
        if ratio is None:
            if cr > t['max_static_residual_px']:
                fail('camera_invented_shake', frames, cid)
        else:
            if not t['min_handheld_amplitude_ratio'] <= ratio <= t['max_handheld_amplitude_ratio']:
                fail('camera_residual_amplitude_mismatch', frames, cid)
            if lag is None or abs(lag) > t['max_handheld_lag_frames']:
                fail('camera_residual_phase_mismatch', frames, cid)
            if correlation is None or correlation < t['min_handheld_correlation']:
                fail('camera_residual_trajectory_mismatch', frames, cid)

    events = []
    for a in actors:
        se, ce = transitions(source_visibility[a]), transitions(candidate_visibility[a])
        for direction in set(se) | set(ce):
            sf, cf = se.get(direction, []), ce.get(direction, [])
            if len(sf) != len(cf):
                fail('visibility_event_count', sorted(set(sf + cf)) or range(1, n + 1), a + ':' + direction)
            for p, q in zip(sf, cf):
                lag = abs(p - q)
                events.append({'actor': a, 'transition': direction, 'source_frame': p, 'candidate_frame': q, 'lag_frames': lag})
                if lag > t['max_visibility_event_lag_frames']:
                    fail('visibility_event_timing', [p, q], a + ':' + direction)
    all_failed_frames = set().union(*failure_frames.values()) if failure_frames else set()
    failure_rows = [{'code': code, 'detail': detail, 'frame_ranges': ranges(frames)}
                    for (code, detail), frames in failures.items()]
    return {'passed': not failures, 'scope': 'NUMERIC_DIAGNOSTIC_ONLY_NO_ARTIFACT_VALIDATION',
            'frame_count': n, 'threshold_status': calibration.get('status'),
            'failures': failure_rows, 'failed_ranges': ranges(all_failed_frames),
            'measured': {'background': {'fit_count': len(fit_errors), 'holdout_count': len(holdout_errors), 'fit_p90_source_px': percentile(fit_errors, .9), 'holdout_p90_source_px': percentile(holdout_errors, .9), 'all_median_source_px': percentile(fit_errors + holdout_errors, .5), 'samples': bg_samples},
                'visible_pose': {'count': len(pose_samples), 'normalized_sample_count': sum(r['nme'] is not None for r in pose_samples), 'nme_p90': percentile([r['nme'] for r in pose_samples if r['nme'] is not None], .9), 'samples': pose_samples},
                'motion': motions, 'camera_residual': handheld, 'visibility_events': events,
                'occlusion': occlusions, 'frames': frame_metrics, 'inferred_entries_excluded': inferred}}


def window(item, n):
    if not isinstance(item, dict):
        raise ValueError('Named frame window required')
    start, end = integer(item.get('start'), 'window.start', 1), integer(item.get('end'), 'window.end', 1)
    if end < start or end > n:
        raise ValueError('Window outside assigned source')
    return start, end


def boxes_overlap(a, b):
    if len(a) != 4 or len(b) != 4:
        raise ValueError('Four pixel bbox coordinates required')
    ax, ay, aw, ah = [number(v, 'bbox') for v in a]
    bx, by, bw, bh = [number(v, 'bbox') for v in b]
    return min(ax + aw, bx + bw) > max(ax, bx) and min(ay + ah, by + bh) > max(ay, by)


def residuals(frames):
    result = []
    for i, points in enumerate(frames):
        fraction = i / (len(frames) - 1)
        row = []
        for p, xy in enumerate(points):
            for axis in range(2):
                baseline = frames[0][p][axis] + fraction * (frames[-1][p][axis] - frames[0][p][axis])
                row.append(xy[axis] - baseline)
        result.append(row)
    return result


def vector_rms(rows):
    # RMS Euclidean residual per static anchor, in native source pixels.
    return math.sqrt(sum(v * v for row in rows for v in row) / (len(rows) * len(rows[0]) / 2))


def best_lag(source, candidate, radius):
    choices = []
    for lag in range(-min(radius, len(source) - 3), min(radius, len(source) - 3) + 1):
        pairs = [(source[i], candidate[i + lag]) for i in range(len(source)) if 0 <= i + lag < len(candidate)]
        # Avoid a tiny edge overlap winning by accident.
        if len(pairs) < max(3, math.ceil(len(source) * .8)):
            continue
        dot = sum(x * y for a, b in pairs for x, y in zip(a, b))
        aa = sum(x * x for a, _ in pairs for x in a)
        bb = sum(y * y for _, b in pairs for y in b)
        if aa > 1e-12 and bb > 1e-12:
            choices.append((dot / math.sqrt(aa * bb), -abs(lag), -lag, lag))
    if not choices:
        return None, None
    score, _, _, lag = max(choices)
    return lag, score


def transitions(states):
    result = {}
    for f in sorted(states):
        if f - 1 in states and states[f] != states[f - 1]:
            result.setdefault(states[f - 1] + '->' + states[f], []).append(f)
    return result


def preview_check(root, manifest_path, job, blend_hash):
    data = read_json(manifest_path)
    schema(data, 'match-preview')
    s = job['source']
    if data.get('status') != 'COMPLETE':
        raise ValueError('Preview actual rendering status must be COMPLETE')
    for key in ['job_id', 'standards_sha256', 'skill_revision']:
        if data.get(key) != job[key]:
            raise ValueError('Preview job/rules/revision binding mismatch')
    if data.get('source_sha256') != s['sha256'] or data.get('blend_sha256') != blend_hash:
        raise ValueError('Preview belongs to another source/scene')
    w, h = integer(data.get('width'), 'preview.width', 1), integer(data.get('height'), 'preview.height', 1)
    if w > s['width'] or h > s['height'] or w * s['height'] != h * s['width']:
        raise ValueError('Preview must preserve aspect ratio without upscale')
    if (data.get('fps_num'), data.get('fps_den')) != (s['fps_num'], s['fps_den']):
        raise ValueError('Preview source FPS mismatch')
    rows = data.get('frames', [])
    if not isinstance(rows, list) or len(rows) != s['frame_count']:
        raise ValueError('Full assigned-range actual PNG preview required')
    seen = set()
    for row in rows:
        f = integer(row.get('frame'), 'preview.frame', 1)
        if f in seen or f > s['frame_count']:
            raise ValueError('Duplicate/out-of-range preview frame')
        seen.add(f)
        if row.get('source_frame') != f - 1 or type(row.get('source_frame')) is not int:
            raise ValueError('Preview source/Blender frame mismatch')
        expected = (f - 1) * s['fps_den'] / s['fps_num']
        if abs(number(row.get('pts_seconds'), 'preview.pts_seconds') - expected) > 1e-7:
            raise ValueError('Preview source PTS mismatch')
        p = descriptor(manifest_path.parent, row, 'preview PNG')
        p.relative_to(root.resolve())
        if png_dimensions(p) != (w, h) or (row.get('width'), row.get('height')) != (w, h):
            raise ValueError('Actual preview PNG dimensions mismatch')
    return {'status': 'COMPLETE', 'frame_count': len(seen), 'width': w, 'height': h, 'all_frame_hashes_verified': True,
            'png_payload_structure_verified': True}


def png_dimensions(path):
    """Validate genuine non-interlaced RGB/RGBA PNG payloads, not a fake header."""
    data = Path(path).read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Actual PNG preview required')
    pos, ihdr, compressed, ended = 8, None, [], False
    while pos < len(data):
        if pos + 12 > len(data):
            raise ValueError('Truncated preview PNG')
        size = struct.unpack('>I', data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        end = pos + 12 + size
        if end > len(data):
            raise ValueError('Truncated preview PNG chunk')
        payload = data[pos + 8:pos + 8 + size]
        crc = struct.unpack('>I', data[end - 4:end])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            raise ValueError('Corrupt preview PNG chunk CRC')
        if ihdr is None and kind != b'IHDR':
            raise ValueError('Preview PNG needs first IHDR')
        if kind == b'IHDR':
            if ihdr is not None or size != 13:
                raise ValueError('Invalid preview PNG IHDR')
            ihdr = struct.unpack('>IIBBBBB', payload)
        elif kind == b'IDAT':
            compressed.append(payload)
        elif kind == b'IEND':
            if size or end != len(data):
                raise ValueError('Invalid preview PNG IEND')
            ended = True
            break
        pos = end
    if not ended or not compressed or ihdr is None:
        raise ValueError('Preview PNG has no complete pixel payload')
    w, h, depth, color, compression, filtering, interlace = ihdr
    if not w or not h or depth not in {8, 16} or color not in {2, 6} or compression or filtering or interlace:
        raise ValueError('Supported non-interlaced RGB/RGBA preview required')
    # Bound the decompression before allocation, including malformed bomb data.
    if w > 16384 or h > 16384:
        raise ValueError('Unbounded preview image dimensions')
    row_size = w * (3 if color == 2 else 4) * (depth // 8) + 1
    expected = row_size * h
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(b''.join(compressed), expected + 1)
    except zlib.error as error:
        raise ValueError('Corrupt preview PNG compressed pixels') from error
    if len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('Preview PNG pixel payload length mismatch')
    if any(pixels[i] > 4 for i in range(0, len(pixels), row_size)):
        raise ValueError('Invalid preview PNG row filter')
    return w, h


def check(job_path, evidence_path, output=None, blend_path=None):
    job, root = load_job(job_path)
    evidence_path = Path(evidence_path).resolve()
    evidence_path.relative_to(root)
    evidence = read_json(evidence_path)
    schema(evidence, 'match-evidence')
    blend = bounded(root, evidence.get('scene_path'))
    blend_hash = sha256(blend)
    if blend_path is not None and Path(blend_path).resolve() != blend:
        raise ValueError('Match report refers to a different scene path')
    bindings = {'job_id': job['job_id'], 'source_sha256': job['source']['sha256'],
                'template_sha256': job['template']['sha256'], 'standards_sha256': job['standards_sha256'],
                'skill_revision': job['skill_revision'], 'blend_sha256': blend_hash}
    if evidence.get('bindings') != bindings:
        raise ValueError('Match evidence job/source/template/standards/revision/scene binding mismatch')
    files = {k: descriptor(root, evidence.get(k), k) for k in ['observations', 'candidate', 'calibration', 'preview']}
    observed, candidate, calibration = [read_json(files[k]) for k in ['observations', 'candidate', 'calibration']]
    if candidate.get('blend_sha256') != blend_hash:
        raise ValueError('Candidate numerical data is from another scene')
    if candidate.get('scope') not in CANDIDATE_SCOPES or observed.get('scope') != 'source_observations_not_visual_acceptance':
        raise ValueError('Diagnostic candidate/source annotations stay outside production gates')
    if any(data.get('diagnostic_scope') for data in [observed, candidate]):
        raise ValueError('Diagnostic-only annotations cannot be wrapped as production evidence')
    for key in ['job_id', 'standards_sha256', 'skill_revision']:
        if candidate.get(key) != job[key]:
            raise ValueError('Candidate numerical job/rules/revision binding mismatch')
    if len({evidence_path, blend, *files.values()}) != 6:
        raise ValueError('Distinct scene/evidence/numerical artifacts required')
    actor_ids = [a.get('id') for a in job.get('scene', {}).get('actors', [])]
    if not actor_ids or len(set(actor_ids)) != len(actor_ids) or set(calibration.get('actors', [])) != set(actor_ids):
        raise ValueError('Calibration must cover all locked scene actor identities')
    preview = preview_check(root, files['preview'], job, blend_hash)
    result = evaluate(observed, candidate, calibration, job['source'])
    result.update({'schema': PREFIX + 'match-qa.v1', 'scope': SCOPE, **bindings,
                   'evidence': artifact_ref(root, evidence_path),
                   'artifact_bindings': {k: artifact_ref(root, p) for k, p in files.items()},
                   'scene': artifact_ref(root, blend), 'preview': preview,
                   'approval': {'technical_precheck': result['passed'], 'independent_visual': False, 'client_acceptance': False},
                   'excluded_checks': ['3Dcontact_and_collision', 'lighting_match', 'hidden_source_limb_ground_truth', 'independent_visual_review']})
    if output is not None:
        target = Path(output).resolve()
        target.relative_to(root)
        protected = {Path(job_path).resolve(), evidence_path, blend, *files.values(),
                     bounded(root, job['source']['path']), bounded(root, job['template']['path'])}
        if target in protected:
            raise ValueError('Report output would overwrite an input artifact')
        write_json(target, result)
    return result


def validate_report(job_path, report_path, blend_path=None):
    """Render gate: actual SHA checks + full recomputation, never trust a flag."""
    _, root = load_job(job_path)
    path = Path(report_path).resolve()
    path.relative_to(root)
    data = read_json(path)
    schema(data, 'match-qa')
    evidence_path = descriptor(root, data.get('evidence'), 'match evidence')
    actual = check(job_path, evidence_path, blend_path=blend_path)
    if data != actual:
        raise ValueError('Match report does not equal current measured evidence')
    if actual['passed'] is not True:
        raise ValueError('Numerical source-match precheck failed: ' + ', '.join(sorted({i['code'] for i in actual['failures']})))
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('check')
    p.add_argument('--job', required=True)
    p.add_argument('--evidence', required=True)
    p.add_argument('--output')
    p = sub.add_parser('validate-report')
    p.add_argument('--job', required=True)
    p.add_argument('--report', required=True)
    p.add_argument('--blend')
    args = parser.parse_args()
    if args.command == 'check':
        output = args.output or str(Path(args.job).resolve().parent / 'match_qa.json')
        result = check(args.job, args.evidence, output=output)
        print(json.dumps({'passed': result['passed'], 'scope': result['scope'], 'report': str(Path(output).resolve()),
                          'failure_count': len(result['failures']), 'failed_ranges': result['failed_ranges']}, ensure_ascii=False))
        return 0 if result['passed'] else 2
    result = validate_report(args.job, args.report, args.blend)
    print(json.dumps({'passed': True, 'scope': result['scope'], 'job_id': result['job_id']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, TypeError, FileNotFoundError, RuntimeError) as error:
        print(json.dumps({'passed': False, 'scope': SCOPE, 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

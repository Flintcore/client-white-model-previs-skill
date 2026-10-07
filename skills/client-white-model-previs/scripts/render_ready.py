"""Validate actual engineering and measured preview evidence before native render."""
from pathlib import Path
from common import load_job, read_json, sha256


def validate_render_ready(job_path, blend_path, diagnostic_report, match_report):
    job, _ = load_job(job_path)
    blend = Path(blend_path).resolve()
    if not blend.is_file():
        raise ValueError('Save an editable scene before rendering')
    qa_path = Path(diagnostic_report).resolve()
    qa = read_json(qa_path)
    if (qa.get('schema') != 'client-white-model-blender-qa.v1'
            or qa.get('job_sha256') != sha256(job_path)
            or qa.get('passed') is not True or qa.get('blend_sha256') != sha256(blend)
            or qa.get('job_id') != job['job_id']
            or qa.get('standards_sha256') != job['standards_sha256']):
        raise ValueError('Current scene must pass actual engineering/physical QA first')
    rows = qa.get('checks', [])
    required = {'saved_blend', 'source_identity', 'template_identity', 'standards_identity',
        'support_inventory', 'actor_L3_and_foot_metadata', 'per_frame_gait_evidence',
        'actual_shin_cap_ground_support', 'actual_shin_cap_penetration',
        'stance_pinned_vertex_drift', 'readonly_blend_identity'}
    if (not isinstance(rows, list) or not rows
            or any(not isinstance(row, dict) or row.get('passed') is not True for row in rows)
            or not required <= {row.get('name') for row in rows}):
        raise ValueError('Complete passed actual engineering checks required')
    if not match_report:
        raise ValueError('Measured full-frame preview match report required before native render')
    # Recalculate from pinned observation/candidate/calibration/preview artifacts.
    # An unrelated JSON with passed=True is not a preview match certificate.
    from match_check import validate_report
    measured = validate_report(job_path, match_report, blend_path=blend)
    if measured.get('passed') is not True:
        raise ValueError('Current preview has unmatched or unverified source constraints')
    return {'job_id': job['job_id'], 'blend_sha256': sha256(blend),
            'engineering_report_sha256': sha256(qa_path),
            'match_report_sha256': sha256(Path(match_report)),
            'scope': 'RENDER_ENTRY_PRECHECK_NOT_FINAL_VISUAL_OR_CLIENT_ACCEPTANCE'}

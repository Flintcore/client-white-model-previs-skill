"""Actual full-frame cheap preview inside Blender; not native delivery or approval.

Preserves source frame rate, frame mapping and the saved scene bytes. Reduces
resolution/samples in memory only. Output is the match-preview.v1 contract;
partial results are deliberately not usable by match_check.
"""
import argparse
from fractions import Fraction
import math
from pathlib import Path
import struct
import sys
import time

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_job, sha256, write_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--percentage', type=int, default=25)
    p.add_argument('--samples', type=int, default=16)
    args = p.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    job, root = load_job(args.job)
    scene = bpy.context.scene
    blend = Path(bpy.data.filepath).resolve()
    blend.relative_to(root)
    if not blend.is_file():
        raise ValueError('A saved job-local scene is required')
    original_hash = sha256(blend)
    s, t, r = job['source'], job['timeline'], job['render']
    fps = Fraction(str(scene.render.fps)) / Fraction(str(scene.render.fps_base))
    if not math.isclose(float(fps), s['fps_num']/s['fps_den'], rel_tol=2e-7, abs_tol=1e-8):
        raise ValueError('Preview must preserve assigned source FPS')
    if (scene.render.resolution_x, scene.render.resolution_y,
        scene.render.resolution_percentage, scene.frame_start, scene.frame_end) != (
            r['width'], r['height'], 100, t['frame_start'], t['frame_end']):
        raise ValueError('Saved native dimensions/timeline differ from job')
    if scene.render.engine not in {'BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'}:
        raise ValueError('Preview profile requires Eevee')
    if not 1 <= args.percentage <= 50 or not 1 <= args.samples <= 64:
        raise ValueError('Preview requires 1..50 percent resolution and 1..64 samples')
    w, h = r['width'] * args.percentage // 100, r['height'] * args.percentage // 100
    if min(w, h) < 4 or w*s['height'] != h*s['width']:
        raise ValueError('Choose a preview percentage with exact source aspect and dimensions >=4')
    out = Path(args.output).resolve()
    out.relative_to(root)
    if out == root or out.relative_to(root).parts[0] in {'inputs', 'delivery'}:
        raise ValueError('Preview output must be separate from job inputs and client delivery')
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError('Preserve earlier preview; use a new empty job-local directory')
    out.mkdir(parents=True, exist_ok=True)
    scene.render.resolution_percentage = args.percentage
    scene.eevee.taa_render_samples = args.samples
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGB'
    manifest = {'schema': 'client-white-model-match-preview.v1', 'status': 'RENDERING',
        'source_sha256': s['sha256'], 'blend_sha256': original_hash,
        'job_id': job['job_id'], 'skill_revision': job['skill_revision'],
        'standards_sha256': job['standards_sha256'], 'width': w, 'height': h,
        'fps_num': s['fps_num'], 'fps_den': s['fps_den'], 'frames': [],
        'samples': args.samples, 'percentage': args.percentage,
        'executed_with': 'Blender bpy actual full-frame renderer',
        'upscaled': False, 'client_acceptance': False,
        'scope': 'INTERNAL_LOW_RESOLUTION_DIAGNOSTIC_NOT_DELIVERY'}
    target = out / 'preview_manifest.json'
    start = time.monotonic()
    try:
        for frame in range(t['frame_start'], t['frame_end']+1):
            png = out / f'frame_{frame:04d}.png'
            scene.frame_set(frame)
            scene.render.filepath = str(png)
            bpy.ops.render.render(write_still=True)
            with png.open('rb') as stream:
                header = stream.read(24)
            if (header[:8] != b'\x89PNG\r\n\x1a\n' or len(header) != 24
                    or struct.unpack('>II', header[16:24]) != (w, h)):
                raise ValueError('Actual preview PNG dimensions differ')
            manifest['frames'].append({'frame': frame, 'source_frame': frame-1,
                'pts_seconds': (frame-1)*s['fps_den']/s['fps_num'],
                'path': png.name, 'sha256': sha256(png), 'width': w, 'height': h})
            manifest['elapsed_seconds'] = time.monotonic()-start
            write_json(target, manifest)
            print(f'ACTUAL_PREVIEW_FRAME {frame}/{t["frame_end"]}', flush=True)
        if sha256(blend) != original_hash:
            raise ValueError('Saved scene changed during diagnostic preview')
        manifest['status'] = 'COMPLETE'
        manifest['elapsed_seconds'] = time.monotonic()-start
        manifest['average_seconds_per_frame'] = manifest['elapsed_seconds']/len(manifest['frames'])
    except Exception:
        manifest['status'] = 'FAILED'
        raise
    finally:
        write_json(target, manifest)


if __name__ == '__main__':
    main()

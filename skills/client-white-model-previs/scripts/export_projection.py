"""Read-only evaluated Blender projection exporter driven by explicit mapping.

Production: Blender --background --disable-autoexec SCENE --python-exit-code 1
  --python export_projection.py -- --job JOB --config CONFIG --output OUTPUT

Diagnostic: replace --job by --diagnostic-lock LOCK. Diagnostic outputs have no
job_id, are labelled outside-production-gates, and are not acceptance evidence.
This exports actual camera/bone/object projections, not rendered visibility,
source pose, contact/collision checks, lighting checks or client acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time

SHA = re.compile(r'^[0-9a-f]{64}$')
SCHEMA = 'client-white-model-projection-config.v1'


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024*1024), b''):
            h.update(part)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def bounded(root, value, *, must_exist=True):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or Path(value).is_absolute()):
        raise ValueError('Explicit bounded relative path required')
    if '..' in Path(value).parts:
        raise ValueError('Path traversal rejected')
    root = Path(root).resolve()
    path = (root/value).resolve()
    if root not in path.parents:
        raise ValueError('Path leaves bundle')
    if must_exist and not path.is_file():
        raise FileNotFoundError(path)
    return path


def within(root, path, *, must_exist=True):
    resolved = Path(path).resolve()
    root = Path(root).resolve()
    if root not in resolved.parents:
        raise ValueError('CLI path leaves bundle')
    if must_exist and not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value):
        raise ValueError('Finite number required')
    return float(value)


def vector(value):
    if not isinstance(value,list) or len(value)!=3:
        raise ValueError('Three-vector required')
    return [number(x) for x in value]


def validate_config(config):
    if not isinstance(config,dict) or config.get('schema')!=SCHEMA:
        raise ValueError('Wrong projection config schema')
    if not isinstance(config.get('camera'),str) or not config['camera']:
        raise ValueError('Explicit saved-camera object name required')
    s=config.get('space',{})
    if not isinstance(s,dict):
        raise ValueError('Projection space mapping required')
    for axis in ('width','height'):
        if type(s.get(axis)) is not int or s[axis]<1:
            raise ValueError('Positive integer projection dimensions required')
    seen=set()
    points=config.get('background',[])
    if not isinstance(points,list):
        raise ValueError('Background point list required')
    for point in points:
        if not isinstance(point,dict):
            raise ValueError('Background point mapping required')
        ident=point.get('id')
        if not isinstance(ident,str) or not ident or ident in seen:
            raise ValueError('Unique background IDs required')
        seen.add(ident)
        if point.get('split') not in ('fit','holdout'):
            raise ValueError('Explicit fit/holdout partition required')
        if ('world_xyz_m' in point)==('object_point' in point):
            raise ValueError('Exactly one fixed-world or actual-object point required')
        if 'world_xyz_m' in point:
            vector(point['world_xyz_m'])
            if not point.get('position_evidence'):
                raise ValueError('World point needs explicit surveyed/inferred provenance')
        else:
            item=point['object_point']
            if not isinstance(item,dict):
                raise ValueError('Background object point mapping required')
            if not isinstance(item.get('object'),str) or not item['object']:
                raise ValueError('Named actual object required')
            vector(item.get('local_xyz_m'))
    actors=config.get('actors',{})
    if not isinstance(actors,dict) or not actors:
        raise ValueError('Explicit actor mapping required')
    for aid, actor in actors.items():
        if not isinstance(aid,str) or not aid or not isinstance(actor,dict) or not isinstance(actor.get('object'),str) or not actor['object']:
            raise ValueError('Actor object mapping required')
        ids=set()
        if not isinstance(actor.get('joints'),list) or not actor['joints']:
            raise ValueError('At least one configured point per actor required')
        for joint in actor['joints']:
            if not isinstance(joint,dict):
                raise ValueError('Actor joint mapping required')
            ident=joint.get('id')
            if not isinstance(ident,str) or not ident or ident in ids:
                raise ValueError('Unique actor joint IDs required')
            ids.add(ident)
            if 'bone' in joint:
                if not isinstance(joint['bone'],str) or joint.get('endpoint') not in ('head','tail'):
                    raise ValueError('Explicit bone name and head/tail endpoint required')
                if 'local_xyz_m' in joint:
                    raise ValueError('Bone endpoint and local point are mutually exclusive')
            else:
                vector(joint.get('local_xyz_m'))
                if not joint.get('point_semantics'):
                    raise ValueError('Local object point needs explicit point semantics')
    return config


def require_rigid_local_point(obj):
    """Local points follow rigid object transforms, not evaluated deformation."""
    if len(getattr(obj,'modifiers',())) or getattr(getattr(obj,'data',None),'shape_keys',None) is not None:
        raise ValueError('Local points require rigid objects without modifiers or shape keys')


def diagnostic_bundle(lock_path):
    lock_path=Path(lock_path).resolve()
    root=lock_path.parent
    data=read_json(lock_path)
    if data.get('schema')!='client-white-model-projection-diagnostic-lock.v1':
        raise ValueError('Wrong independent diagnostic lock schema')
    for key in ('source','blend'):
        item=data.get(key,{})
        if not SHA.fullmatch(item.get('sha256','')):
            raise ValueError('Diagnostic input SHA required')
        actual=bounded(root,item['path'])
        if sha256(actual)!=item['sha256']:
            raise ValueError('Diagnostic input identity mismatch: '+key)
    source=data['source']
    for key in ('width','height','fps_num','fps_den','frame_count'):
        if type(source.get(key)) is not int or source[key]<1:
            raise ValueError('Actual diagnostic source dimensions/FPS/count required')
    t=data.get('timeline',{})
    if type(t.get('frame_start')) is not int or type(t.get('frame_end')) is not int:
        raise ValueError('Diagnostic frame range required')
    if t['frame_start']!=1 or not (1<=t['frame_end']<=source['frame_count']):
        raise ValueError('Diagnostic frame range outside source')
    return data,root


def export(args):
    import bpy
    from mathutils import Vector
    from bpy_extras.object_utils import world_to_camera_view

    t0=time.perf_counter()
    if args.job:
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        from common import load_job
        job,root=load_job(args.job)
        mode='production_projection_only_not_visual_acceptance'
        lock_path=Path(args.job).resolve()
    else:
        job,root=diagnostic_bundle(args.diagnostic_lock)
        mode='diagnostic_outside_production_gates'
        lock_path=Path(args.diagnostic_lock).resolve()
    config_path=within(root,args.config)
    config=validate_config(read_json(config_path))
    output=within(root,args.output,must_exist=False)
    if output.exists():
        raise ValueError('Choose a new projection artifact path')
    blend=within(root,bpy.data.filepath)
    if blend.suffix.lower()!='.blend':
        raise ValueError('A saved Blender candidate is required')
    source_path=bounded(root,job['source']['path'])
    protected={'blend':sha256(blend),'source':sha256(source_path),
               'config':sha256(config_path),'lock':sha256(lock_path)}
    if not args.job and protected['blend']!=job['blend']['sha256']:
        raise ValueError('Loaded Blend differs from diagnostic lock')
    if args.expected_blend_sha and protected['blend']!=args.expected_blend_sha:
        raise ValueError('Wrong loaded saved candidate SHA')
    if blend in [source_path,config_path,lock_path] or output in [blend,source_path,config_path,lock_path]:
        raise ValueError('Output/input collision rejected')
    if args.job:
        template_path=bounded(root,job['template']['path'])
        protected['template']=sha256(template_path)
        if output==template_path or blend==template_path:
            raise ValueError('Template is not a candidate/output')
    scene=bpy.context.scene
    unit_scale=number(scene.unit_settings.scale_length)
    if unit_scale<=0 or not math.isclose(unit_scale,1.,rel_tol=0.,abs_tol=1e-7):
        raise ValueError('Projection metre contract requires scene unit scale_length exactly 1')
    camera_object=bpy.data.objects.get(config['camera'])
    if camera_object is None or camera_object.type!='CAMERA' or scene.camera!=camera_object:
        raise ValueError('Configured camera must be the actual saved active camera')
    t=job['timeline']; s=job['source']
    if (scene.frame_start,scene.frame_end)!=(t['frame_start'],t['frame_end']):
        raise ValueError('Saved frame range differs from assigned range')
    fps=scene.render.fps/scene.render.fps_base
    if not math.isclose(fps,s['fps_num']/s['fps_den'],rel_tol=2e-7,abs_tol=1e-8):
        raise ValueError('Saved scene FPS differs from locked source')
    if (scene.render.resolution_x,scene.render.resolution_y)!=(job['render']['width'],job['render']['height']):
        raise ValueError('Actual saved project aspect/dimensions differ')
    if not math.isclose(scene.render.pixel_aspect_x/scene.render.pixel_aspect_y,1.,rel_tol=1e-7):
        raise ValueError('Square-pixel source mapping required')
    space=config['space']; w=space['width']; h=space['height']
    if w*s['height']!=h*s['width'] or w>s['width'] or h>s['height']:
        raise ValueError('Projection space must be same-aspect source or downscale')
    if scene.camera.data.type!='PERSP':
        raise ValueError('Current projection contract supports perspective camera only')
    rows=[]
    first_background={}
    for frame in range(t['frame_start'],t['frame_end']+1):
        scene.frame_set(frame)
        if scene.camera!=camera_object:
            raise ValueError('Active camera changed within fixed-camera projection range')
        dg=bpy.context.evaluated_depsgraph_get()
        camera=camera_object.evaluated_get(dg)
        row={'frame':frame,'source_frame':frame-1,
             'pts_seconds':(frame-1)*s['fps_den']/s['fps_num'],
             'background':[],'actors':{},'actual_camera_matrix_world':[list(r) for r in camera.matrix_world],
             'actual_lens_mm':camera.data.lens,'actual_sensor_width_mm':camera.data.sensor_width}
        def project(world):
            result=world_to_camera_view(scene,camera,world)
            xy=[float(result.x*w),float((1-result.y)*h)]
            if not all(math.isfinite(v) for v in xy+[result.z]):
                raise ValueError('Nonfinite actual projection')
            return {'xy':xy,'depth_m':float(result.z)}
        for point in config.get('background',[]):
            if 'world_xyz_m' in point:
                world=Vector(point['world_xyz_m'])
            else:
                spec=point['object_point']
                obj=bpy.data.objects.get(spec['object'])
                if obj is None:
                    raise ValueError('Configured background object missing')
                require_rigid_local_point(obj)
                world=obj.evaluated_get(dg).matrix_world @ Vector(spec['local_xyz_m'])
            if point['id'] in first_background and (world-first_background[point['id']]).length>1e-6:
                raise ValueError('Configured static background point moves in world space')
            first_background.setdefault(point['id'],world.copy())
            result=project(world)
            row['background'].append({'id':point['id'],**result,'split':point['split'],
                'confidence':1.,'provenance':'extracted','world_xyz_m':list(world),
                'position_evidence':point.get('position_evidence','actual_evaluated_static_object_point')})
        for aid, mapping in config['actors'].items():
            original=bpy.data.objects.get(mapping['object'])
            if original is None:
                raise ValueError('Configured actor object missing')
            obj=original.evaluated_get(dg)
            joints=[]
            for point in mapping['joints']:
                if 'bone' in point:
                    if obj.type!='ARMATURE' or point['bone'] not in obj.pose.bones:
                        raise ValueError('Configured evaluated pose bone missing')
                    bone=obj.pose.bones[point['bone']]
                    local=getattr(bone,point['endpoint'])
                else:
                    require_rigid_local_point(original)
                    local=Vector(point['local_xyz_m'])
                world=obj.matrix_world @ local
                result=project(world)
                x,y=result['xy']
                state='offscreen' if result['depth_m']<=0 or x<0 or x>=w or y<0 or y>=h else 'unknown'
                joints.append({'id':point['id'],**result,'world_xyz_m':list(world),
                    'state':state,'confidence':1.,'provenance':'extracted',
                    'visibility_basis':'projection_only_not_pixel_occlusion'})
            row['actors'][aid]={'joints':joints,'visibility':{
                'state':'unknown','confidence':0.,'provenance':'extracted',
                'method':'pixel_visibility_not_measured_by_projection_export'}}
        rows.append(row)
    after={'blend':sha256(blend),'source':sha256(source_path),
           'config':sha256(config_path),'lock':sha256(lock_path)}
    if args.job:
        after['template']=sha256(template_path)
    if after!=protected:
        raise RuntimeError('Protected candidate/source/config/lock changed during read-only extraction')
    result={'schema':'client-white-model-match-candidate.v1','source_sha256':s['sha256'],
        'blend_sha256':protected['blend'],'space':{'width':w,'height':h,
            'to_source':[s['width']/w,0,0,0,s['height']/h,0]},'frames':rows,
        'scope':mode,'extraction':{'all_frames_evaluated':len(rows),'blender_version':bpy.app.version_string,
            'config_sha256':protected['config'],'lock_sha256':protected['lock'],
            'exporter_sha256':sha256(__file__),'before':protected,'after':after,
            'saved_scene':False,'rendered_frames':0,'lighting_verified':False,
            'pixel_occlusion_verified':False,'source_pose_measured':False,
            'unit_scale_length':unit_scale,'scene_unit_system':scene.unit_settings.system,
            'coordinate_units':'one_Blender_unit_equals_one_metre; scene geometry may remain inferred',
            'local_object_points':'rigid_transform_only; any modifiers or shape keys rejected',
            'elapsed_seconds':time.perf_counter()-t0}}
    if args.job:
        result.update(job_id=job['job_id'],skill_revision=job['skill_revision'],
                      standards_sha256=job['standards_sha256'])
    output.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive creation protects immutable evidence from accidental overwrite.
    # A failed write is not a completed artifact and should never pass a gate.
    with output.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,separators=(',', ':'))
        stream.write('\n')
    return {'output':str(output),'sha256':sha256(output),'scope':mode,
            'actual_frames':len(rows),'extraction':result['extraction']}


def main():
    p=argparse.ArgumentParser()
    identity=p.add_mutually_exclusive_group(required=True)
    identity.add_argument('--job')
    identity.add_argument('--diagnostic-lock')
    p.add_argument('--config',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--expected-blend-sha')
    args=p.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:])
    if args.expected_blend_sha and not SHA.fullmatch(args.expected_blend_sha):
        raise ValueError('Expected Blend SHA must be a full SHA-256')
    print(json.dumps(export(args),ensure_ascii=False))


if __name__=='__main__':
    main()

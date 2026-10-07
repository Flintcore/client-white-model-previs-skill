"""Run inside Blender once for the full assigned frame range; no upscale."""
import argparse, math, struct, sys, time
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import *

def main():
    p=argparse.ArgumentParser();p.add_argument('--job',required=True);p.add_argument('--output',required=True)
    p.add_argument('--diagnostic-report',required=True)
    p.add_argument('--match-report',required=True)
    args=p.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    job,root=load_job(args.job);scene=bpy.context.scene;blend=Path(bpy.data.filepath)
    if not blend.is_file():raise ValueError('Save an editable scene before rendering')
    from render_ready import validate_render_ready
    ready=validate_render_ready(args.job,blend,args.diagnostic_report,args.match_report)
    blend_hash=ready['blend_sha256']
    r=job['render'];s=job['source'];t=job['timeline']
    from fractions import Fraction
    actual_fps=Fraction(str(scene.render.fps))/Fraction(str(scene.render.fps_base))
    # Blender stores fps_base as float32; exact rational text equality rejects
    # legitimate 30000/1001. Only accept its representational rounding tolerance.
    if not math.isclose(float(actual_fps),s['fps_num']/s['fps_den'],rel_tol=2e-7,abs_tol=1e-8):
        raise ValueError('Saved FPS differs from source')
    actual=(scene.render.resolution_x,scene.render.resolution_y,scene.render.resolution_percentage,
            scene.frame_start,scene.frame_end)
    if actual!=(r['width'],r['height'],100,t['frame_start'],t['frame_end']):raise ValueError('Saved native output/timeline mismatch')
    if scene.render.engine not in ['BLENDER_EEVEE','BLENDER_EEVEE_NEXT']:raise ValueError('Current profile requires Eevee')
    if scene.eevee.taa_render_samples!=64:raise ValueError('Render samples must be exactly 64')
    rt=getattr(scene.eevee,'use_raytracing',None)
    if not raytracing_matches(r,rt,job['profile']):raise ValueError('Saved ray tracing differs from locked render profile')
    out=Path(args.output).resolve()
    out.relative_to(root)
    if out==root or out.relative_to(root).parts[0] in {'inputs','delivery'}:
        raise ValueError('Render output must be a separate job-local diagnostic/render directory')
    if out.exists() and any(out.iterdir()):raise ValueError('Use a new empty render directory; avoid mixing revisions')
    out.mkdir(parents=True,exist_ok=True)
    output_width,output_height=delivery_dimensions(r)
    manifest={'schema':'client-white-model-render.v1','status':'RENDERING','job_id':job['job_id'],
        'blend_sha256':blend_hash,'standards_sha256':job['standards_sha256'],'skill_revision':job['skill_revision'],
        'engine':scene.render.engine,'resolution_percentage':100,'width':output_width,'height':output_height,
        'saved_project_width':r['width'],'saved_project_height':r['height'],
        'fps_num':s['fps_num'],'fps_den':s['fps_den'],'samples':64,'dark_scene':r['dark_scene'],
        'raytracing':rt,'frame_start':t['frame_start'],'frame_end':t['frame_end'],'completed':[],
        'engineering_report_sha256':ready['engineering_report_sha256'],
        'match_report_sha256':ready['match_report_sha256'],
        'executed_with':'Blender bpy actual renderer','upscaled':False}
    scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGB'
    # In-memory export override only. The saved editable 4K project is not saved.
    scene.render.resolution_x=output_width;scene.render.resolution_y=output_height
    start=time.monotonic()
    try:
        for frame in range(t['frame_start'],t['frame_end']+1):
            target=out/f'frame_{frame:04d}.png';scene.frame_set(frame);scene.render.filepath=str(target)
            bpy.ops.render.render(write_still=True)
            with open(target,'rb') as f:header=f.read(24)
            dims=struct.unpack('>II',header[16:24])
            if header[:8]!=b'\x89PNG\r\n\x1a\n' or dims!=(output_width,output_height):raise ValueError('Actual PNG differs from native delivery resolution')
            manifest['completed'].append({'frame':frame,'path':target.name,'sha256':sha256(target),'width':dims[0],'height':dims[1]})
            manifest['elapsed_seconds']=time.monotonic()-start;write_json(out/'render_manifest.json',manifest)
            print(f'ACTUAL_NATIVE_FRAME {frame}/{t["frame_end"]}',flush=True)
        if sha256(blend)!=blend_hash:raise ValueError('Input scene changed during render')
        if (sha256(args.diagnostic_report)!=ready['engineering_report_sha256'] or
                sha256(args.match_report)!=ready['match_report_sha256']):
            raise ValueError('Frozen pre-render reports changed during render')
        # Re-read evidence/artifacts as well: a stable report file is not proof
        # that its referenced preview/observations remained unchanged.
        validate_render_ready(args.job,blend,args.diagnostic_report,args.match_report)
        manifest['status']='COMPLETE';manifest['elapsed_seconds']=time.monotonic()-start
        manifest['average_seconds_per_frame']=manifest['elapsed_seconds']/len(manifest['completed'])
    except Exception:
        manifest['status']='FAILED';raise
    finally:write_json(out/'render_manifest.json',manifest)

if __name__=='__main__':main()

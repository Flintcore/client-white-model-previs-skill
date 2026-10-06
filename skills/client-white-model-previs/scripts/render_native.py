"""Run inside Blender once for the full assigned frame range; no upscale."""
import argparse, math, struct, sys, time
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import *

def main():
    p=argparse.ArgumentParser();p.add_argument('--job',required=True);p.add_argument('--output',required=True)
    p.add_argument('--diagnostic-report',required=True)
    args=p.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    job,root=load_job(args.job);scene=bpy.context.scene;blend=Path(bpy.data.filepath)
    if not blend.is_file():raise ValueError('Save an editable scene before rendering')
    blend_hash=sha256(blend);qa=read_json(args.diagnostic_report)
    if (not qa.get('passed') or qa.get('blend_sha256')!=blend_hash or qa.get('job_id')!=job['job_id']
        or qa.get('standards_sha256')!=job['standards_sha256']):
        raise ValueError('Current scene must pass actual independent engineering/physical QA first')
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
    rt=bool(getattr(scene.eevee,'use_raytracing',False))
    if r['dark_scene'] and not rt:raise ValueError('Dark scene requires ray tracing')
    out=Path(args.output).resolve()
    if out.exists() and any(out.iterdir()):raise ValueError('Use a new empty render directory; avoid mixing revisions')
    out.mkdir(parents=True,exist_ok=True)
    manifest={'schema':'client-white-model-render.v1','status':'RENDERING','job_id':job['job_id'],
        'blend_sha256':blend_hash,'standards_sha256':job['standards_sha256'],'skill_revision':job['skill_revision'],
        'engine':scene.render.engine,'resolution_percentage':100,'width':r['width'],'height':r['height'],
        'fps_num':s['fps_num'],'fps_den':s['fps_den'],'samples':64,'dark_scene':r['dark_scene'],
        'raytracing':rt,'frame_start':t['frame_start'],'frame_end':t['frame_end'],'completed':[],
        'executed_with':'Blender bpy actual renderer','upscaled':False}
    scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGB'
    start=time.monotonic()
    try:
        for frame in range(t['frame_start'],t['frame_end']+1):
            target=out/f'frame_{frame:04d}.png';scene.frame_set(frame);scene.render.filepath=str(target)
            bpy.ops.render.render(write_still=True)
            with open(target,'rb') as f:header=f.read(24)
            dims=struct.unpack('>II',header[16:24])
            if header[:8]!=b'\x89PNG\r\n\x1a\n' or dims!=(r['width'],r['height']):raise ValueError('Actual PNG is not native resolution')
            manifest['completed'].append({'frame':frame,'path':target.name,'sha256':sha256(target),'width':dims[0],'height':dims[1]})
            manifest['elapsed_seconds']=time.monotonic()-start;write_json(out/'render_manifest.json',manifest)
            print(f'ACTUAL_NATIVE_FRAME {frame}/{t["frame_end"]}',flush=True)
        if sha256(blend)!=blend_hash:raise ValueError('Input scene changed during render')
        manifest['status']='COMPLETE';manifest['elapsed_seconds']=time.monotonic()-start
        manifest['average_seconds_per_frame']=manifest['elapsed_seconds']/len(manifest['completed'])
    except Exception:
        manifest['status']='FAILED';raise
    finally:write_json(out/'render_manifest.json',manifest)

if __name__=='__main__':main()

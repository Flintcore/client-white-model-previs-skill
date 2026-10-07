"""Native media verification/encoding. Packaging is gated separately."""
import argparse, json, shutil, struct, sys
from fractions import Fraction
from pathlib import Path
from common import *

def delivery_root(job,root):
    name=job['project_name']
    if not re.fullmatch(r'[\w.-]+',name,flags=re.UNICODE) or name in ['.','..']:raise ValueError('Invalid project name')
    return root/'delivery'/name

def verify_native(job,blend,renders):
    manifest_path=Path(renders)/'render_manifest.json';m=read_json(manifest_path)
    t=job['timeline'];r=job['render'];s=job['source'];expected=list(range(t['frame_start'],t['frame_end']+1))
    width,height=delivery_dimensions(r)
    if (m.get('schema')!='client-white-model-render.v1' or m.get('status')!='COMPLETE' or m.get('job_id')!=job['job_id'] or
        m.get('skill_revision')!=job['skill_revision'] or
        m.get('standards_sha256')!=job['standards_sha256'] or m.get('blend_sha256')!=sha256(blend)):
        raise ValueError('Native render manifest not bound to this actual job/scene')
    for key,value in {'width':width,'height':height,'resolution_percentage':100,'samples':64,
                      'frame_start':t['frame_start'],'frame_end':t['frame_end'],
                      'fps_num':s['fps_num'],'fps_den':s['fps_den'],'upscaled':False}.items():
        if m.get(key)!=value:raise ValueError('Native manifest field differs: '+key)
    if job['profile']=='client-4k-project-1080p' and (m.get('saved_project_width'),m.get('saved_project_height'))!=(r['width'],r['height']):
        raise ValueError('Saved 4K project provenance differs')
    if m.get('engine') not in ['BLENDER_EEVEE','BLENDER_EEVEE_NEXT'] or not raytracing_matches(r,m.get('raytracing'),job['profile']):
        raise ValueError('Engine/ray tracing evidence mismatch')
    rows=m.get('completed',[])
    if [x['frame'] for x in rows]!=expected:raise ValueError('Native frame evidence incomplete/duplicated')
    files=sorted(Path(renders).glob('frame_*.png'))
    if [x.name for x in files]!=[f'frame_{i:04d}.png' for i in expected]:raise ValueError('Actual native frame set differs')
    for row,file in zip(rows,files):
        with open(file,'rb') as f:header=f.read(24)
        if header[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',header[16:24])!=(width,height):
            raise ValueError('Actual PNG is not native size: '+str(file))
        if row.get('path')!=file.name or row.get('sha256')!=sha256(file):raise ValueError('Native frame identity differs')
        if (row.get('width'),row.get('height'))!=(width,height):raise ValueError('Native row dimensions differ')
    return artifact(manifest_path,'renders')

def audio_hashes(path,ffprobe):
    p=json.loads(run([ffprobe,'-v','error','-select_streams','a','-show_packets','-show_data_hash','sha256',
          '-show_entries','packet=stream_index,pts_time,dts_time,data_hash','-of','json',path]))
    return [(x['stream_index'],x.get('pts_time'),x['data_hash']) for x in p.get('packets',[])]

def stage(job_path):
    job,root=load_job(job_path);out=delivery_root(job,root);out.mkdir(parents=True,exist_ok=True)
    original=out/(job['project_name']+'.mp4');source=relative_file(root,job['source']['path'])
    if original.exists() and sha256(original)!=sha256(source):raise ValueError('Existing staged original differs; preserve revision')
    if not original.exists():shutil.copy2(source,original)
    return {'original':str(original),'save_editable_blend_to':str(out/(job['project_name']+'.blend')),
            'relative_reference':f"//{job['project_name']}.mp4",'status':'ORIGINAL_STAGED_NOT_RENDERED'}

def encode(args):
    job,root=load_job(args.job);out=delivery_root(job,root);base=job['project_name']
    blend=Path(args.blend).resolve();expected=(out/(base+'.blend')).resolve()
    if blend!=expected:raise ValueError('Save final scene beside the staged original before independent QA/render')
    original=out/(base+'.mp4');source=relative_file(root,job['source']['path'])
    if not original.exists() or sha256(original)!=job['source']['sha256']:raise ValueError('Staged source identity differs')
    qa=read_json(args.blender_report)
    if not qa.get('passed') or qa.get('blend_sha256')!=sha256(blend) or qa.get('job_id')!=job['job_id'] or qa.get('standards_sha256')!=job['standards_sha256']:
        raise ValueError('Final actual Blender/physical QA must match this scene')
    # --renders must have been checked in an independent reopen, not just here.
    if not qa.get('render_evidence',{}).get('passed'):raise ValueError('Independent full native render evidence required')
    portable=qa.get('final_bundle_portability',{})
    if portable.get('applicable') is not True or portable.get('passed') is not True:
        raise ValueError('Final four-file scene dependency portability must be verified')
    native=verify_native(job,blend,args.renders)
    info=video_info(original,args.ffprobe);s=job['source'];n=s['frame_count'];fps=f"{s['fps_num']}/{s['fps_den']}"
    if any(info[k]!=s[k] for k in ['width','height','fps_num','fps_den','frame_count']):raise ValueError('Assigned source changed')
    white=out/(base+' 白模.mp4');comparison=out/(base+' 对比.mp4')
    if white.exists() or comparison.exists():raise ValueError('Keep prior media; choose a new revision job before encoding')
    common=['-frames:v',str(n),'-c:v','libx264','-crf','18','-preset','fast','-pix_fmt','yuv420p',
            '-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-movflags','+faststart']
    # Original audio is copied, not replaced with music, silence, or alert sounds.
    run([args.ffmpeg,'-hide_banner','-v','error','-y','-framerate',fps,'-start_number',job['timeline']['frame_start'],
         '-i',Path(args.renders)/'frame_%04d.png','-i',original,'-map','0:v:0','-map','1:a?',
         *common,'-c:a','copy',white])
    r=job['render']
    width,height=delivery_dimensions(r)
    top_scale=f",scale={width}:{height}:flags=lanczos" if (s['width'],s['height'])!=(width,height) else ''
    run([args.ffmpeg,'-hide_banner','-v','error','-y','-i',original,'-i',white,
         '-filter_complex',f'[0:v]setpts=PTS-STARTPTS{top_scale},setsar=1[top];[1:v]setpts=PTS-STARTPTS,setsar=1[bottom];[top][bottom]vstack=inputs=2[v]',
         '-map','[v]','-map','0:a?',*common,'-c:a','copy',comparison])
    results=[];source_audio=audio_hashes(original,args.ffprobe)
    for path,width,height,kind in [(original,s['width'],s['height'],'original'),(white,width,height,'white'),(comparison,width,height*2,'comparison')]:
        q=video_info(path,args.ffprobe)
        if (q['width'],q['height'],q['fps_num'],q['fps_den'],q['frame_count'],q['codec'])!=(width,height,s['fps_num'],s['fps_den'],n,'h264'):
            raise ValueError('Encoded media spec differs: '+str(path))
        full_decode(path,args.ffmpeg)
        # Relative audio stream indices vary with remuxing; payload and timing must not.
        a=[(x[1],x[2]) for x in audio_hashes(path,args.ffprobe)]
        b=[(x[1],x[2]) for x in source_audio]
        if a!=b:raise ValueError('Audio payload/PTS differs from reference: '+str(path))
        item=artifact(path,kind);item.update(video=q,full_decode_pass=True,audio_identity_pass=True)
        results.append(item)
    results.append(artifact(blend,'blend'))
    report={'schema':'client-white-model-media.v1','passed':True,'job_id':job['job_id'],
        'standards_sha256':job['standards_sha256'],'skill_revision':job['skill_revision'],
        'blend_sha256':sha256(blend),'source_sha256':sha256(source),'artifacts':results,
        'native_render':native,'source_audio_packet_count':len(source_audio),
        'comparison_top_original_bottom_white':True,'no_upscale':True,'visual_approval':False}
    write_json(args.report,report);return {'media_report':str(args.report),'media_verified':True,'visual_approval':False}

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('stage');a.add_argument('--job',required=True)
    a=sub.add_parser('encode');a.add_argument('--job',required=True);a.add_argument('--blend',required=True)
    a.add_argument('--renders',required=True);a.add_argument('--blender-report',required=True);a.add_argument('--report',required=True)
    a.add_argument('--ffmpeg',default='ffmpeg');a.add_argument('--ffprobe',default='ffprobe')
    a=sub.add_parser('verify-native');a.add_argument('--job',required=True);a.add_argument('--blend',required=True);a.add_argument('--renders',required=True)
    args=p.parse_args()
    if args.command=='stage':out=stage(args.job)
    elif args.command=='encode':out=encode(args)
    else:
        job,_=load_job(args.job);out=verify_native(job,args.blend,args.renders)
    print(json.dumps(out,ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,FileNotFoundError,RuntimeError) as error:
        print(json.dumps({'passed':False,'error':str(error)},ensure_ascii=False),file=sys.stderr);sys.exit(1)

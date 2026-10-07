"""Create a locked local production job or an unapproved review worksheet."""
import argparse, json, shutil, subprocess, sys
from fractions import Fraction
from pathlib import Path
from common import *

def pixel_hashes(path,end,ffmpeg):
    out=run([ffmpeg,'-hide_banner','-v','error','-i',path,'-map','0:v:0','-an',
             '-vf',f'trim=end_frame={end}','-f','framemd5','-'])
    return [line.rsplit(',',1)[-1].strip() for line in out.splitlines() if line and not line.startswith('#')]

def create_job(args):
    source=Path(args.source).resolve();template=Path(args.template).resolve();root=Path(args.output).resolve()
    if not source.is_file() or not template.is_file():raise FileNotFoundError('Source and client template required')
    if root.exists() and any(root.iterdir()):raise ValueError('Choose a new empty job directory; do not reinitialize work')
    original=video_info(source,args.ffprobe)
    if original['codec']!='h264':raise ValueError('Assigned MP4 reference requires H.264; resolve source format explicitly')
    if args.profile=='client-4k' and sorted([original['width'],original['height']])!=[2160,3840]:
        raise ValueError('Explicit 4K profile conflicts with this source; resolve the source-native/4K requirement first')
    count=args.frames or original['frame_count']
    if count<1 or count>original['frame_count']:raise ValueError('Frame count outside actual reference')
    revision=current_revision()
    if args.skill_revision and args.skill_revision!=revision:raise ValueError('Requested revision is not this exact clean installed release')
    inputs=root/'inputs';inputs.mkdir(parents=True,exist_ok=True)
    clip=inputs/'source.mp4';model=inputs/'template.blend'
    if count==original['frame_count']:
        shutil.copy2(source,clip);pixel_identity=True
    else:
        duration=Fraction(count*original['fps_den'],original['fps_num'])
        run([args.ffmpeg,'-hide_banner','-v','error','-y','-i',source,'-frames:v',count,
             '-t',format(float(duration),'.12f'),'-map','0:v:0','-map','0:a?','-c','copy','-movflags','+faststart',clip])
        # Prefix stream copying is permitted only when actual decoded images match.
        a=pixel_hashes(source,count,args.ffmpeg);b=pixel_hashes(clip,count,args.ffmpeg)
        pixel_identity=len(a)==count and a==b
        if not pixel_identity:raise ValueError('Source excerpt is not frame-identical; fix the cut rather than hide a mismatch')
    shutil.copy2(template,model)
    info=video_info(clip,args.ffprobe);full_decode(clip,args.ffmpeg)
    if info['frame_count']!=count:raise ValueError('Actual excerpt frame count differs')
    source_data={k:info[k] for k in ['width','height','fps_num','fps_den','frame_count']}
    source_data.update(path='inputs/source.mp4',sha256=sha256(clip),audio_present=bool(info['audio']))
    template_data={'path':'inputs/template.blend','sha256':sha256(model),'level':'L3'}
    render={'width':info['width'],'height':info['height'],'percentage':100,'samples':64,'dark_scene':args.dark_scene}
    if args.profile=='client-4k-project-1080p':
        render.update(width=2160 if info['height']>info['width'] else 3840,
                      height=3840 if info['height']>info['width'] else 2160,raytracing=False,
                      output_width=1080 if info['height']>info['width'] else 1920,
                      output_height=1920 if info['height']>info['width'] else 1080)
    validate_render_contract(render,source_data,args.profile)
    timeline={'frame_start':1,'frame_end':count}
    spec={'render':render,'fps':f"{info['fps_num']}/{info['fps_den']}",'profile':args.profile,
          'palette_decision':args.palette_decision,'uniform_character_level':'L3'}
    queue_task={'source_sha256':source_data['sha256'],'template_sha256':template_data['sha256'],
                'standards_sha256':sha256(RULES_PATH),'skill_revision':revision,'spec':spec,'timeline':timeline}
    from team_queue import task_id
    identity=task_id(queue_task);queue_task['job_id']=identity
    job={'schema':'client-white-model-job.v1','job_id':identity,'standards_version':VERSION,
         'standards_sha256':sha256(RULES_PATH),'skill_revision':revision,'producer':args.worker_id,
         'project_name':args.name,'profile':args.profile,'source':source_data,'template':template_data,
         'render':render,'timeline':timeline,'queue_task':queue_task,
         'palette_decision':args.palette_decision,'source_provenance':{'original_sha256':sha256(source),
         'assigned_source_start_frame':0,'assigned_frame_count':count,'decoded_prefix_identical':pixel_identity},
         'scene':{'shots':[],'actors':[],'production_objects':[],'support_objects':[],
                  'excluded_source_objects':[],'approved_visibility_exceptions':[]},
         'engineering_thresholds':{'contact_gap_m':.015,'penetration_m':.005,'stance_drift_m':.03,
             'max_joint_step_m':.12,'max_joint_angle_deg':18,'rigid_edge_drift_m':.0001},
         'state':'INPUTS_VERIFIED_SCENE_MAPPING_REQUIRED'}
    if not re.fullmatch(r'[\w.-]+',args.name,flags=re.UNICODE) or args.name in ['.','..']:
        raise ValueError('Project base name must be a simple file name')
    write_json(root/'job.json',job)
    write_json(root/'inputs_report.json',{'schema':'client-white-model-inputs.v1','job_id':identity,
        'standards_sha256':job['standards_sha256'],'passed':True,'source_sha256':source_data['sha256'],
        'template_sha256':template_data['sha256'],'full_decode_pass':True,
        'source_frame_count':count,'decoded_prefix_identical':pixel_identity})
    write_json(root/'queue_task.json',queue_task)
    return {'job':str(root/'job.json'),'job_id':identity,'frames':count,'state':job['state']}

def review_worksheet(job_path,output,reviewer):
    job,root=load_job(job_path)
    rows=[{'id':r['id'],'status':'unverified','shot_ids':[s['id'] for s in job.get('scene',{}).get('shots',[])],
           'evidence':[],'observations':''} for r in blocking_rules()]
    report={'schema':'client-white-model-review.v1','job_id':job['job_id'],
        'standards_sha256':job['standards_sha256'],'skill_revision':job['skill_revision'],
        'reviewer':{'id':reviewer,'kind':'independent_reviewer'},'producer':job['producer'],
        'source_sha256':job['source']['sha256'],'blend_sha256':None,
        'white_sha256':None,'comparison_sha256':None,'all_frames_reviewed':False,
        'reviewed_frame_count':0,'playback_reviewed':False,'last_second_reviewed':False,
        'rule_results':rows,'client_acceptance':False,'scope':'INTERNAL_REVIEW_NOT_CLIENT_ACCEPTANCE'}
    write_json(output,report)
    return {'review':str(output),'unverified_blocking_rules':len(rows)}

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('init');a.add_argument('--source',required=True);a.add_argument('--template',required=True)
    a.add_argument('--output',required=True);a.add_argument('--name',required=True);a.add_argument('--frames',type=int)
    a.add_argument('--profile',choices=['client-4k-project-1080p','client-4k','source-native'],default='client-4k-project-1080p')
    a.add_argument('--dark-scene',action='store_true');a.add_argument('--palette-decision',required=True)
    a.add_argument('--worker-id',required=True);a.add_argument('--skill-revision')
    a.add_argument('--ffmpeg',default='ffmpeg');a.add_argument('--ffprobe',default='ffprobe')
    a=sub.add_parser('review-template');a.add_argument('--job',required=True);a.add_argument('--output',required=True)
    a.add_argument('--reviewer',required=True)
    a=sub.add_parser('probe');a.add_argument('source');a.add_argument('--ffprobe',default='ffprobe')
    a=sub.add_parser('validate');a.add_argument('--job',required=True)
    args=p.parse_args()
    if args.command=='init':out=create_job(args)
    elif args.command=='review-template':out=review_worksheet(args.job,args.output,args.reviewer)
    elif args.command=='probe':out=video_info(args.source,args.ffprobe)
    else:
        job,root=load_job(args.job);info=video_info(relative_file(root,job['source']['path']))
        for key in ['width','height','fps_num','fps_den','frame_count']:
            if info[key]!=job['source'][key]:raise ValueError('Actual source media differs from declared '+key)
        out={'job_id':job['job_id'],'input_bytes_and_spec_valid':True,'source_media_reprobed':True,
             'template_usability_certified':False,'scene_is_not_certified':True}
    print(json.dumps(out,ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,FileNotFoundError,RuntimeError) as error:
        print(json.dumps({'passed':False,'error':str(error)},ensure_ascii=False),file=sys.stderr);sys.exit(1)

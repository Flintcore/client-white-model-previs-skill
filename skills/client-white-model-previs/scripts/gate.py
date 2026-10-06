"""Fail-closed rule/evidence gate; client acceptance is not implied."""
import argparse, json, sys, zipfile
from pathlib import Path
from common import *

def within(root,path):
    path=Path(path)
    if not path.is_absolute():path=root/path
    path=path.resolve();path.relative_to(root.resolve())
    if not path.is_file():raise FileNotFoundError(path)
    return path

def verify_bundle(job_path,blender_report,media_report,review_path):
    job,root=load_job(job_path);issues=[];artifacts=[]
    inputs=read_json(root/'inputs_report.json');b=read_json(blender_report);m=read_json(media_report);v=read_json(review_path)
    for kind,path,data in [('inputs',root/'inputs_report.json',inputs),('blender',blender_report,b),('media',media_report,m)]:
        if data.get('passed') is not True:issues.append(kind+': actual report failed or unverified')
        if data.get('job_id')!=job['job_id'] or data.get('standards_sha256')!=job['standards_sha256']:issues.append(kind+': wrong job/rules')
        artifacts.append(artifact(within(root,path),kind))
    expected_schemas={'inputs':'client-white-model-inputs.v1','blender':'client-white-model-blender-qa.v1','media':'client-white-model-media.v1'}
    for kind,data in [('inputs',inputs),('blender',b),('media',m)]:
        if data.get('schema')!=expected_schemas[kind]:issues.append(kind+': unsupported report schema')
    if v.get('schema')!='client-white-model-review.v1':issues.append('visual: unsupported review schema')
    current={}
    base=job['project_name'];bundle=root/'delivery'/base
    expected_files={'original':bundle/(base+'.mp4'),'white':bundle/(base+' 白模.mp4'),
                    'comparison':bundle/(base+' 对比.mp4'),'blend':bundle/(base+'.blend')}
    for item in m.get('artifacts',[]):
        path=within(root,item['path'])
        if item['kind'] in current:issues.append('Duplicate delivered artifact: '+item['kind'])
        if item['kind'] not in expected_files or path!=expected_files[item['kind']].resolve():
            issues.append('Wrong final bundle filename/location: '+item['kind'])
        if sha256(path)!=item['sha256']:issues.append('Changed artifact: '+item['kind'])
        current[item['kind']]=item['sha256'];artifacts.append(artifact(path,item['kind']))
    if set(current)!=set(['original','white','comparison','blend']):issues.append('Exactly four actual client artifacts required')
    if inputs.get('source_sha256')!=job['source']['sha256'] or inputs.get('template_sha256')!=job['template']['sha256']:
        issues.append('Input report not bound to current source/template')
    if b.get('blend_sha256')!=current.get('blend') or m.get('blend_sha256')!=current.get('blend'):
        issues.append('Scene hash differs from actual independently tested scene')
    portable=b.get('final_bundle_portability',{})
    if portable.get('applicable') is not True or portable.get('passed') is not True:
        issues.append('Final four-file bundle dependencies were not verified portable')
    if current.get('original')!=job['source']['sha256'] or m.get('source_sha256')!=job['source']['sha256']:
        issues.append('Delivered original differs from assigned source')
    if not b.get('render_evidence',{}).get('passed'):issues.append('No independent full native-frame evidence')
    native=m.get('native_render',{})
    native_path=within(root,native['path'])
    if sha256(native_path)!=native['sha256']:issues.append('Changed native render manifest')
    native_report=read_json(native_path)
    if native_report.get('blend_sha256')!=current.get('blend') or native_report.get('job_id')!=job['job_id'] or native_report.get('status')!='COMPLETE':
        issues.append('Native rendering is not the current completed scene')
    artifacts.append(artifact(native_path,'renders'))
    # Recheck every native image, including hashes, not just a cached manifest flag.
    from media import verify_native
    verify_native(job,within(root,next(i['path'] for i in m['artifacts'] if i['kind']=='blend')),native_path.parent)
    shots=job.get('scene',{}).get('shots',[]);shot_ids={x['id'] for x in shots}
    cursor=1
    for shot in sorted(shots,key=lambda x:x['start']):
        if shot['start']!=cursor or shot['end']<shot['start']:issues.append('Shot coverage gap/overlap')
        cursor=shot['end']+1
    if not shots or cursor!=job['source']['frame_count']+1 or len(shot_ids)!=len(shots):issues.append('Source shot coverage is incomplete')
    expected_v={'job_id':job['job_id'],'standards_sha256':job['standards_sha256'],'skill_revision':job['skill_revision'],
        'source_sha256':job['source']['sha256'],'blend_sha256':current.get('blend'),
        'white_sha256':current.get('white'),'comparison_sha256':current.get('comparison')}
    for key,value in expected_v.items():
        if v.get(key)!=value:issues.append('Visual review not bound to current '+key)
    reviewer=v.get('reviewer',{}).get('id')
    if not reviewer or reviewer==job['producer'] or v.get('producer')!=job['producer']:issues.append('Producer may not self-approve')
    if (v.get('all_frames_reviewed') is not True or v.get('playback_reviewed') is not True or
        v.get('last_second_reviewed') is not True or v.get('reviewed_frame_count')!=job['source']['frame_count']):
        issues.append('Full actual playback/frame coverage/last-second review is missing')
    required={r['id']:r for r in blocking_rules()};rows=v.get('rule_results',[]);seen={}
    bound_evidence={'inputs':{within(root,root/'inputs_report.json')},'blender':{within(root,blender_report)},
                    'media':{within(root,media_report)},'renders':{native_path},
                    'integrity':{within(root,job_path)},'visual':{within(root,review_path)}}
    # Visual evidence is actual delivered source/comparison/white or the current
    # signed review, not an unrelated text file relabelled as an observation.
    bound_evidence['visual'].update(within(root,i['path']) for i in m.get('artifacts',[]) if i['kind'] in ['original','comparison','white'])
    for row in rows:
        rid=row.get('id')
        if rid in seen:issues.append('Duplicate rule result '+str(rid))
        seen[rid]=row
        if rid not in required:issues.append('Unknown/inactive rule in hard gate '+str(rid));continue
        rule=required[rid];status=row.get('status')
        if status=='not_applicable':
            if not rule.get('conditional') or not row.get('rationale') or not row.get('source_frames'):
                issues.append(rid+': unsupported applicability exemption')
        elif status!='pass':issues.append(rid+': failed/unverified')
        if not row.get('observations','').strip():issues.append(rid+': actual observation/evidence absent')
        if set(row.get('shot_ids',[]))!=shot_ids:issues.append(rid+': not reviewed for every assigned shot')
        provided=set()
        for e in row.get('evidence',[]):
            if not isinstance(e,dict):issues.append(rid+': evidence needs path/kind/SHA');continue
            path=within(root,e['path'])
            if sha256(path)!=e['sha256']:issues.append(rid+': changed evidence')
            if path not in bound_evidence.get(e['kind'],set()):issues.append(rid+': evidence kind is not bound to the actual current artifact')
            provided.add(e['kind'])
        if not set(rule.get('evidence_kinds',[]))<=provided:issues.append(rid+': incomplete rule evidence kinds')
    for rid in required.keys()-seen.keys():issues.append('Missing rule '+rid)
    artifacts.append(artifact(within(root,review_path),'visual'))
    artifacts.append(artifact(within(root,job_path),'integrity'))
    return {'schema':'client-white-model-gate.v1','passed':not issues,'job_id':job['job_id'],
        'standards_sha256':job['standards_sha256'],'skill_revision':job['skill_revision'],
        'blocking_rule_count':len(required),'reviewed_rule_count':len(set(required)&set(seen)),
        'producer':job['producer'],'reviewer':reviewer,'issues':issues,'artifacts':artifacts,
        'client_acceptance':False,'scope':'INTERNAL_PRODUCTION_GATE_NOT_CLIENT_ACCEPTANCE'}

def package(job_path,blender_report,media_report,review_path,gate_path):
    # Never trust an old "passed" bit: re-run current evidence checks before ZIP.
    report=verify_bundle(job_path,blender_report,media_report,review_path);write_json(gate_path,report)
    if not report['passed']:raise ValueError('Delivery gate failed; preserve diagnostic/rework state')
    job,root=load_job(job_path);base=job['project_name'];folder=root/'delivery'/base;target=root/'delivery'/(base+'.zip')
    if target.exists():raise ValueError('Existing package preserved; release a new version explicitly')
    names=[base+'.mp4',base+' 白模.mp4',base+' 对比.mp4',base+'.blend']
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_STORED) as z:
        for name in names:z.write(folder/name,base+'/'+name)
    with zipfile.ZipFile(target) as z:
        if z.testzip() is not None or z.namelist()!=[base+'/'+n for n in names]:raise ValueError('ZIP structure/CRC failed')
        for name in names:
            h=hashlib.sha256()
            with z.open(base+'/'+name) as f:
                for chunk in iter(lambda:f.read(2**20),b''):h.update(chunk)
            if h.hexdigest()!=sha256(folder/name):raise ValueError('ZIP member SHA differs')
    receipt={'schema':'client-white-model-package.v1','job_id':job['job_id'],'gate_sha256':sha256(gate_path),
        'package':artifact(target,'package'),'member_count':4,'member_hashes_and_crc_pass':True,
        'client_acceptance':False,'scope':'INTERNALLY_VERIFIED_RELEASE'}
    write_json(root/'package_receipt.json',receipt);return receipt

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['check','package']);p.add_argument('--job',required=True)
    p.add_argument('--blender-report',required=True);p.add_argument('--media-report',required=True)
    p.add_argument('--review',required=True);p.add_argument('--output',required=True);args=p.parse_args()
    if args.command=='package':result=package(args.job,args.blender_report,args.media_report,args.review,args.output)
    else:
        result=verify_bundle(args.job,args.blender_report,args.media_report,args.review);write_json(args.output,result)
    print(json.dumps(result,ensure_ascii=False))
    if result.get('passed') is False:sys.exit(1)

if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,FileNotFoundError,RuntimeError) as error:
        print(json.dumps({'passed':False,'error':str(error)},ensure_ascii=False),file=sys.stderr);sys.exit(1)

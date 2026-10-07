"""Portable shared contracts. No machine paths, project footage, or secrets."""
import hashlib, json, os, re, subprocess
from fractions import Fraction
from pathlib import Path

SKILL_ROOT=Path(__file__).resolve().parents[1]
RULES_PATH=SKILL_ROOT/'references'/'standards.json'
VERSION='1.1.0'


def validate_render_contract(render, source, profile):
    """Keep input pixels separate from the explicitly revised delivery pixels."""
    if render.get('percentage') != 100 or render.get('samples') != 64:
        raise ValueError('100 percent and exactly 64 samples are mandatory')
    if not isinstance(render.get('dark_scene'), bool):
        raise ValueError('Explicit dark-scene classification required')
    if profile == 'client-4k-project-1080p':
        dimensions = (2160, 3840) if source['height'] > source['width'] else (3840, 2160)
        output = (1080, 1920) if source['height'] > source['width'] else (1920, 1080)
        if (render.get('width'), render.get('height')) != dimensions:
            raise ValueError('Current client profile requires saved 4K project dimensions')
        if (render.get('output_width'), render.get('output_height')) != output:
            raise ValueError('Current client profile requires native 1080p video dimensions')
        if render.get('raytracing') is not False:
            raise ValueError('Current client profile requires ray tracing disabled')
        if source['width'] * dimensions[1] != source['height'] * dimensions[0]:
            raise ValueError('Source aspect ratio differs; resolve framing before conversion')
    elif profile in {'client-4k', 'source-native'}:
        if (render.get('width'), render.get('height')) != (source['width'], source['height']):
            raise ValueError('Legacy native reference dimensions are mandatory')
        if profile == 'client-4k' and sorted([source['width'], source['height']]) != [2160, 3840]:
            raise ValueError('4K/native source conflict')
    else:
        raise ValueError('Unknown source profile')


def raytracing_matches(render, saved, profile):
    """False is an explicit current setting, not a missing/unsupported property."""
    if profile == 'client-4k-project-1080p':
        return render.get('raytracing') is False and saved is False
    return isinstance(render.get('dark_scene'), bool) and (not render['dark_scene'] or saved is True)


def delivery_dimensions(render):
    return render.get('output_width', render['width']), render.get('output_height', render['height'])

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.writing')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(temporary,path)

def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(2**20),b''):h.update(chunk)
    return h.hexdigest()

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def run(command):
    result=subprocess.run([str(x) for x in command],capture_output=True,text=True,encoding='utf-8',errors='replace')
    if result.returncode:
        raise RuntimeError(f'Command exit {result.returncode}: {command}\n{result.stderr[-6000:]}')
    return result.stdout

def probe(path,ffprobe='ffprobe'):
    return json.loads(run([ffprobe,'-v','error','-count_frames','-show_streams','-show_format','-of','json',path]))

def video_info(path,ffprobe='ffprobe'):
    p=probe(path,ffprobe)
    v=next(x for x in p['streams'] if x['codec_type']=='video')
    fps=Fraction(v['avg_frame_rate'])
    if fps<=0:raise ValueError('Reference must have a defined constant-frame-rate contract')
    n=int(v.get('nb_read_frames') or v.get('nb_frames') or 0)
    if not n:raise ValueError('Actual decoded video frame count is required')
    # A rational average alone is not proof of CFR. Inspect actual presentation
    # timestamps; allow only the source time-base rounding error, never resample.
    ticks=Fraction(v['time_base']);expected=Fraction(1,1)/fps
    raw=run([ffprobe,'-v','error','-select_streams','v:0','-show_entries',
             'frame=best_effort_timestamp','-of','csv=p=0',path])
    timestamps=[]
    for line in raw.splitlines():
        first=line.split(',',1)[0].strip()
        if re.fullmatch(r'-?\d+',first):timestamps.append(int(first))
    if len(timestamps)!=n:raise ValueError('Complete actual source frame timestamps required')
    for a,b in zip(timestamps,timestamps[1:]):
        if b<=a or abs((b-a)*ticks-expected)>ticks:
            raise ValueError('Variable/discontinuous source frame timing: resolve explicitly, do not silently resample')
    return {'width':v['width'],'height':v['height'],'fps_num':fps.numerator,
            'fps_den':fps.denominator,'frame_count':n,'codec':v['codec_name'],
            'pixel_format':v.get('pix_fmt'),'audio':[a for a in p['streams'] if a['codec_type']=='audio']}

def full_decode(path,ffmpeg='ffmpeg'):
    run([ffmpeg,'-hide_banner','-v','error','-xerror','-i',path,'-map','0:v:0','-map','0:a?','-f','null','-'])

def rules():
    data=read_json(RULES_PATH)
    if data.get('version')!=VERSION:raise ValueError('Unsupported standards version')
    return data

def blocking_rules():
    return [r for r in rules()['rules'] if r.get('status')=='active' and r.get('blocking',True)]

def relative_file(root,value):
    value=Path(value)
    if value.is_absolute() or '..' in value.parts:raise ValueError('Job assets must use bounded relative paths')
    root=Path(root).resolve();path=(root/value).resolve()
    path.relative_to(root)
    if not path.is_file():raise FileNotFoundError(path)
    return path

def current_revision():
    """Require a real clean checkout or a byte-verified installation receipt."""
    receipt=SKILL_ROOT/'installation.json'
    if receipt.is_file():
        data=read_json(receipt)
        actual={p.relative_to(SKILL_ROOT).as_posix():sha256(p) for p in SKILL_ROOT.rglob('*')
                if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc' and p.name!='installation.json'}
        if data.get('schema')!='client-white-model-skill-install.v1' or actual!=data.get('files'):
            raise ValueError('Installed skill bytes differ from the pinned receipt')
        if data.get('standards_sha256')!=sha256(RULES_PATH):raise ValueError('Installed standards changed')
        revision=data.get('revision','')
    else:
        revision=run(['git','-C',SKILL_ROOT,'rev-parse','HEAD']).strip()
        dirty=run(['git','-C',SKILL_ROOT,'status','--porcelain','--untracked-files=all','--',str(SKILL_ROOT)]).strip()
        if dirty:raise ValueError('Skill checkout has uncommitted changes; pin a clean release before production')
    if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('Real pinned release revision required')
    return revision

def load_job(path,check_inputs=True):
    path=Path(path).resolve();job=read_json(path)
    if job.get('schema')!='client-white-model-job.v1':raise ValueError('Wrong job schema')
    if job.get('standards_version')!=VERSION or job.get('standards_sha256')!=sha256(RULES_PATH):
        raise ValueError('Rule set differs from this installed release; use the pinned version')
    if not re.fullmatch('[0-9a-f]{40}',job.get('skill_revision','')):
        raise ValueError('Pin a real 40-character Git revision, not latest/main')
    if job['skill_revision']!=current_revision():raise ValueError('Job uses another skill revision; use its exact release')
    if not re.fullmatch('[0-9a-f]{64}',job.get('job_id','')):raise ValueError('Missing deterministic job identity')
    if job['template'].get('level')!='L3':raise ValueError('Current client requires uniform L3')
    r=job['render'];s=job['source'];t=job['timeline']
    validate_render_contract(r,s,job['profile'])
    if t['frame_start']!=1 or t['frame_end']!=s['frame_count']:raise ValueError('Assigned reference clip and timeline differ')
    from team_queue import task_id
    q=job['queue_task']
    if task_id(q)!=job['job_id'] or q.get('job_id')!=job['job_id']:raise ValueError('Deterministic task identity changed')
    expected={'source_sha256':s['sha256'],'template_sha256':job['template']['sha256'],
              'standards_sha256':job['standards_sha256'],'skill_revision':job['skill_revision'],
              'spec':{'render':r,'fps':f"{s['fps_num']}/{s['fps_den']}",'profile':job['profile'],
                      'palette_decision':job['palette_decision'],'uniform_character_level':job['template']['level']},
              'timeline':t}
    if any(q.get(k)!=value for k,value in expected.items()):raise ValueError('Job input/spec differs from its immutable queue task')
    name=job['project_name']
    if not re.fullmatch(r'[\w.-]+',name,flags=re.UNICODE) or name in ['.','..']:raise ValueError('Simple delivery base name required')
    if check_inputs:
        for key in ['source','template']:
            asset=relative_file(path.parent,job[key]['path'])
            if sha256(asset)!=job[key]['sha256']:raise ValueError('Asset identity mismatch: '+key)
    return job,path.parent

def artifact(path,kind):
    path=Path(path).resolve()
    return {'kind':kind,'path':str(path),'sha256':sha256(path)}

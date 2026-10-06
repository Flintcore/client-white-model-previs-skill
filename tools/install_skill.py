"""Verified local install/update; backs up prior contents, never git-pulls an install."""
import argparse, hashlib, json, os, shutil, subprocess, time, uuid
from pathlib import Path

NAME='client-white-model-previs'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def files(folder):
    return {p.relative_to(folder).as_posix():sha(p) for p in sorted(folder.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.name!='installation.json' and p.suffix!='.pyc'}
def bounded(path,parent):
    path=path.resolve();path.relative_to(parent.resolve());return path
def install(repository,target,update=False,revision=None):
    repository=Path(repository).resolve();source=repository/'skills'/NAME
    if not (source/'SKILL.md').is_file():raise ValueError('Repository skill entrypoint missing')
    target=Path(target).expanduser().absolute()
    if target.name!=NAME or target.is_symlink():raise ValueError('Target must be an actual skill folder named '+NAME)
    parent=target.parent.resolve();bounded(target,parent)
    if target.exists() and not update:raise ValueError('Installed skill exists; inspect it, then use --update to preserve a backup')
    r=subprocess.run(['git','-C',str(repository),'rev-parse','HEAD'],capture_output=True,text=True)
    if r.returncode:raise ValueError('Clone the actual pinned Git release before installation')
    actual_revision=r.stdout.strip()
    if revision and revision!=actual_revision:raise ValueError('Requested revision differs from actual checkout HEAD')
    revision=actual_revision
    dirty=subprocess.run(['git','-C',str(repository),'status','--porcelain','--untracked-files=all','--',str(source)],
                         capture_output=True,text=True)
    if dirty.returncode or dirty.stdout.strip():raise ValueError('Commit or restore skill edits before installing a pinned release')
    import re
    if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('Use a full pinned Git commit SHA')
    parent.mkdir(parents=True,exist_ok=True)
    stage=bounded(parent/('.'+NAME+'.installing-'+uuid.uuid4().hex),parent)
    shutil.copytree(source,stage,ignore=shutil.ignore_patterns('__pycache__','*.pyc','installation.json'))
    expected=files(source)
    if files(stage)!=expected:raise ValueError('Staged skill byte hashes differ')
    receipt={'schema':'client-white-model-skill-install.v1','revision':revision,
             'standards_sha256':sha(stage/'references'/'standards.json'),'files':expected}
    (stage/'installation.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    backup=None
    if target.exists():
        backup_root=parent.parent/'skill-backups';backup_root.mkdir(parents=True,exist_ok=True)
        backup=bounded(backup_root/(NAME+'-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]),backup_root)
        bounded(target,parent).rename(backup)
    try:stage.rename(target)
    except Exception:
        if backup and not target.exists():backup.rename(target)
        raise
    if files(target)!=expected:raise ValueError('Installed byte hashes differ; backup preserved')
    return {'installed':str(target.resolve()),'revision':revision,'file_count':len(expected),
            'all_file_hashes_match':True,'backup':str(backup) if backup else None}
def verify(target):
    target=Path(target).resolve();receipt=json.loads((target/'installation.json').read_text(encoding='utf-8'))
    import re
    if receipt.get('schema')!='client-white-model-skill-install.v1' or not re.fullmatch('[0-9a-f]{40}',receipt.get('revision','')):
        raise ValueError('Unsupported installation receipt or revision')
    if files(target)!=receipt['files']:raise ValueError('Installed skill changed or has extra/missing files')
    if sha(target/'references'/'standards.json')!=receipt['standards_sha256']:raise ValueError('Rules changed')
    return {'verified':True,'target':str(target),'revision':receipt['revision'],'file_count':len(receipt['files'])}
def main():
    p=argparse.ArgumentParser();p.add_argument('--repository',default=str(Path(__file__).resolve().parents[1]))
    p.add_argument('--target',default=str(Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'skills'/NAME))
    p.add_argument('--update',action='store_true');p.add_argument('--revision');p.add_argument('--verify',action='store_true')
    a=p.parse_args();out=verify(a.target) if a.verify else install(a.repository,a.target,a.update,a.revision)
    print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__':main()

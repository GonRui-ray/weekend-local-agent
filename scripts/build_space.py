"""Assemble a flat, secret-free ModelScope Gradio bundle, like edu-agent."""
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'build/modelscope'
IGNORE=shutil.ignore_patterns('__pycache__','*.pyc','.env','*.env','.DS_Store')

def build():
    dist=ROOT/'frontend/dist'
    if not (dist/'index.html').is_file(): raise SystemExit('先执行 cd frontend && npm ci && npm run build')
    if OUT.is_dir(): shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for name in ('app.py','requirements.txt','README.md','Dockerfile'):
        shutil.copy2(ROOT/'space'/name,OUT/name)
    for name in ('plan.schema.json','response.schema.json','coordinator.schema.json','turn.schema.json','event-feed.schema.json'):
        shutil.copy2(ROOT/'agent'/name,OUT/name)
    for name in ('weekend_agent','prompts'):
        shutil.copytree(ROOT/'agent'/name,OUT/name,ignore=IGNORE)
    shutil.copytree(dist,OUT/'ui')
    shutil.copy2(ROOT/'NOTICE.md',OUT/'NOTICE.md')
    shutil.copy2(ROOT/'event-feed.template.json',OUT/'event-feed.template.json')
    forbidden=[p for p in OUT.rglob('*') if p.name=='.env' or p.name.endswith('.pyc') or p.name=='node_modules']
    if forbidden: raise SystemExit('打包异常：出现了不应包含的本地文件。')
    print(f'ModelScope 包已生成：{OUT}')
    return OUT

if __name__=='__main__':build()

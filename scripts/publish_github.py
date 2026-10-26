"""Publish a prepared repo; require an authenticated gh session, never accept tokens."""
from pathlib import Path
import argparse
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def run(*args, capture=False):
    result = subprocess.run(args,cwd=ROOT,text=True,capture_output=capture)
    if result.returncode:
        if capture and result.stderr: print(result.stderr.strip())
        raise SystemExit(f'命令未完成：{args[0]} {args[1]}；未继续后续步骤。')
    return result.stdout.strip() if capture else ''

def main():
    parser=argparse.ArgumentParser(description='将已提交版本发布到 GitHub；先运行 gh auth login --web --scopes workflow。')
    parser.add_argument('--name',default='weekend-local-agent')
    parser.add_argument('--private',action='store_true',help='创建私有源码仓库；不自动开启 Pages。')
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',args.name):parser.error('仓库名只允许字母、数字、点、下划线和连字符')
    run('gh','auth','status','--hostname','github.com')
    owner=run('gh','api','user','--jq','.login',capture=True)
    if not re.fullmatch(r'[A-Za-z0-9-]+',owner):raise SystemExit('无法确认当前 GitHub 用户。')
    if run('git','status','--porcelain',capture=True):raise SystemExit('请先提交当前改动，再发布。')
    run('git','rev-parse','--verify','HEAD',capture=True)
    if run('git','branch','--show-current',capture=True)!='main':raise SystemExit('请在 main 分支发布。')
    remote=subprocess.run(['git','remote','get-url','origin'],cwd=ROOT,text=True,capture_output=True)
    expected=f'https://github.com/{owner}/{args.name}.git'
    if remote.returncode==0:
        if remote.stdout.strip() not in (expected,expected.removesuffix('.git'),f'git@github.com:{owner}/{args.name}.git'):
            raise SystemExit('origin 已指向其他仓库，未覆盖。请先核对发布目标。')
    else:
        run('gh','repo','create',f'{owner}/{args.name}','--private' if args.private else '--public','--source',str(ROOT),'--remote','origin','--description','周末盲盒策划师：面向本地居住者的活动整合、路线规划与邀请函 Agent')
    run('git','-c','credential.https://github.com.helper=','-c','credential.https://github.com.helper=!gh auth git-credential','push','-u','origin','HEAD')
    url=run('gh','repo','view',f'{owner}/{args.name}','--json','url','--jq','.url',capture=True)
    print(f'仓库：{url}')
    if args.private:
        print('私有源码已推送；未自动启用 Pages。')
        return
    existing=subprocess.run(['gh','api',f'repos/{owner}/{args.name}/pages'],cwd=ROOT,text=True,capture_output=True)
    if existing.returncode:
        created=run('gh','api','--method','POST',f'repos/{owner}/{args.name}/pages','-f','build_type=workflow',capture=True)
        page=json.loads(created)
    else:
        page=json.loads(existing.stdout)
        if page.get('build_type')!='workflow':raise SystemExit('已有 Pages 使用其他发布模式，未覆盖设置。源码已推送。')
    print(f"Pages 配置地址：{page.get('html_url','请到仓库 Settings → Pages 查看')}")
    print('正在触发 Pages 构建；地址可访问性以 Actions 部署成功为准。')
    run('gh','workflow','run','pages.yml','--repo',f'{owner}/{args.name}','--ref','main')
    print(f'检查运行结果：{url}/actions')

if __name__=='__main__':main()

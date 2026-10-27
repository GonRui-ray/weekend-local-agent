"""Verify a live cloud backend, then connect the existing Pages frontend.

Requires an authenticated gh session. Never accepts supplier keys or login tokens.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from verify_live_backend import read_request_file, verify_backend, public_url, VerificationError

REPO = "GonRui-ray/weekend-local-agent"
ROOT = Path(__file__).resolve().parents[1]


def gh(*args):
    result = subprocess.run(["gh", *args], cwd=ROOT, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("GitHub 操作未完成：gh " + " ".join(args[:3])
                           + "；请检查登录或仓库权限，未打印凭证。")
    return result.stdout.strip()


def connect(base_url, requests, *, require_dated=False, check_only=False, output):
    if not public_url(base_url):
        raise VerificationError("需要公开 HTTPS 根地址，例如你的云服务地址；不要填写 Key。")
    gh("auth", "status", "--hostname", "github.com")
    report = asyncio.run(verify_backend(base_url, requests, require_dated=require_dated))
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not report["passed"]:
        print("验收未通过，现有 Pages 配置未改变。" + report.get("error", ""))
        print("详细结果：" + str(path))
        return 1
    print("真实后端检索、约束、来源和邀请函链路通过。")
    print("结构化日期记录覆盖：" + str(all(c["all_plans_have_dated_records"] for c in report["cities"])))
    if check_only:
        print("仅验收，未修改 GitHub。")
        return 0
    gh("variable", "set", "AGENT_API_URL", "--repo", REPO, "--body", base_url.rstrip("/"))
    # This value is a public service address, never a supplier credential.
    gh("workflow", "run", "pages.yml", "--repo", REPO, "--ref", "main")
    receipt = {"requested_at": datetime.now(timezone.utc).isoformat(), "repo": REPO,
               "backend_url": base_url.rstrip("/"), "frontend_deployment": "requested_not_yet_verified",
               "workflow": "https://github.com/" + REPO + "/actions/workflows/pages.yml"}
    path.with_name("pages-connection.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("已设置公开后端地址并触发 Pages 发布；发布成功后仍需手机浏览器验证。")
    print(receipt["workflow"])
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--requests-file")
    parser.add_argument("--require-dated", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--output", default="build/evidence/live-backend.json")
    args = parser.parse_args()
    try:
        return connect(args.base_url, read_request_file(args.requests_file),
                       require_dated=args.require_dated, check_only=args.check_only, output=args.output)
    except (VerificationError, ValueError, RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

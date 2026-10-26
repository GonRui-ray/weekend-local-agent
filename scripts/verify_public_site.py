"""Check the public showcase without provider credentials or a local server."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

import httpx
from jsonschema import validate, ValidationError

ROOT = Path(__file__).resolve().parents[1]


def verify(base_url):
    if urlparse(base_url).scheme != "https":
        raise ValueError("The public site URL must use HTTPS.")
    base_url = base_url.rstrip("/") + "/"
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "site_url": base_url,
        "scope": "Public showcase HTTP, built assets and labelled sample; no live provider calls.",
        "checks": [],
        "resources": [],
    }
    checks = report["checks"]

    def check(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    with httpx.Client(timeout=30, follow_redirects=True) as client:
        page = client.get(base_url)
        check("homepage", page.status_code == 200 and "text/html" in page.headers.get("content-type", ""), {"status": page.status_code, "url": str(page.url)})
        scripts = re.findall(r'<script[^>]+src="([^"]+)"', page.text)
        styles = re.findall(r'<link[^>]+href="([^"]+\.css)"', page.text)
        check("entry_assets", bool(scripts and styles), {"scripts": scripts, "styles": styles})
        script_text = ""
        for asset in scripts + styles:
            url = urljoin(base_url, asset)
            # Never follow a script URL to a different site's credentials boundary.
            if urlparse(url).netloc != urlparse(base_url).netloc:
                check("asset_origin", False, url)
                continue
            response = client.get(url)
            mime = response.headers.get("content-type", "")
            valid_mime = "javascript" in mime if asset in scripts else "text/css" in mime
            resource = {"url": url, "status": response.status_code, "content_type": mime, "bytes": len(response.content)}
            report["resources"].append(resource)
            check("asset", response.status_code == 200 and valid_mime, resource)
            if asset in scripts:
                script_text += response.text
        check("preview_label", "交互体验版" in script_text and "非实时活动" in script_text, "The deployed app labels the sample experience.")
        check("sample_entry", "先拆一个样例盲盒" in script_text, "Sample entry is available on the homepage.")
        sample = client.get(urljoin(base_url, "sample-response.json"))
        sample_valid = False
        try:
            sample_data = sample.json()
            validate(sample_data, json.loads((ROOT / "agent/response.schema.json").read_text()))
            result = sample_data["next_step"]["result"]
            validate(result, json.loads((ROOT / "agent/plan.schema.json").read_text()))
            sample_valid = result["meta"]["mode"] == "demo" and bool(result["plans"])
            report["sample"] = {"mode": result["meta"]["mode"], "date": result["request"]["date"], "plans": len(result["plans"]), "note": "Sample date, places and prices are illustrative; not current activity recommendations."}
        except (ValueError, KeyError, TypeError, ValidationError) as error:
            report["sample_error"] = type(error).__name__
        check("sample_schema", sample.status_code == 200 and sample_valid, report.get("sample", {"status": sample.status_code}))
        # These paths report the actual public backend availability, separately from showcase checks.
        report["backend_observations"] = []
        for path in ("api/config", "health", "ready"):
            response = client.get(urljoin(base_url, path))
            report["backend_observations"].append({"path": path, "status": response.status_code, "content_type": response.headers.get("content-type", "")})
    report["live_backend_verified"] = False
    report["live_activity_providers_verified"] = False
    report["passed"] = all(item["passed"] for item in checks)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://gonrui-ray.github.io/weekend-local-agent/")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = verify(args.base_url)
    except Exception as error:
        report = {"checked_at": datetime.now(timezone.utc).isoformat(), "site_url": args.base_url, "passed": False, "error": type(error).__name__ + ": " + str(error)}
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded)
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

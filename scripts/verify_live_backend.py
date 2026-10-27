"""Exercise a deployed backend with actual providers; never substitute a sample.

This checks a workflow, not ticket availability or truth of search snippets.
No provider keys are read here: credentials stay on the cloud service.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

import httpx
from jsonschema import validate, ValidationError

ROOT = Path(__file__).resolve().parents[1]
PAGES_ORIGIN = "https://gonrui-ray.github.io"


class VerificationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def public_url(value):
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and bool(parsed.hostname)
                and not parsed.username and not parsed.password and not parsed.query
                and not parsed.fragment and parsed.path in ("", "/")
                and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))
    except (ValueError, TypeError):
        return False


def test_requests():
    today = datetime.now(timezone(timedelta(hours=8))).date()
    saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    return [{
        "city": city, "date": saturday.isoformat(), "budget": 500,
        "maxDistanceKm": 5, "maxCommuteMinutes": 60,
        "preferences": ["看展", "火锅"], "residentArea": "", "avoidPlaces": [],
        "people": [{"name": "验收者" + str(i + 1), "location": origin,
                    "availableFrom": "10:00", "availableTo": "21:00"}
                   for i, origin in enumerate(origins)],
    } for city, origins in (("上海", ("人民广场", "静安寺")), ("成都", ("天府广场", "春熙路")))]


def read_request_file(path):
    requests = json.loads(Path(path).read_text()) if path else test_requests()
    require(isinstance(requests, list) and 1 <= len(requests) <= 3,
            "验收输入必须是 1–3 个完整条件组成的 JSON 数组。")
    sys.path.insert(0, str(ROOT / "agent"))
    from weekend_agent.domain.planning import normalize
    for request in requests:
        normalize(request)
    return requests


async def stream_turn(client, base_url, payload):
    result = None
    progress = []
    count = 0
    async with client.stream("POST", base_url + "/turn", json=payload) as response:
        require(response.status_code == 200, f"/turn 返回 HTTP {response.status_code}。")
        require("text/event-stream" in response.headers.get("content-type", ""),
                "/turn 没有返回 SSE。")
        async for line in response.aiter_lines():
            count += len(line.encode())
            require(count <= 2_000_000, "SSE 超出验收大小限制。")
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:])
            if event.get("type") == "progress":
                progress.append({key: event.get(key) for key in ("role", "state", "title")})
            if event.get("type") == "result":
                require(result is None, "同一轮出现多个最终结果。")
                result = event
    require(result is not None, "未收到最终结果。")
    validate(result["data"], json.loads((ROOT / "agent/response.schema.json").read_text()))
    require(isinstance(result.get("conversation"), str) and bool(result["conversation"]),
            "没有获得可继续邀请的会话。")
    return result, progress


def inspect_plan(reply, request):
    require(reply["intent"] == "plan", "本轮未生成路线：" + reply.get("message", "")[:220])
    result = reply["next_step"]["result"]
    require(result is not None and result["meta"]["mode"] == "live", "返回了样例或空结果。")
    require(result["request"]["city"] == request["city"].removesuffix("市"), "城市被替换。")
    require(result["request"]["date"] == request["date"], "日期被替换。")
    sys.path.insert(0, str(ROOT / "agent"))
    from weekend_agent.domain.planning import minutes
    start = max(minutes(p["availableFrom"]) for p in request["people"])
    end = min(minutes(p["availableTo"]) for p in request["people"])
    urls = set()
    dated_plans = 0
    for plan in result["plans"]:
        require(plan["budgetPerPerson"]["total"] <= request["budget"], "路线超预算。")
        require(plan["totalDistanceKm"] <= request.get("maxDistanceKm", 3), "路线超出移动范围。")
        require(len(plan["arrivals"]) == len(request["people"]), "遗漏同行人。")
        for arrival in plan["arrivals"]:
            require(minutes(plan["startTime"]) - arrival["minutes"] >= start
                    and minutes(plan["endTime"]) + arrival["returnMinutes"] <= end,
                    "往返超出共同空闲窗口。")
            require(max(arrival["minutes"], arrival["returnMinutes"])
                    <= request.get("maxCommuteMinutes", 45), "超过通勤上限。")
        dated = False
        for stop in plan["stops"]:
            require(stop["source"]["verification"] == "poi_verified"
                    and stop["source"]["url"], "有地点缺少真实地图来源。")
            for source in stop["activitySources"]:
                url = urlsplit(source["url"])
                require(url.scheme in ("https", "http") and url.hostname
                        and not url.username and not url.password, "出现无效资讯链接。")
                urls.add(source["url"])
                if source["verification"] == "provider_record":
                    day = date.fromisoformat(request["date"])
                    require(date.fromisoformat(source["eventStart"]) <= day
                            <= date.fromisoformat(source["eventEnd"]), "活动记录日期未覆盖出行日。")
                    dated = True
        dated_plans += int(dated)
    require(bool(urls), "只有地图地点，未取得与路线匹配的活动或资讯来源。")
    return {
        "city": result["request"]["city"], "date": request["date"],
        "mode": "live", "plan_count": len(result["plans"]),
        "plans_with_dated_records": dated_plans,
        "all_plans_have_dated_records": dated_plans == len(result["plans"]),
        "source_urls": sorted(urls),
        "source_channels": result["meta"].get("information", {}).get("channels", []),
        "warnings": result["meta"].get("warnings", []),
    }


async def verify_backend(base_url, requests, *, client=None, require_dated=False):
    require(public_url(base_url), "后端地址必须为 HTTPS 根地址，不含凭证、参数或子路径。")
    base_url = base_url.rstrip("/")
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "base_url": base_url,
              "scope": "Live providers, constraints and invitation; not ticket or business-hours verification.",
              "passed": False, "checks": [], "cities": [],
              "limitations": ["交通、消费为估算；网页搜索不是已核实活动。", "未验证余票、预约及营业时段。"]}
    owned_client = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(90, connect=20), follow_redirects=False)
    try:
        for route in ("/health", "/ready", "/api/config"):
            response = await client.get(base_url + route, headers={"Origin": PAGES_ORIGIN})
            require(response.status_code == 200, f"{route} 返回 HTTP {response.status_code}；配置未就绪时不会调用检索。")
            data = response.json()
            if route == "/health": require(data.get("ok") is True, "进程健康检查失败。")
            if route == "/ready": require(data.get("ready") is True, "后端配置尚未就绪。")
            if route == "/api/config":
                require(data.get("places") and (data.get("activities") or data.get("feed")),
                        "缺少地图或资讯来源配置。")
                require(response.headers.get("access-control-allow-origin") == PAGES_ORIGIN,
                        "CORS 尚未允许现有 GitHub Pages。")
            report["checks"].append(route)
        preflight = await client.options(base_url + "/turn", headers={
            "Origin": PAGES_ORIGIN, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type"})
        require(preflight.status_code == 200 and preflight.headers.get("access-control-allow-origin") == PAGES_ORIGIN,
                "Pages POST 跨域预检失败。")
        ui = await client.get(base_url + "/ui-app/")
        require(ui.status_code == 200 and 'id="root"' in ui.text, "云端 React 页面尚未提供。")
        report["checks"].extend(["cors_preflight", "react_ui"])
        for request in requests:
            event, progress = await stream_turn(client, base_url, {"request": request})
            city = inspect_plan(event["data"], request)
            require(any(p["role"] == "search" and p["state"] == "done" for p in progress),
                    "缺少实际地点搜索完成事件。")
            plan = event["data"]["next_step"]["result"]["plans"][0]
            invited, _ = await stream_turn(client, base_url, {
                "conversation": event["conversation"], "action": "invite", "plan_id": plan["id"]})
            output = invited["data"]
            require(output["intent"] == "invite" and output["next_step"]["selected_plan_id"] == plan["id"]
                    and output["next_step"]["invitation"] == plan["invitation"], "邀请函与选中路线不一致。")
            city.update(invitation_verified=True, progress=progress)
            report["cities"].append(city)
            if require_dated:
                require(city["all_plans_have_dated_records"], "资讯已联网，但有路线缺少覆盖出行日期的活动记录。")
        report["passed"] = True
    except (VerificationError, httpx.HTTPError, ValueError, KeyError, TypeError, ValidationError) as error:
        # HTTP errors may contain provider URLs. Only our endpoint and error type are reported.
        report["error"] = str(error) if isinstance(error, VerificationError) else type(error).__name__
    finally:
        if owned_client:
            await client.aclose()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--requests-file", help="1–3 个完整条件组成的数组；默认上海与成都公共地标。")
    parser.add_argument("--require-dated", action="store_true", help="要求每套路线至少有覆盖出行日的结构化活动记录。")
    parser.add_argument("--output", default="build/evidence/live-backend.json")
    args = parser.parse_args()
    try:
        report = asyncio.run(verify_backend(args.base_url, read_request_file(args.requests_file),
                                           require_dated=args.require_dated))
    except (VerificationError, ValueError) as error:
        parser.error(str(error))
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

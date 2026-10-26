"""Deterministic constraints: models never decide whether a route is feasible."""
from __future__ import annotations
import copy
import math
from datetime import date, datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
PREFERENCES = ("看展", "火锅", "咖啡", "散步", "逛街")

class PlanError(ValueError):
    def __init__(self, code: str, message: str, suggestion: str = ""):
        super().__init__(message)
        self.code, self.message, self.suggestion = code, message, suggestion

def minutes(value):
    try:
        if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
            raise ValueError()
        h, m = map(int, value.split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59): raise ValueError()
        return h * 60 + m
    except (ValueError, TypeError, AttributeError):
        raise PlanError("INVALID_INPUT", "请使用有效的 24 小时时间，例如 12:00–20:00。")

def clock(value): return f"{value // 60:02d}:{value % 60:02d}"
def round5(value): return math.ceil(value / 5) * 5
def commute(km): return max(5, round5(km / 20 * 60 + 10))
def stamp(): return datetime.now(timezone.utc).isoformat()
def distance(a, b):
    lng1, lat1, lng2, lat2 = map(math.radians, (a["lng"], a["lat"], b["lng"], b["lat"]))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lng2-lng1)/2)**2
    return 6371 * 2 * math.asin(min(1, math.sqrt(h)))

def normalize(raw):
    req = copy.deepcopy(raw)
    try:
        d = date.fromisoformat(req["date"])
        if d < datetime.now(CST).date(): raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise PlanError("INVALID_INPUT", "请选择尚未过去的有效日期。")
    if not isinstance(req.get("city"), str) or not req["city"].strip():
        raise PlanError("INVALID_INPUT", "请告诉我出发城市。")
    req["city"] = req["city"].strip().removesuffix("市")[:20]
    if type(req.get("budget")) not in (int, float) or not math.isfinite(req["budget"]) or not 0 <= req["budget"] <= 2000:
        raise PlanError("INVALID_INPUT", "每人预算需要在 0–2000 元之间。")
    if req.get("maxDistanceKm", 3) not in (1, 3, 5, 10):
        raise PlanError("INVALID_INPUT", "路线内移动距离支持 1、3、5 或 10 公里。")
    req["maxDistanceKm"] = req.get("maxDistanceKm", 3)
    req["audience"] = "resident"
    req["residentArea"] = str(req.get("residentArea", "")).strip()[:60]
    if req.get("maxCommuteMinutes", 45) not in (30, 45, 60):
        raise PlanError("INVALID_INPUT", "单程通勤上限支持 30、45 或 60 分钟。")
    req["maxCommuteMinutes"] = req.get("maxCommuteMinutes", 45)
    avoided = req.get("avoidPlaces", [])
    if not isinstance(avoided, list) or len(avoided) > 10 or any(not isinstance(p, str) or not p.strip() or len(p) > 60 for p in avoided):
        raise PlanError("INVALID_INPUT", "不想再去的地方最多填写 10 个，每个不超过 60 字。")
    req["avoidPlaces"] = list(dict.fromkeys(p.strip() for p in avoided))
    prefs = req.get("preferences", [])
    if not isinstance(prefs, list) or not prefs or any(p not in PREFERENCES for p in prefs):
        raise PlanError("INVALID_INPUT", "请选择看展、火锅、咖啡、散步或逛街中的一种。")
    req["preferences"] = list(dict.fromkeys(prefs))
    people = req.get("people")
    if not isinstance(people, list) or not 1 <= len(people) <= 6:
        raise PlanError("INVALID_INPUT", "目前支持同城 1–6 人一起出发。")
    for person in people:
        if not isinstance(person, dict) or not str(person.get("location", "")).strip():
            raise PlanError("INVALID_INPUT", "还需要每个人的出发地。")
        person["name"] = str(person.get("name", "朋友"))[:20]
        person["location"] = str(person["location"])[:60]
        if minutes(person.get("availableTo")) <= minutes(person.get("availableFrom")):
            raise PlanError("INVALID_INPUT", f"{person['name']}的结束时间需要晚于开始时间，不支持跨夜。")
    start = max(minutes(p["availableFrom"]) for p in people)
    end = min(minutes(p["availableTo"]) for p in people)
    if end - start < 180:
        raise PlanError("NO_COMMON_TIME", "大家的共同空闲时间不足 3 小时。", "调整每个人的空闲时间，给往返也留一点余地。")
    req["commonWindow"] = {"from": clock(start), "to": clock(end)}
    return req

def schedule(candidate, req, people, mode):
    stops = copy.deepcopy(candidate["stops"])
    if any(avoided in s["name"] for s in stops for avoided in req.get("avoidPlaces", [])): return None
    transfers = []
    for i, stop in enumerate(stops[:-1]):
        km = distance(stop["location"], stops[i+1]["location"]) * 1.3
        transfers.append({"from": i, "to": i+1, "distanceKm": round(km, 1), "minutes": max(5, round5(km/4.5*60)), "mode": "步行", "estimated": True})
    total_km = round(sum(t["distanceKm"] for t in transfers), 1)
    costs, transport = sum(s["cost"] for s in stops), 20
    if total_km > req["maxDistanceKm"] or costs + transport > req["budget"]: return None
    arrivals = [{"name": p["name"], "from": p["location"], "minutes": commute(distance(p["coordinate"], stops[0]["location"])), "returnMinutes": commute(distance(stops[-1]["location"], p["coordinate"])), "estimated": True} for p in people]
    if any(max(a["minutes"], a["returnMinutes"]) > req.get("maxCommuteMinutes", 45) for a in arrivals): return None
    cursor = round5(minutes(req["commonWindow"]["from"]) + max(a["minutes"] for a in arrivals))
    durations = [70 if s["category"] in ("火锅", "餐饮") else 65 if s["category"] == "看展" else 35 for s in stops]
    if cursor + sum(durations) + sum(t["minutes"] for t in transfers) + max(a["returnMinutes"] for a in arrivals) > minutes(req["commonWindow"]["to"]): return None
    output = []
    for i, stop in enumerate(stops):
        start = cursor; cursor += durations[i]
        output.append({"order": i+1, "name": stop["name"], "category": stop["category"], "address": stop["address"], "location": stop["location"], "startTime": clock(start), "endTime": clock(cursor), "durationMinutes": durations[i], "costEstimate": stop["cost"], "description": stop["description"], "activitySources": copy.deepcopy(stop.get("activitySources", [])), "source": {"provider": "高德地图 POI" if mode == "live" else "离线示例", "url": stop.get("sourceUrl"), "checkedAt": stamp() if mode == "live" else None, "verification": "poi_verified" if mode == "live" else "sample"}})
        if i < len(transfers): cursor += transfers[i]["minutes"]
    plan = {"id": candidate["id"], "title": candidate["title"], "subtitle": candidate["subtitle"], "tags": list(dict.fromkeys(s["category"] for s in stops)), "theme": candidate["theme"], "startTime": output[0]["startTime"], "endTime": output[-1]["endTime"], "totalMinutes": cursor-minutes(output[0]["startTime"]), "totalDistanceKm": total_km, "meetingPoint": output[0]["name"], "budgetPerPerson": {"total": costs+transport, "currency": "CNY", "activities": costs, "transport": transport, "estimated": True}, "arrivals": arrivals, "stops": output, "transfers": transfers}
    plan["invitation"] = invitation(plan, req, mode)
    return plan

def invitation(plan, req, mode):
    itinerary = "\n\n".join(f"{s['startTime']}–{s['endTime']} {s['name']}\n{s['address']}" for s in plan["stops"])
    sample = "这是一份示例路线。" if mode != "live" else ""
    return f"这周末，一起拆个城市盲盒吧！\n\n📍 {req['city']} · {plan['title']}\n🗓 {req['date']} {plan['startTime']}–{plan['endTime']}\n👋 {'、'.join(p['name'] for p in req['people'])}\n\n{itinerary}\n\n💰 每人约 ¥{plan['budgetPerPerson']['total']}（含交通预留，消费为估算）\n🚶 路线内约 {plan['totalDistanceKm']} km，交通为估算\n📌 {plan['meetingPoint']}碰面，出发前预留好路上时间。\n\n{sample}展期、营业时间、票价和预约要求还需确认；不代表已订票或订位。\n把周末留一点给我们，好不好？"

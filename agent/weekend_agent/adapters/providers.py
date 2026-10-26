from __future__ import annotations
import asyncio
import copy
import json
import math
from pathlib import Path
from urllib.parse import urlencode
from weekend_agent.domain.planning import PlanError, distance, stamp

class Places:
    def __init__(self, client, key="", data=None):
        self.client, self.key = client, key
        self.data = data or json.loads(Path(__file__).with_name("demo-data.json").read_text())

    async def _get(self, path, params):
        response = await self.client.get("https://restapi.amap.com"+path, params={**params, "key": self.key})
        response.raise_for_status(); data = response.json()
        if data.get("status") != "1": raise ValueError("provider_unavailable")
        return data

    @staticmethod
    def coordinate(value):
        lng, lat = map(float, value.split(","))
        if not (math.isfinite(lng) and math.isfinite(lat) and 73 <= lng <= 136 and 3 <= lat <= 54): raise ValueError("invalid_coordinates")
        return {"lng": lng, "lat": lat}

    def demo(self, req):
        if req["city"] != "杭州":
            raise PlanError("CITY_UNAVAILABLE", f"离线库暂时只有杭州，无法可靠推荐{req['city']}的本地路线。", "切换杭州体验，或配置地图检索后再试。")
        people = copy.deepcopy(req["people"])
        for person in people:
            origin = next((p for p in self.data["origins"] if any(alias in person["location"] for alias in p["aliases"])), None)
            if origin is None: raise PlanError("LOCATION_UNAVAILABLE", f"示例库暂时无法定位「{person['location']}」。", "可使用武林广场、西湖文化广场、杭州东站、龙翔桥、拱宸桥或天目里。")
            person["coordinate"] = {"lng": origin["lng"], "lat": origin["lat"]}
        candidates = copy.deepcopy(self.data["routes"])
        for route in candidates:
            for stop in route["stops"]:
                if stop["category"] == "看展" and "看展" not in req["preferences"]:
                    stop.update(name=stop["name"].replace("美术馆", "周边街区").replace("博物馆", "周边街区"), category="逛街" if "逛街" in req["preferences"] else "散步", cost=0, description="逛逛附近公共街区；具体通行情况以现场为准。")
                if stop["category"] == "火锅" and "火锅" not in req["preferences"]:
                    stop.update(name=stop["name"].replace("火锅时间", "吃顿便饭"), category="餐饮", cost=55, description="附近选一家合口味的小店，具体商家和价格待确认。")
                if stop["category"] == "散步" and "咖啡" in req["preferences"]:
                    stop.update(name=stop["name"]+" · 咖啡小歇", category="咖啡", cost=30, description="在这个区域选家咖啡店，具体商家和价格待确认。")
            if "火锅" not in req["preferences"]: route["subtitle"] = "在街区里慢慢逛，给相聚留出足够的时间。"
        return people, candidates

    async def locate_city(self, lng, lat):
        if not self.key: raise PlanError("LOCATION_NOT_CONFIGURED", "定位服务尚未配置，请手动填写常住城市。")
        try:
            coordinate = self.coordinate(f"{lng},{lat}")
            converted = await self._get("/v3/assistant/coordinate/convert", {"locations":f"{coordinate['lng']},{coordinate['lat']}","coordsys":"gps"})
            data = await self._get("/v3/geocode/regeo", {"location":converted["locations"],"extensions":"base"})
            address = data["regeocode"]["addressComponent"]
            city = address.get("city") or address.get("province")
            if not isinstance(city, str) or not city: raise ValueError("no_city")
            return {"city":city.removesuffix("市"),"district":str(address.get("district") or "")[:60],"needsConfirmation":True}
        except Exception: raise PlanError("LOCATION_FAILED", "暂时无法识别所在城市，请手动填写。")

    async def find(self, req, emit, *, sample=False, events=()):
        if sample:
            emit("search", "running", "正在读取杭州示例地点", "使用离线灵感库，没有进行实时搜索")
            people, candidates = self.demo(req)
            emit("search", "done", "示例地点已匹配", "展期、消费和营业状态尚未实时核实")
            return people, candidates, "demo", []
        if not self.key:
            raise PlanError("PLACES_NOT_CONFIGURED", f"{req['city']}的实时地点检索尚未配置，无法生成可靠的当地路线。", "请由部署者配置高德 Web 服务 Key。你填写的本地条件已保留。")
        try:
            emit("search", "running", "正在定位每个人的出发地", f"在{req['city']}查询地标坐标")
            async def locate(person):
                data = await self._get("/v3/geocode/geo", {"address": req["city"]+person["location"], "city": req["city"]})
                found = next((g for g in data.get("geocodes", []) if req["city"] in str(g.get("city", ""))), None)
                if not found: raise ValueError("origin_not_found")
                return {**person, "coordinate": self.coordinate(found["location"])}
            people = await asyncio.gather(*(locate(p) for p in req["people"]))
            center = {k: sum(p["coordinate"][k] for p in people)/len(people) for k in ("lng", "lat")}
            primary = "看展" if "看展" in req["preferences"] else "逛街" if "逛街" in req["preferences"] else "散步"
            categories = [primary, "火锅" if "火锅" in req["preferences"] else "餐饮", "咖啡" if "咖啡" in req["preferences"] else "散步"]
            keywords = {"看展": "美术馆", "逛街": "商场", "散步": "公园", "火锅": "火锅", "餐饮": "餐厅", "咖啡": "咖啡"}
            emit("search", "running", f"正在搜索{req['city']}附近地点", "、".join(keywords[c] for c in categories))
            async def search(category):
                data = await self._get("/v3/place/around", {"location": f"{center['lng']},{center['lat']}", "keywords": keywords[category], "radius": min(10000, int((req["maxDistanceKm"]+2)*1000)), "sortrule": "distance", "offset": 20, "page": 1})
                stops = []
                for p in data.get("pois", []):
                    try: location = self.coordinate(p["location"])
                    except (ValueError, TypeError, KeyError): continue
                    name = str(p.get("name", ""))[:100]
                    if not name or any(word in name for word in ["游客中心", "观光", "旅游集散", "一日游"]+req.get("avoidPlaces", [])): continue
                    if p.get("cityname") and req["city"] not in p["cityname"]: continue
                    stops.append({"name": name, "category": category, "address": req["city"]+" · "+str(p.get("address") or "请查看地图来源")[:120], "location": location, "cost": {"看展":60,"火锅":100,"餐饮":65,"咖啡":35}.get(category, 0), "description": "地图已检索到该地点；当前展期、营业时间、消费及预约要求仍待确认。", "sourceUrl": "https://uri.amap.com/marker?"+urlencode({"position": p["location"], "name": p["name"]})})
                return stops
            groups = await asyncio.gather(*(search(c) for c in categories))
            # Authorized dated events can introduce new venues before generic POI ranking.
            relevant = [e for e in events if e["category"] == primary][:4]
            async def event_anchor(event):
                try:
                    from .information import same_venue
                    data = await self._get("/v3/place/text", {"keywords":event["venue"],"city":req["city"],"citylimit":"true","offset":5})
                    place = next((p for p in data.get("pois", []) if same_venue(event["venue"],str(p.get("name", ""))) and (not p.get("cityname") or req["city"] in p["cityname"])), None)
                    if not place: return None
                    location = self.coordinate(place["location"])
                    if distance(center,location)>10 or any(word in place["name"] for word in req.get("avoidPlaces", [])): return None
                    return {"name":place["name"],"category":primary,"address":req["city"]+" · "+str(place.get("address") or event["venue"]),"location":location,"cost":60 if primary=="看展" else 0,"description":"来自活动库，已在地图核对场馆位置，活动场次仍需确认。","sourceUrl":"https://uri.amap.com/marker?"+urlencode({"position":place["location"],"name":place["name"]})}
                except Exception: return None
            anchors = [p for p in await asyncio.gather(*(event_anchor(e) for e in relevant)) if p]
            groups[0] = anchors + [p for p in groups[0] if p["name"] not in {a["name"] for a in anchors}]
            if any(not g for g in groups): raise ValueError("no_places")
            candidates = []
            for i, anchor in enumerate(groups[0][:12]):
                food = min((p for p in groups[1] if p["name"] != anchor["name"]), key=lambda p: distance(anchor["location"], p["location"]), default=None)
                last = min((p for p in groups[2] if p["name"] not in (anchor["name"], food["name"] if food else "")), key=lambda p: distance((food or anchor)["location"], p["location"]), default=None)
                if food and last: candidates.append({"id":f"live-{i}","title": ["给周末一点新鲜感", "在城市里，慢慢逛", "一起出门，遇见惊喜"][i%3], "subtitle": f"从{anchor['name']}出发，串起附近的美好。", "theme": ["peach","sage","lilac"][i%3], "stops":[anchor,food,last]})
            emit("search", "done", f"已找到 {sum(map(len, groups))} 个候选地点", "地图地点存在不代表展期或营业状态已确认")
            return people, candidates, "live", []
        except Exception:
            raise PlanError("SEARCH_UNAVAILABLE", f"{req['city']}的地点检索暂时不可用，未生成替代城市或虚构路线。", "请检查出发地或稍后重试，当前条件已保留。")

from .information import Activities

from __future__ import annotations
import copy
import re
from dataclasses import dataclass, field
from jsonschema import validate
from weekend_agent.domain.planning import PlanError, normalize, schedule, stamp
from weekend_agent.domain.understanding import demo_request, missing_fields

@dataclass
class Session:
    slots: dict = field(default_factory=dict)
    result: dict | None = None
    selected: str | None = None
    sample: bool = False

class WeekendPipeline:
    def __init__(self, runner, places, activities, response_schema, plan_schema):
        self.runner, self.places, self.activities = runner, places, activities
        self.response_schema, self.plan_schema = response_schema, plan_schema

    def reply(self, session, intent, message, *, action="none", options=(), invitation=None, notices=()):
        response = {"schema_version":"1.0","intent":intent,"message":message,"next_step":{"action":action,"options":[{"key":str(i+1),"text":t} for i,t in enumerate(options)],"result":session.result,"invitation":invitation,"selected_plan_id":session.selected,"slots":copy.deepcopy(session.slots),"notices":list(notices)}}
        validate(response, self.response_schema)
        return response

    async def turn(self, session, payload, emit):
        text = str(payload.get("input", "")).strip()
        notices = []
        try:
            home = payload.get("home")
            if home and payload.get("action") != "demo":
                city = home["city"].strip().removesuffix("市")
                if session.slots.get("city") and city != session.slots["city"].removesuffix("市"):
                    session.slots.pop("people", None)
                    session.result, session.selected, session.sample = None, None, False
                session.slots.update(city=city, residentArea=home.get("residentArea", ""))
            if payload.get("action") == "demo":
                session.slots = demo_request()
                session.sample = True
                decision = {"intent":"update","slots":{},"plan_id":None}
                emit("coordinator", "done", "已载入杭州体验条件", "两人、12:00–20:00、每人 180 元、看展和火锅")
            elif payload.get("action") == "invite":
                decision = {"intent":"invite","slots":{},"plan_id":payload.get("plan_id")}
            elif payload.get("request") is not None:
                session.slots = copy.deepcopy(payload["request"])
                session.sample = False
                decision = {"intent":"update","slots":{},"plan_id":None}
                emit("coordinator", "done", "已读取你填写的条件", "接下来核对时间、地点和预算")
            else:
                if not text: raise PlanError("INVALID_INPUT", "先说说你想怎么过这个周末吧。")
                decision, notices = await self.runner.understand(text, session.slots, emit)
            if decision["intent"] == "out_of_scope":
                return self.reply(session, "out_of_scope", decision.get("message") or "我只负责同城周末的信息检索、路线规划与邀请函，不提供交易。", options=["编辑本地条件"])
            if decision["intent"] == "invite":
                if not session.result:
                    return self.reply(session, "clarify", "先排好路线，再为你准备邀请函。告诉我城市、同行人、空闲时间和预算吧。", action="provide_details", options=["编辑本地条件"])
                plan_id = decision.get("plan_id") or session.selected
                number = re.search(r"第?([一二三123])(?:条|个|套)", text)
                if number:
                    index = {"一":0,"二":1,"三":2,"1":0,"2":1,"3":2}[number[1]]
                    if index < len(session.result["plans"]): plan_id = session.result["plans"][index]["id"]
                    else: raise PlanError("INVALID_INPUT", "还没有这条路线，请从已有方案里选一条。")
                plan = next((p for p in session.result["plans"] if p["id"] == plan_id), None)
                if not plan: raise PlanError("INVALID_INPUT", "请从当前路线中选择一条。")
                session.selected = plan["id"]
                emit("inviter", "done", "邀请函已整理好", "只生成文案，不会向朋友自动发送")
                return self.reply(session, "invite", f"选好了「{plan['title']}」。邀请函已放到右侧，可以复制后发给朋友。", action="share_invitation", invitation=plan["invitation"], notices=notices)
            patch = decision.get("slots", {})
            if patch.get("city") and patch["city"].removesuffix("市") != session.slots.get("city", "").removesuffix("市"):
                session.slots.pop("people", None)
                session.result, session.selected, session.sample = None, None, False
            session.slots.update(patch)
            missing = missing_fields(session.slots)
            if missing:
                return self.reply(session, "clarify", "我会按你们日常居住和出发的位置安排附近周末，还需要："+"、".join(missing)+"。\n\n可以补充常住城市、各自附近的地铁站、12:00 到 20:00 这样的空闲时段，以及每人预算。不会默认你住在杭州。", action="provide_details", options=["编辑本地条件"], notices=notices)
            if not patch and payload.get("action") != "demo" and "request" not in payload and not re.search("重试|重新|再来|生成|安排|排路线", text):
                return self.reply(session, "clarify", "可以继续修改预算、时间或偏好，也可以选择一条路线生成邀请函。复杂条件可以点「编辑条件」填写。", options=["重新生成路线", "预算改成150元", "生成邀请函"], notices=notices)
            req = normalize(session.slots)
            context = None
            if not session.sample and self.places.key:
                context = await self.activities.discover(req, emit)
            people, candidates, mode, warnings = await self.places.find(req, emit, sample=session.sample, events=context["events"] if context else ())
            activity_search = {"status":"not_configured","provider":None,"checkedAt":None}
            information = {"channels":[],"rejectedRecords":0}
            if mode == "live":
                activity_search, activity_warnings, information = await self.activities.attach(candidates, req, emit, context)
                warnings.extend(activity_warnings)
            emit("planner", "running", "正在核对路线与每个人的时间", "检查消费、移动距离、集合及返程预留")
            plans = [p for c in candidates if (p := schedule(c, req, people, mode)) is not None]
            def rank(plan):
                sources = [s for stop in plan["stops"] for s in stop["activitySources"]]
                dated = sum(s["verification"] == "provider_record" for s in sources)
                return (-dated, -min(len(sources), 3), max(a["minutes"] for a in plan["arrivals"]))
            plans.sort(key=rank)
            distinct = []; seen = set()
            for plan in plans:
                if plan["meetingPoint"] not in seen: distinct.append(plan); seen.add(plan["meetingPoint"])
            plans = distinct[:3]
            if not plans: raise PlanError("NO_MATCH", "暂时没有同时满足预算、距离、通勤与共同时间的完整路线。", "试试增加预算、放宽移动距离或通勤上限，或延长空闲时间。")
            if len(plans) == 1: warnings.append("当前条件仅匹配到 1 条完整路线，没有为凑数虚构其他方案。")
            unmatched = [pref for pref in req["preferences"] if not any(pref in p["tags"] for p in plans)]
            if unmatched: warnings.append("暂未匹配到「"+"、".join(unmatched)+"」，请查看路线的实际类型。")
            result = {"version":"1.0","request":req,"meta":{"mode":mode,"generatedAt":stamp(),"provider":"amap" if mode == "live" else "local-demo","dataNote":"按本地居住者规划。地点来自高德；活动记录与公开搜索分别标注，未核实的展期、营业与消费不会当作事实。交通和预算为估算。" if mode == "live" else "主动选择的杭州产品样例，不是你的所在地推荐，也不是实时活动。","warnings":warnings,"commonWindow":req["commonWindow"],"activitySearch":activity_search,"information":information},"plans":plans}
            validate(result, self.plan_schema)
            session.result, session.selected = result, plans[0]["id"]
            emit("planner", "done", f"已排好 {len(plans)} 条路线", "所有方案已通过预算、时间和距离校验；数值为估算")
            return self.reply(session, "plan", f"给你们排好了 {len(plans)} 条{'示例' if mode != 'live' else ''}路线。共同空闲时间是 {req['commonWindow']['from']}–{req['commonWindow']['to']}，已给每个人预留往返。\n\n先看看「{plans[0]['title']}」，也可以切换其他方案；喜欢哪条，我就帮你写邀请函。", action="choose_plan", options=["预算改成150元", "不要火锅，想喝咖啡", "生成邀请函"], notices=notices)
        except PlanError as error:
            emit("planner", "warning", "这次安排需要调整", error.message)
            return self.reply(session, "error", error.message+"\n"+error.suggestion, action="retry", options=["重新生成路线", "编辑本地条件"], notices=notices)

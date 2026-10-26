"""A deliberately bounded offline parser; live natural-language extraction is injected."""
import copy
import re
from datetime import datetime, timedelta
from .planning import CST

LOCATIONS = ("西湖文化广场", "武林广场", "杭州东站", "龙翔桥", "拱宸桥", "天目里")

def requires_external_action(text):
    transactions = r"(?:订票|订位|付款|支付|买票|发消息)"
    # A negated transaction is a boundary preference, not an instruction to transact.
    negated = r"(?:不需要|不用|不要|无需|不必|不想|别|不)(?:你|帮我|帮我们|替我|再|去|为我)*\s*" + transactions
    negated += r"(?:\s*(?:并|和|或|及|、|也)\s*" + transactions + r")*"
    return bool(re.search(transactions + r"|作业|写代码", re.sub(negated, "", text)))

def people_count(value):
    if value.isdigit(): return int(value)
    digits = {"零":0,"〇":0,"一":1,"二":2,"两":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9}
    if value in digits: return digits[value]
    if "十" in value and value.count("十") == 1:
        tens, ones = value.split("十")
        if (not tens or tens in digits) and (not ones or ones in digits):
            return digits.get(tens, 1)*10 + digits.get(ones, 0)
    return 100  # Other large Chinese numerals exceed this demo's six-person limit.

def update_times(text, people):
    matches = list(re.finditer(r"(\d{1,2})(?::(\d{2})|点|时)\s*(?:到|至|[-—~～])\s*(\d{1,2})(?::(\d{2})|点|时)", text))
    assignments = {}
    for i, match in enumerate(matches):
        prefix = re.split(r"[，,。；;\n]", text[matches[i-1].end() if i else 0:match.start()])[-1]
        if re.search(r"大家|我们|全员|所有人|都", prefix):
            targets = list(range(len(people)))
        else:
            names = [(p.get("name", ""), j) for j, p in enumerate(people) if p.get("name")]
            aliases = sorted({name for name, _ in names}, key=len, reverse=True)
            named = list(re.finditer("|".join(re.escape(n) for n in aliases), prefix)) if aliases else []
            targets = [j for name, j in names if named and name == named[-1].group()]
            if not targets and len(matches) == 1: targets = list(range(len(people)))
        window = (f"{int(match[1]):02d}:{int(match[2] or 0):02d}", f"{int(match[3]):02d}:{int(match[4] or 0):02d}")
        if not targets or any(j in assignments and assignments[j] != window for j in targets):
            for person in people: person.update(availableFrom="", availableTo="")
            return "这几个空闲时段还不能对应到每个人。请分别写明姓名和时间，例如「我12:00到18:00，朋友14:00到20:00」，或在条件表单中填写。"
        for j in targets: assignments[j] = window
    for j, (start, end) in assignments.items():
        people[j].update(availableFrom=start, availableTo=end)
    return ""

def demo_request():
    today = datetime.now(CST).date()
    day = today + timedelta(days=(5-today.weekday()) % 7)
    return {"city": "杭州", "date": day.isoformat(), "budget": 180, "maxDistanceKm": 3, "preferences": ["看展", "火锅"], "people": [{"name": "我", "location": "武林广场", "availableFrom": "12:00", "availableTo": "20:00"}, {"name": "朋友", "location": "西湖文化广场", "availableFrom": "12:00", "availableTo": "20:00"}]}

def extract(text, current):
    if requires_external_action(text):
        return {"intent": "out_of_scope", "slots": {}, "message": "我负责找信息、排路线和写邀请函；订票、订位与发送消息需要你自己操作。", "plan_id": None}
    if re.search(r"邀请函|发给朋友|选(?:第)?[一二三123](?:条|个|套)|第[一二三123](?:条|个|套)", text):
        return {"intent": "invite", "slots": {}, "message": "", "plan_id": None}
    slots = {}
    city = re.search(r"杭州|上海|北京|成都|深圳|广州|南京|苏州|武汉|重庆|天津|西安|长沙|郑州|青岛|济南|厦门|福州|宁波|无锡|合肥|昆明|贵阳|南宁|南昌|长春|沈阳|大连|哈尔滨|石家庄|太原|兰州|海口|三亚|珠海|佛山|东莞|温州|绍兴|嘉兴|常州|洛阳|泉州|烟台|银川|西宁|乌鲁木齐|呼和浩特|拉萨", text)
    if city: slots["city"] = city.group()
    else:
        explicit = re.search(r"(?:我住在|常住|城市是|城市改成|在)([\u4e00-\u9fff]{2,8})市", text)
        if explicit: slots["city"] = explicit[1]
    area = re.search(r"(?:常住片区|常住区域|住在)(?:是|为)?([^，,。；;\n]{2,20}(?:区|街道|片区))", text)
    if area: slots["residentArea"] = area[1]
    commute = re.search(r"(?:通勤|单程)(?:不超过|最多|上限)?\s*(30|45|60)\s*分钟", text)
    if commute: slots["maxCommuteMinutes"] = int(commute[1])
    avoid = re.search(r"(?:不想再去|排除|去过了不去)(?:的地方是|的地方|：|:)?\s*([^。；;\n]{1,200})", text)
    if avoid: slots["avoidPlaces"] = [p.strip() for p in re.split(r"[、，,]", avoid[1]) if p.strip()][:10]
    day = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if day: slots["date"] = day.group()
    else:
        weekday = 5 if re.search(r"周六|星期六|礼拜六", text) else 6 if re.search(r"周日|周天|星期天|星期日|礼拜天|礼拜日", text) else 5 if "周末" in text else None
        if weekday is not None:
            today = datetime.now(CST).date()
            offset = (weekday-today.weekday()) % 7
            if "下周" in text: offset = 7-today.weekday()+weekday
            slots["date"] = (today+timedelta(days=offset)).isoformat()
    count = re.search(r"(\d+|[零〇一二两三四五六七八九十百]+)\s*(?:个)?人", text)
    people = copy.deepcopy(current.get("people", []))
    if count:
        n = people_count(count.group(1))
        if not 1 <= n <= 6:
            return {"intent": "out_of_scope", "slots": {}, "message": "目前支持 1–6 人的同城周末规划，请调整人数。", "plan_id": None}
        people = (people + [{"name": "我" if i == 0 else "朋友" if i == 1 else f"朋友{i}", "location": "", "availableFrom": "", "availableTo": ""} for i in range(len(people), n)])[:n]
    questions = []
    amount = r"\s*[¥￥]?\s*(\d+(?:\.\d{1,2})?)(?![\d.])"
    total = re.search(r"(?:总预算|一共预算|预算总共)(?:改[成为]|为|是)?" + amount, text)
    budget = re.search(r"(?:每人(?:预算)?|人均|预算(?:改[成为]|为|是)?)" + amount, text)
    if total:
        if people:
            # Round down to cents so the per-person allowance never exceeds the total.
            from decimal import Decimal, ROUND_DOWN
            slots["budget"] = float((Decimal(total[1])/len(people)).quantize(Decimal('.01'), rounding=ROUND_DOWN))
        else:
            questions.append("你给的是总预算，请先补充同行人数，或直接填写每人预算。")
    elif budget: slots["budget"] = float(budget.group(1))
    prefs = list(current.get("preferences", []))
    for pref, keyword in (("看展", "看展|展览|美术馆"), ("火锅", "火锅"), ("咖啡", "咖啡"), ("散步", "散步|公园"), ("逛街", "逛街|小店")):
        if re.search(r"(?:不想|不要|不吃|不喝|取消|不去|不).{0,3}(?:"+keyword+r")", text): prefs = [p for p in prefs if p != pref]
        elif re.search(keyword, text) and pref not in prefs: prefs.append(pref)
    if prefs != current.get("preferences", []): slots["preferences"] = prefs
    if "不想走太远" in text or "不走太远" in text: slots["maxDistanceKm"] = 3
    km = re.search(r"(1|3|5|10)\s*(?:km|公里|千米)", text, re.I)
    if km: slots["maxDistanceKm"] = int(km.group(1))
    locations = list(re.finditer(r"(?:从|改在|出发地是)\s*([^，,。；;\n]{2,25}?)(?:出发|[，,。；;\n]|$)", text))
    if not locations:
        locations = list(re.finditer("|".join(LOCATIONS), text))
    for i, match in enumerate(locations):
        if not people: break
        value = match.group(1).strip() if match.lastindex else match.group()
        prefix = text[max(0, match.start()-8):match.start()]
        index = 1 if ("朋友" in prefix or "第二" in prefix) and len(people) > 1 else 0 if "我" in prefix else min(i, len(people)-1)
        people[index]["location"] = value
    if people:
        question = update_times(text, people)
        if question: questions.append(question)
    if people and people != current.get("people"): slots["people"] = people
    return {"intent": "update", "slots": slots, "message": "\n".join(questions), "plan_id": None}

def missing_fields(slots):
    missing = []
    for key, label in (("city", "出发城市"), ("date", "出发日期"), ("budget", "每人预算"), ("preferences", "想玩的类型")):
        if key not in slots or slots[key] in (None, "", []): missing.append(label)
    if not slots.get("people"): missing.append("同行人数，以及每个人的出发地和空闲时间")
    else:
        for p in slots["people"]:
            name = p.get("name", "朋友")
            if not p.get("location"): missing.append(f"{name}的出发地")
            if not p.get("availableFrom") or not p.get("availableTo"): missing.append(f"{name}的空闲时间")
    return missing

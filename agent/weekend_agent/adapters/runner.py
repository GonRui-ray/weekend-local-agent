import json
from datetime import datetime
from jsonschema import validate
from weekend_agent.domain.understanding import extract
from weekend_agent.domain.planning import CST

class StructuredRunner:
    def __init__(self, client, *, key, base_url, model, prompt, schema):
        self.client, self.key, self.base_url, self.model = client, key, base_url.rstrip("/"), model
        self.prompt, self.schema = prompt, schema

    async def understand(self, text, slots, emit):
        if not self.key:
            emit("coordinator", "done", "已读取你的周末期待", "演示模式使用规则理解；复杂表达可在配置模型后尝试")
            return extract(text, slots), []
        emit("coordinator", "running", "正在理解这次的安排", "模型提取需求，程序校验后才会用于规划")
        for attempt in range(2):
            try:
                response = await self.client.post(self.base_url+"/chat/completions", headers={"Authorization":"Bearer "+self.key}, json={"model":self.model,"messages":[{"role":"system","content":self.prompt},{"role":"user","content":json.dumps({"today":datetime.now(CST).date().isoformat(),"current_slots":slots,"input":text},ensure_ascii=False)}],"response_format":{"type":"json_object"},"temperature":0.2,"max_tokens":2000}, timeout=12)
                response.raise_for_status()
                decision = json.loads(response.json()["choices"][0]["message"]["content"])
                validate(decision, self.schema)
                emit("coordinator", "done", "需求已通过结构校验", "只更新本轮明确提出的条件")
                return decision, []
            except Exception:
                if attempt == 0: emit("coordinator", "running", "正在重试需求理解", "上次模型调用或结构校验未完成")
        emit("coordinator", "warning", "已切换规则理解", "模型服务暂时不可用，保留输入继续处理")
        return extract(text, slots), ["模型服务暂时不可用，本轮使用规则理解；请核对右侧需求摘要。"]

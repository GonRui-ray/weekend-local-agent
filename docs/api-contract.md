# HTTP / SSE 与 JSON 契约

这些是 Python 后端已实现的接口。**当前 GitHub Pages 样例网址不提供这些接口**；需先将 Python 服务部署到 ModelScope、Render 或其他云主机。接口定义见 [space/app.py](../space/app.py)。

## 接口清单

| 方法 / 路径 | 返回与用途 |
| --- | --- |
| `GET /health` | 进程健康，`{"ok":true,"schema_version":"1.0"}` |
| `GET /ready` | 必需配置检查；缺地图或活动来源返回 503；200 也不证明真实 API 调用已成功 |
| `GET /api/config` | model / places / activities / feed 布尔状态；不返回任何 Key |
| `POST /api/location` | GPS 经纬度识别城市、片区，返回 `needsConfirmation: true` |
| `POST /turn` | 对话、条件更新、规划、邀请函；返回 `text/event-stream` |
| `GET /ui-app/` | Python 同源 React 使用端 |
| `GET /` | Gradio 入口，嵌入 React 使用端 |

若前端在 Pages、Python 在另一域名：后端配置 `CORS_ORIGINS=https://gonrui-ray.github.io`，前端 Actions Variable `AGENT_API_URL` 指向已发布的后端 HTTPS 根地址。前端只收到公开服务地址，不接收供应商 Key。

## 输入

输入由 [turn.schema.json](../agent/turn.schema.json) 校验，示例见 [example-resident-request.json](../example-resident-request.json)。`request` 是完整条件表单；`input` 是本轮自然语言修改。

```json
{
  "conversation": null,
  "request": {
    "city": "上海",
    "residentArea": "静安区",
    "date": "2026-10-31",
    "budget": 180,
    "maxDistanceKm": 3,
    "maxCommuteMinutes": 45,
    "avoidPlaces": ["这次不想去的场馆"],
    "preferences": ["看展", "火锅"],
    "people": [
      {"name": "我", "location": "日常出发地附近地铁站", "availableFrom": "12:00", "availableTo": "20:00"},
      {"name": "朋友", "location": "朋友附近地铁站", "availableFrom": "14:00", "availableTo": "20:00"}
    ]
  }
}
```

日期、城市和起点为格式示例；使用时填写真实条件并选择尚未过去的日期。`budget` 是每人人民币预算。聊天中的“总预算”在人数明确时折算到人均；人数未知时先追问。

后续轮次携带上一轮返回的 `conversation`：

```json
{"conversation":"上一轮返回的会话标识","input":"总预算300元，朋友14:00到20:00有空"}
```

选择已有路线生成邀请函：

```json
{"conversation":"上一轮返回的会话标识","action":"invite","plan_id":"已有路线ID"}
```

主动样例输入为 `{"action":"demo"}`，与真实条件检索有明确区别。

## SSE 事件

每条事件为一行 `data: JSON`，以空行分隔。未结束时每 10 秒可收到 SSE 心跳注释。服务端总超时 60 秒，客户端超时 75 秒。

```text
data: {"type":"progress","role":"search","state":"running","title":"正在搜索附近地点","detail":"查询工具的真实执行状态"}

data: {"type":"result","conversation":"会话标识","data":{"schema_version":"1.0","intent":"clarify","message":"还需要每个人的出发地和空闲时间。","next_step":{"action":"provide_details","options":[],"result":null,"invitation":null,"selected_plan_id":null,"slots":{},"notices":[]}}}

```

示例事件展示格式，不是本次已执行真实搜索的日志。`progress.role` 用于界面标识步骤；`state` 说明运行、完成或警告。

## 固定最终输出

最终 `data` 只有四个顶层字段：

| 字段 | 说明 |
| --- | --- |
| `schema_version` | 当前 `1.0` |
| `intent` | `clarify` / `plan` / `invite` / `out_of_scope` / `error` |
| `message` | 给用户的简短说明或可恢复错误 |
| `next_step` | 下一步、选项、路线、邀请函、当前槽位与提示 |

`next_step.result` 是完整路线对象或 `null`，包含：标准化条件、模式、查询时间、来源渠道状态、共同窗口、警告、路线数组。每条路线包含站点顺序、时间、地址、预算、站间移动、每个人的往返估算及邀请函。

- [response.schema.json](../agent/response.schema.json)：对外响应结构。
- [plan.schema.json](../agent/plan.schema.json)：完整路线结构。
- [coordinator.schema.json](../agent/coordinator.schema.json)：协调模型只输出提取槽位，不输出任意地点或路线。
- [example-response.json](../example-response.json)：可解析且经过 Schema 校验的样例输出，非实时本地活动结果。

## 调用与错误

把下方 `https://your-cloud-api.example` 替换为实际部署的云后端。它是占位域名，不是本项目已经发布的接口。

```bash
curl -N 'https://your-cloud-api.example/turn' \
  -H 'Content-Type: application/json' \
  --data-binary @example-resident-request.json
```

无效 JSON / Schema 错误返回 HTTP 400；请求体大于 20 KB 返回 413；同一会话并行提交返回 409；会话容量满时返回 503。已进入规划的预算、时间冲突、无可行路线或供应商错误通常通过 SSE 最终 `intent=error` 返回，前端应同时判断 HTTP 与业务结果。

活动供给契约、字段可信度及平台内容接入见 [信息接入与整合](../信息接入与整合.md)。

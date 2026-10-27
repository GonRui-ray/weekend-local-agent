# 后续技术预案：从样例到真实联网

**2026-10-27 阶段决定：保留产品 Demo，暂不推进云端后端部署。** 当前无需登录云平台、申请密钥或执行下述脚本；近期工作是 Demo 任务验证与交互完善，见 [项目介绍与后续计划](product-overview.md)。

下述内容仅供以后决定恢复实接时参考。部署配置、后端和验收程序已有，真实云服务尚未创建，供应商凭证尚未配置；没有已验收可用的 `onrender.com` 地址。

## 先部署一套完整应用

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/GonRui-ray/weekend-local-agent)

点击按钮，用你自己的 Render 账号登录，确认导入本仓库的 `render.yaml`。配置默认选择免费 Web Service、Singapore 区域、Docker 构建与手动发布；是否能使用免费资源及账号验证要求，以平台实际页面为准。若平台要求选择付费方案，先暂停，不要为完成这个步骤直接升级。

首次创建只需要填写两项供应商凭证：

| 云端 Secret | 如何取得 | 用途 |
| --- | --- | --- |
| `AMAP_KEY` | [高德控制台](https://console.amap.com/dev/key/app)：创建应用，申请 **Web 服务** Key | 根据用户确认的城市查询各人起点、场馆和附近地点 |
| `TAVILY_KEY` | [Tavily 控制台](https://app.tavily.com/)：获取搜索 API Key | 搜索官方文化资讯、本地生活与可公开索引的社区页面 |

密钥只填写在 Render 的环境变量中，不发到聊天，不放入 GitHub，也不填在前端网址里。服务商可能有配额或收费，按各自控制台设置用量。模型 Key 首次不必填写；缺模型时使用受限规则与明确条件表单。需要模型后再在服务端添加 `MODEL_API_KEY`、`MODEL_BASE_URL` 与实际可用的 `MODEL_NAME`。

Render 首次构建完成并显示 Live 后，复制平台分配的 **HTTPS 根网址**。应用本身就在这个网址运行，手机可直接打开；`/ui-app/` 也提供同一个 React 界面。该部署把 Python 与 React 放在同一云服务上，不需要在用户电脑运行程序。

免费实例可能休眠。首次请求先等待服务唤醒，随后再开始规划。部署默认不加载 Gradio 包装页以减少运行内存；ModelScope 包保留原本的 Gradio 入口。

## 再验收真实调用

在已安装本仓库 Python 依赖的环境执行以下命令，把 `你的云服务.onrender.com` 替换为实际服务地址：

```bash
python scripts/verify_live_backend.py \
  --base-url https://你的云服务.onrender.com \
  --output build/evidence/live-backend.json
```

默认使用下一个周六、上海和成都的公共地标作为验收起点，并使用较宽松的预算与时间。它会触发两轮真实规划及两轮邀请函请求，使用云端供应商配额。这不是对这两个城市一定有活动的承诺；没有合适资料时验收应失败，并记录原因。

也可以用 `--requests-file` 指定 1–3 个完整条件组成的 JSON 数组，选择你准备首发的城市与实际需求。单个元素的字段参考 [居民需求示例](../example-resident-request.json) 中的 `request`，不要在文件中附 `conversation` 或 `action=demo`。

验收包含：

1. `/health` 存活、`/ready` 配置就绪、`/api/config` 有地图与信息源。未就绪时不继续调用供应商。
2. 同源 React 界面可访问；CORS 允许现有 `https://gonrui-ray.github.io` 的 POST。
3. 实际调用 `/turn`，读取 SSE 执行状态与 Schema 合格的最终 JSON。
4. 结果必须是 `live`，城市和日期不能改变，必须有地图来源和与路线匹配的活动/资讯 URL。只有地图地点、样例或空结果均失败。
5. 检查预算、距离、各人往返、共同时间和通勤上限；选中路线后在同一会话生成对应邀请函。

报告只保存城市、日期、来源 URL、渠道状态、执行阶段和验收结论，不保存会话令牌或各人出发地址。默认输出在 Git 忽略的 `build/evidence/` 下。代码测试中的外部接口使用受控响应，不会被保存成真实联网证据。

## 区分“链路已接通”与“活动日期已有依据”

默认验收通过表示：云后端真实调用了地图与资讯工具，返回有来源的路线，且邀请流程通过。**公开搜索摘要仍是线索，不能据此声称展期、营业或余票已核实。** 报告中的 `plans_with_dated_records` 会另行记录日期覆盖情况；来源链接返回给用户，不等于本验收已经逐页打开并人工核实正文。

更严格的活动验收：

```bash
python scripts/verify_live_backend.py \
  --base-url https://你的云服务.onrender.com \
  --require-dated
```

该模式要求每套路线至少含一条结构化活动记录，其开始/结束日期覆盖出行日。需要通过云端 `EVENT_FEED_URL` 或 `EVENT_FEED_PATH` 接入真实、有维护的活动供给，不能上传空模板或伪造记录通过验收。供给可先从首发城市的主办方/场馆公开公告人工核对整理，保留原链接与日期，再逐步接授权 API。

通过严格模式仍不等于完成具体场次、营业时段、实时库存或真实交通核验。当前交通和部分费用为估算，这些限制会在界面中保留。要验收完整可执行的本周末活动，应继续人工核对真实案例，补齐场次、营业时间和实际交通能力。

## 最后连接现有 GitHub 网页

登录了仓库有权管理者的 `gh` 环境中，可以执行：

```bash
python scripts/connect_cloud.py --base-url https://你的云服务.onrender.com
```

程序先做上述真实验收；失败就保留现有 Pages 配置。通过后才把公开网址写入 GitHub Actions 的 `AGENT_API_URL`，触发 Pages 重发。它不接收或上传供应商密钥。用 `--check-only` 可以只验收；`--require-dated` 可提高接通门槛。

Render 的默认 CORS 已允许本项目的 Pages；仓库换账号时，修改云端 `CORS_ORIGINS` 和验收脚本的 `PAGES_ORIGIN`。如果不需要 GitHub Pages，直接使用云服务自身的网页也可完成同源流程。

Pages 发布成功后，再从手机打开原来的体验地址，确认样例版标识已切换，填真实城市与条件，查看真实执行状态、来源链接并复制邀请函。然后测试换城市、无结果和重试；不应自动跳回杭州样例。此步骤还需浏览器与真机验证，不能只凭 `/ready=200` 宣称已上线完成。

## 后续恢复部署时的前置条件

先确定单城供给范围、可核验的活动记录与维护方式，再评估服务商授权、凭证、配额和维护成本。未来决定恢复时，由账号持有者配置云平台和服务端 Secrets，通过真实接口验收后再连接 Pages；平台登录口令与供应商密钥不放在聊天或仓库中。**这些是后续启动条件，当前没有待用户完成的部署步骤。**

参考：[Render 部署按钮](https://render.com/docs/deploy-to-render)、[Blueprint 规范](https://render.com/docs/blueprint-spec)。

---
title: 周末盲盒策划师
emoji: 🌿
colorFrom: green
colorTo: yellow
sdk: gradio
sdk_version: 6.30.0
python_version: "3.12"
app_file: app.py
pinned: false
---

# 周末盲盒策划师 · 本地生活

为住在当地的人整合附近的新鲜事。确认常住城市、片区和每个人的日常出发地，结合预算与时间安排 1–3 条路线，再生成邀请函。只负责查信息、排路线和文案，不订票、不支付、不代发消息。

在 Gradio 创空间上传本目录全部文件，入口 `app.py`，启动命令 `python app.py`。`HOST=0.0.0.0`、`PORT=7860`。凭证只放平台保密变量。

| 配置 | 用途 |
| --- | --- |
| AMAP_KEY | 当地真实推荐必需；高德 Web 服务，城市 / 起点定位与 POI |
| TAVILY_KEY | 官方、本地生活及小红书公开可检索页面发现 |
| EVENT_FEED_URL | 可选，授权活动供给 HTTPS JSON，符合 event-feed.schema.json |
| EVENT_FEED_TOKEN | 可选，供给网关 Bearer 凭证 |
| EVENT_FEED_PATH | 可选，平台上传的活动文件路径，与 URL 二选一 |
| MODEL_API_KEY / MODEL_BASE_URL / MODEL_NAME | 可选，模型需求理解；默认接口 https://api-inference.modelscope.cn/v1，模型按账号可用 ID 配置 |

真实演示至少配置地图和一个有数据的信息源。未配置时保留用户城市与条件，不会自动用杭州替代。设置页可主动看明确标记的杭州产品样例。空的 event-feed.template.json 仅提供结构，不是活动数据。

浏览器定位只有点击授权才执行，识别城市、片区后仍需确认常住地，不使用服务器 IP。通勤上限和不想再去的地点实际参与筛选。来源记录保留链接、日期与状态；公开搜索不等于官方 API 或活动已核实。

`/health` 用于进程健康检查，`/ready` 表示服务配置是否齐全；它不替代真实调用验证。`/config` 保持 Gradio 兼容，React 页面在 `/ui-app/`，接口 `/turn` 同源。单实例内存会话，闲置 30 分钟后清理；页面刷新重新开始。

参照 [edu-agent-learning-coach](https://github.com/julikaalice-ux/edu-agent-learning-coach) 的 React 双场景、FastAPI /turn、Gradio 入口与结构化校验模式改造；首页素材来自该参考项目。

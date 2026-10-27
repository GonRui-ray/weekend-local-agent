# 在 GitHub 上建设与展示

GitHub 保存完整源码、管理变更、执行检查和发布页面。GitHub Pages 提供公网 HTTPS 前端；Python Agent、外部检索和凭证留在云后端运行。

**当前阶段：已有公开样例 Demo，暂不推进 Python 云后端部署。** 以下仓库创建与部署说明是技术参考，不是当前待办。近期重点与后续阶段见 [项目介绍与后续计划](docs/product-overview.md)。

## 创建 GitHub 仓库

当前机器登录 `gh auth login --web --scopes workflow` 后，在仓库根目录运行 `python3 scripts/publish_github.py`，将创建当前账号下的公开 `weekend-local-agent` 仓库并推送、启用 Pages 工作流。私有源码使用 `--private`，不自动启用 Pages。脚本要求 main 分支已提交且工作区干净，不会覆盖其他 origin，不接受明文 Token。

## GitHub Pages 产品展示

仓库默认分支使用 `main`。在 Settings → Pages → Build and deployment 中选择 **GitHub Actions**；`.github/workflows/pages.yml` 会构建并发布前端。仓库名可以更改，工作流自动设置资源路径。

没有云后端地址时，页面明确显示「交互体验版 · 样例数据，非实时活动」。首页可直接点「先拆一个样例盲盒」，主动载入杭州产品样例，试选路线、看详情、生成并复制邀请函。也可填写常住地；静态页面不会假装搜索了真实活动，历史样例日期仅作示意，不会当作其他城市的所在地推荐。

## 后续参考：接通完整云端 Agent（暂不执行）

以后恢复实接时，可按 [真实联网部署与验收](docs/live-launch.md) 操作：后端返回真实、有来源的路线并通过验收后，`scripts/connect_cloud.py` 才连接现有 Pages。部署、配置就绪和真实活动核实是不同阶段。当前不执行这些步骤。

方案一：在 ModelScope 创建 Gradio 创空间。从 Actions → Check and package Agent 的成功运行下载 `weekend-agent-modelscope`，将包内所有文件上传到空间根目录。入口为 `app.py`。配置地图、信息源、可选模型 Key。完整同源界面直接使用该空间的 `/ui-app/` 地址。

方案二：在 Render 的 Blueprint 中连接本 GitHub 仓库，使用根目录 `render.yaml` 和 `Dockerfile`。配置保密变量后手动部署。此处只是可审核的部署定义，没有自动创建云账号或付费资源。免费实例可能休眠，首次访问要等待启动；数据 Key 的使用额度另由服务商计算。

需要让 GitHub Pages 调用云后端时：

1. 在后端环境配置 `CORS_ORIGINS=https://你的用户名.github.io`，只写 origin，不含仓库路径。
2. 在 GitHub Settings → Secrets and variables → Actions → Variables 中设置 **AGENT_API_URL**，值是后端 HTTPS 根地址。
3. 在 Actions 手动运行 Publish product showcase。它只把公开服务地址编译进前端，所有模型与地图 Key 留在后端。
4. 从 Pages 发起一轮真实规划，核对 `/ready`、来源状态和活动日期。后端服务成功后 Pages 自动使用真实接口，不再显示静态预览横幅。

若平台入口处于跨域 iframe 或禁止直接访问接口，优先使用平台的完整同源界面。切勿在 Pages 变量中填 API Key。

## 自动检查

`ci.yml` 在 push 和 PR 上运行 Python 测试、TypeScript 检查、Vite 构建和 ModelScope 打包；成功后上传平台部署包。这里只做检查，不自动合并 PR 或发布到第三方账号。

发布前仍需取得真实活动来源，不能用空供给模板、地图 POI 或静态样例代替本周末活动。更多说明见 README 与信息接入文档。

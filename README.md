# X Operations

面向技术热点和技术资讯的 X 内容工作台。支持多语言草稿、来源对照、版本审核、Postiz 排期及发布结果核对。

## 当前交付

已实现 M1：人工确认素材 → 指定语言生成或手动编辑 → 版本审核 → 单条文字排期 → 发布结果核对。

- 总览、素材库、草稿审核、发布日历、账号设置五个页面。
- SQLite 持久化任务、操作记录、不可变草稿版本与版本批准。
- 显式配置的 Chat Completions JSON 协议生成器；未配置时不返回模拟内容。
- Postiz 创建、查询、取消适配器；超时先核对，不盲目重复创建。
- 登录、密码哈希、会话、CSRF 校验；默认仅监听本机。
- 暂停发布、未来排期取消请求、确认取消后以新时间重新排期。

自动搜索、指标周报、金额预算、有限自动放行和媒体发布属于后续阶段。Thread 可编辑和保存，M1 不发送 Thread。

## Windows 本地启动

需要 Python 3.11+。本项目已在 Windows / Python 3.13 上进行本地验证。

```powershell
cd D:\project\X
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m app.auth init
.\.venv\Scripts\python.exe -m app
```

浏览器打开 http://127.0.0.1:8000 ，使用刚创建的管理员密码登录。另开终端运行后台任务：

```powershell
cd D:\project\X
.\.venv\Scripts\python.exe -m app.worker
```

单次执行一个到期任务：

```powershell
.\.venv\Scripts\python.exe -m app.worker --once
```

Linux/macOS 使用 `.venv/bin/python`。`app.auth init` 在本地交互输入密码并创建私有 `.env`，已有管理员配置时拒绝覆盖。更换 session secret 会使现有会话失效。

## 配置和第一篇内容

1. 登录后在“账号与设置”填写目标读者、主题、语气、时区和数量上限。每天 2 条只是可选起点；初始账号暂停发布，每日生成上限为 0。
2. 在“素材与来源”录入你已阅读确认的公开链接和正文；同一链接的正文保留稳定，不能覆盖已被引用的资料。
3. 在“草稿审核”选择来源、栏目和语言标签，例如 `zh-CN`、`en`、`ja`。可以手动编辑保存，也可以配置模型后生成。
4. 核对来源后批准具体版本。改文后生成新版本，需要重新批准。
5. 配置 Postiz 后在设置中启用发布，选择至少两分钟后的带时区时间，如 `2027-01-01T09:00:00+08:00`。
6. worker 将任务交给 Postiz 并核对结果。只有远端明确返回发布状态才显示“已发布”，实际链接只接受已核对的 X/Twitter 帖子 URL。

表单保存或入队不会自动启动 worker；任务长期等待时先检查后台进程。

## 模型配置

编辑 `.env`，填写服务端配置后重启 Web 和 worker：

```dotenv
XOPS_LLM_BASE_URL=https://your-provider.example/v1
XOPS_LLM_API_KEY=
XOPS_LLM_MODEL=
```

适配器明确使用 `POST {base}/chat/completions`、`response_format: {"type":"json_object"}` 与 `choices[0].message.content` 协议。配置的服务和模型必须支持此协议；不宣称兼容任意服务。模型名称由用户明确填写，不绑定默认模型。

最多选 10 个来源，每个来源最多提交 12000 个字符的正文。生成请求会将选定正文和账号写作配置发送给所配置的服务。每次用户点击生成对应一次有界调用；次数预留后即计入账号时区当天的上限，失败或结果不明也保留计数，避免反复产生费用。M1 展示任务错误并保存用量，尚不估算金额。

结构校验不会代替事实和语言审核，最终内容仍需人工核对。

## Postiz 配置

Postiz 单独部署和维护。先按官方文档连接目标 X 账号，再配置：

```dotenv
XOPS_POSTIZ_BASE_URL=https://your-postiz.example/api/public/v1
XOPS_POSTIZ_API_KEY=
XOPS_POSTIZ_INTEGRATION_ID=
```

Cloud 常用 API base 为 `https://api.postiz.com/public/v1`；自部署按实际实例地址填写。base 已含完整 API 前缀，不包含 `/posts`。服务端使用原样 `Authorization: <api-key>`。

本项目根据下列官方文档实现契约，真实部署版本需要联调验证：

- [创建帖子](https://docs.postiz.com/public-api/posts/create)
- [按时间范围查询](https://docs.postiz.com/public-api/posts/list)
- [取消/删除排期任务](https://docs.postiz.com/public-api/posts/delete)
- [X 连接配置](https://docs.postiz.com/self-host/providers/x-twitter)
- [生成协议参考](https://developers.openai.com/api/reference/resources/chat)

## 发布状态与恢复

`待提交 → 提交中 → 已排期 → 已发布`；结果不明进入“待核对”，明确拒绝进入“失败”。

- 同一版本只保留一个未取消意图，重复提交不重复创建。
- 已确认取消后，返回对应草稿选择**新的时间**，可以创建新排期；旧记录保留。同一时间的取消意图不会自动复活。
- 网络超时、5xx 或 worker 在请求后中断时先查询远端；无法唯一匹配则等待核对，不自动重发。
- worker 每五分钟核对远端队列，持续到排期后 72 小时；连续 12 次核对错误后停止自动重试，保留待核对状态，可在日历手动入队。
- 暂停拦截本地待提交任务，并请求取消远端未来排期。临近发布时仍尽力取消；远端已开始执行时存在竞态，不能承诺撤回已经发布的内容。
- 远端明确为 PUBLISHED 时只回收结果，不调用删除。远端取消响应不明确时不显示“已取消”。
- 修改已排期草稿不会自动改写 Postiz 中的内容；需要在日历取消旧排期，确认结果后为新版本排期。

SQLite 与 Postiz 之间不存在分布式事务；本系统不宣称跨服务严格 exactly-once。过期的本地待提交新闻要求重新审核，停机恢复时不会集中补发。

## 数据与备份

默认私有数据目录 `data/`，数据库 `data/xops.sqlite3`。M1 正文保存在 SQLite，后续媒体使用私有文件目录。`.env`、数据库、测试缓存和运行数据已排除出 Git。

在线备份包含已提交的 WAL 数据，输出文件必须尚不存在：

```powershell
.\.venv\Scripts\python.exe -m app.backup --output data/backups/manual-2026-11-13.sqlite3
```

schema 升级前自动备份到 `data/backups/`。恢复时：先停止 Web 和 worker，另行备份现状，再将选中的备份恢复到独立数据目录并通过 `XOPS_DATA_DIR` 指向该目录；不要把备份覆盖到正在使用的 SQLite 文件。恢复后先保持暂停状态、核对已有远端任务，再恢复运行。

远程部署需显式配置可信 Host、HTTPS 和 `XOPS_SECURE_COOKIE=true`。本项目不自动公开本地服务、配置代理或修改平台权限。

## 关键验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m compileall -q app
```

测试使用临时 SQLite 和 HTTPX 模拟传输，不发送真实帖子、不消耗模型额度。真实联调需要自行配置服务并明确选择测试内容。

## 设计与阶段计划

- [系统设计](docs/superpowers/specs/2026-11-13-x-operations-design.md)
- [M1 实施计划与进度](docs/superpowers/plans/2026-11-13-m1-editorial-publishing.md)

后续通过外部适配器复用 yichen-skills 的搜索与读取能力；M1 无需安装它，也未复制其代码。该仓库声明非商业使用限制，商业接入前需核对授权。

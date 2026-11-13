# M1 内容审核与发布实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking. 用户已于本会话授权当前项目 commit、push。实现按当前会话顺序执行，关键发布流程另经独立只读审查。

**Goal:** 交付单账号“人工确认素材 → 指定语言真实生成 → 版本审核 → Postiz 排期 → 发布核对”的可运行闭环。

**Architecture:** FastAPI/Jinja2 负责审核交互；SQLite 保存版本、批准、任务与发布意图；独立 worker 执行有界网络动作。外部服务通过小型适配器接入，结果不明进入核对状态，避免盲目重试。

**Tech Stack:** Python 3.11+、FastAPI、Jinja2、HTTPX、SQLite、pytest。具体依赖版本在实现时依据当前官方兼容信息选定并锁定。

---

## 范围和完成标准

本计划仅实现设计中的 M1。M2 搜索、M3 指标与自动放行、M4 媒体和真实 Thread 发布各自单独计划。M1 不需要自动搜索、音视频 Runtime 或外部 Skill 才能完成。

可本地验收的能力：持久化草稿与批准、任务恢复、模拟外部服务下的 HTTP 契约与故障行为、审核页面。

真实联调的条件：模型服务配置、Postiz 服务、X 连接与用户指定测试内容。没有真实服务时保留联调项未完成，不将模拟传输视为真实发布。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| pyproject.toml、依赖锁文件 | 可安装应用、运行和开发依赖 |
| .env.example、.gitignore | 空值配置示例与私密产物排除 |
| app/config.py | 环境读取与类型检查 |
| app/db.py、app/migrations/001_initial.sql | 连接、事务、schema 版本与 M1 表 |
| app/accounts.py | 配置、暂停、数量限制 |
| app/sources.py | 人工确认的链接与正文 |
| app/drafts.py | 不可变版本、修改与批准 |
| app/publications.py | 发布意图和状态机 |
| app/jobs.py | 持久化任务领取、租约和恢复 |
| app/adapters/generator.py | 结构化模型输入输出和使用量 |
| app/adapters/postiz.py | 排期、查询、取消与错误映射 |
| app/worker.py | 任务分派与有界执行 |
| app/auth.py、app/main.py | 登录、会话、CSRF、路由 |
| app/templates/、app/static/ | 总览、素材、审核、日历和设置 |
| tests/ | 关键业务状态和外部契约验证 |

## 任务 1：应用基础与本地配置

- [x] 创建最小可安装包与本地启动入口，应用默认绑定 127.0.0.1。
- [x] 创建空值配置示例；数据目录与凭据文件默认排除出版本控制。
- [x] 配置缺失使用结构化状态显示；启动不能自动发起模型调用或发布。
- [x] 创建 GET /api/health，只报告本地健康状态，不返回密钥或服务原始错误。
- [x] 创建 tests/test_config.py 验证非法时区、缺少生成服务和数据根目录检查。

拟定配置键：

```dotenv
XOPS_DATA_DIR=./data
XOPS_BIND_HOST=127.0.0.1
XOPS_BIND_PORT=8000
XOPS_ADMIN_PASSWORD_HASH=
XOPS_SESSION_SECRET=
XOPS_LLM_BASE_URL=
XOPS_LLM_API_KEY=
XOPS_LLM_MODEL=
XOPS_POSTIZ_BASE_URL=
XOPS_POSTIZ_API_KEY=
XOPS_POSTIZ_INTEGRATION_ID=
```

验证命令：`python -m pytest tests/test_config.py -q`，预期非法配置被拒绝且无外部请求。使用临时目录，不写入真实运行目录。

## 任务 2：存储与内容版本

- [x] 实现 M1 迁移：accounts、sources、drafts、draft_versions、approvals、publications、jobs、events；后续表留到对应阶段迁移。
- [x] 编写 tests/test_editorial.py：修改内容生成新版本，旧版批准不能批准新版本。
- [x] 在测试先失败后实现创建素材、创建草稿、版本更新和批准事务。
- [x] PUT 内容接口必须提交 expected_version；版本冲突返回 409。
- [x] 批准事务比较 version_id 和服务端计算的 content_hash，不信任前端提供的哈希。

核心行为样例：

```python
def test_approval_does_not_follow_an_edit(editorial):
    source = editorial.add_source(url="https://example.com/release", title="Release", body="Release notes")
    draft = editorial.create_draft(source_ids=[source["id"]], language="en", column="news")
    v1 = editorial.save_version(draft["id"], expected_version=0, segments=["First draft"])
    editorial.approve(draft["id"], version_id=v1["id"])
    v2 = editorial.save_version(draft["id"], expected_version=1, segments=["Revised draft"])
    assert editorial.is_approved(v1["id"])
    assert not editorial.is_approved(v2["id"])
```

editorial 测试夹具在临时 SQLite 数据库中创建服务；对象 ID、版本号和批准查询由 app/drafts.py 提供。

验证命令：`python -m pytest tests/test_editorial.py -q`，预期旧批准不扩散、冲突可见、events 与变更同时落库。

## 任务 3：结构化生成

- [x] 阅读选定模型服务官方 API，记录经过验证的协议与固定模型配置，不假设所有服务协议相同。
- [x] 定义 Generator.generate(profile, sources, column, output_language) 返回 segments、source_ids、claim_refs、editor_notes、usage。
- [x] 使用 HTTPX 传输替身验证协议，不调用真实付费模型。
- [x] 编写 tests/test_generator.py：未知 source_id、空正文、非法结构和请求超时均不产生已批准草稿。
- [x] 实现服务端输入组装、来源长度限制、超时、输出校验与使用量记录；正文不参与工具执行。
- [x] 生成成功保存新版本并进入待审核；失败保存任务错误，可由用户明确重新生成。

验证命令：`python -m pytest tests/test_generator.py -q`，预期合法响应进入待审核，其余结果不伪造成功。

## 任务 4：发布意图与远端适配器

- [x] 对照实际 Postiz 版本的创建、列表、状态和取消文档，固定字段映射并保存脱敏响应夹具。
- [x] 创建唯一发布意图并检查账号、批准版本、带时区时间、每日上限与暂停状态。
- [x] 编写 tests/test_publications.py：重复排期得到同一有效意图；未批准和暂停账号被拒绝。
- [x] 编写 tests/test_postiz.py：读取远端 ID、鉴权失败、429、5xx 和超时映射。
- [x] 实现 PostizAdapter.schedule、inspect、cancel；只支持单条文字发布，Thread 返回明确的本阶段不支持结果。
- [x] scheduled 与 published 分别映射；真实平台 ID 缺失时不能生成猜测帖子链接。

发布幂等键使用服务端生成的账号、版本和目标摘要；它仅用于本地去重，不能在未经确认时假设远端支持 Idempotency-Key。

验证命令：`python -m pytest tests/test_publications.py tests/test_postiz.py -q`，预期重复意图被约束、结果不明映射为待核对。

## 任务 5：worker、核对与暂停

- [x] 实现 jobs 条件领取、租约、有限重试和重启扫描；一次执行一个有界动作。
- [x] 编写 tests/test_worker.py：两个领取者只有一个取得租约，历史 submitting 不直接再次提交。
- [x] 实现 generate_draft、submit_publication、reconcile_publication 三种任务。
- [x] 编写 tests/test_reconciliation.py：提交超时转待核对；核对唯一匹配恢复远端 ID；无匹配或多匹配均不盲目重发。
- [x] 实现暂停的事务检查以及远端取消任务；状态明确区分取消请求、已取消、已发布和取消结果不明。
- [x] 编写 tests/test_reconciliation.py：暂停拦截未提交任务，取消失败不能显示已取消。

关键验收场景：远端已经接受，但响应丢失；本地下一轮只能调用查询而不能再次创建。先以受控传输验证，再在真实服务允许的测试环境验证。

验证命令：`python -m pytest tests/test_worker.py tests/test_reconciliation.py tests/test_reconciliation.py -q`。

## 任务 6：审核界面与访问控制

- [x] 创建管理员登录、会话过期与 CSRF；业务 API 和表单共用身份校验。
- [x] 编写 tests/test_auth.py：未登录不能编辑/发布，缺少 CSRF 的写操作被拒绝。
- [x] 实现五个页面：总览、素材、草稿审核、发布日历、设置；M1 设置页明确指标功能未启用。
- [x] 使用 Jinja2 默认转义渲染来源和模型正文，避免把来源 HTML 当可信页面。
- [x] 审核操作显示版本号、语言、来源、时间和账号；批准后仍需明确排期动作。
- [x] 浏览器关键路径验证：录入素材、生成、改文、批准、排期、核对、暂停，确认错误和空状态可理解。

验证命令：`python -m pytest tests/test_auth.py tests/test_routes.py -q`。浏览器走本地模拟外部传输；没有真实配置时界面显示未配置。

## 任务 7：运行说明与关键验收

- [x] 更新 README，包含安装、配置、Web/worker 启动、Postiz 独立部署、备份和恢复说明。
- [x] 添加设计中确定的状态转换与超时窗口说明，明确 M1 不提供远端 exactly-once 保证。
- [x] 静态编译检查：`python -m compileall -q app`。
- [x] 只运行上述 M1 关键路径测试；记录执行结果与未验证项，不扩大成长耗时测试。
- [ ] 真实服务联调：待用户配置模型、Postiz 和 X 连接并明确测试内容后，执行一次真实生成和发布核对。
- [x] 检查 git diff 和私密文件排除项；按当前用户授权管理提交与推送；所有凭据与运行数据排除出版本控制。

## 设计覆盖核对

| M1 要求 | 对应任务 |
| --- | --- |
| 账号、语言、来源配置 | 1、2、6 |
| 真实生成与来源对应 | 3 |
| 不可变版本与审核 | 2、6 |
| Postiz 排期与单条发布 | 4 |
| 超时核对与防重复 | 4、5 |
| 暂停与取消差异 | 5 |
| 本机认证与 CSRF | 6 |
| 文档、恢复和真实联调边界 | 7 |

M1 本地实现已完成，真实服务联调未执行。34 项关键测试通过；浏览器已验证登录、素材录入、指定语言建稿、保存、批准以及改文后重新审核。依赖锁文件已生成。存在一项上游 Starlette/AnyIO 测试兼容弃用提示，不影响验证结果。
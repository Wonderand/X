# X 技术内容运营系统设计

日期：2026-11-13

状态：M1 已实现并通过本地验证，真实服务联调待配置；本文件区分已确认目标、建议默认值和运行前必填配置。

## 1. 目标与范围

已确认：面向技术领域，生产热点新闻与技术资讯；支持多语言。“技术资讯”不指技术咨询服务。第一版聚焦单账号和人工审核后的排期发布。

内容栏目：

| 栏目 | 输出 | 来源要求 |
| --- | --- | --- |
| 技术快讯 | 核心变化、影响、限制、来源 | 事件时间与原始发布链接可追溯 |
| 技术解读 | 原理、技术取舍、适用场景 | 事实与分析分开记录 |
| 实用发现 | 工具用途、使用场景、限制 | 项目仓库或官方文档优先 |

建议初始频率为每天 1～3 条，属于建议而非已确认业务规则。默认发布语言、时区、频率、目标读者和月度预算在账号启用时填写。系统支持空缺，不为填满排期生成无来源内容。

M1：来源录入、正文录入或经确认的正文读取、真实模型生成、版本审核、Postiz 排期、发布核对、暂停和故障恢复。

M2：受限搜索执行器、候选来源选择、事件合并、定时草稿生产。

M3：可用指标采集、周报、费用与调用控制、有限自动发布。

M4：音视频、图片切片、增强 Thread、回复建议。

## 2. 架构与所有权

采用模块化单体：FastAPI 与 Jinja2 提供审核界面，独立 worker 处理持久化任务。SQLite 是本系统业务状态的事实源。Postiz 管理远端排期和执行状态；两个系统通过远端 ID 与核对任务同步。

Web 请求仅校验、持久化、入队；外部读取、模型调用、远端发布由 worker 执行。Web 与 worker 使用不同 SQLite 连接，启用外键、WAL、busy timeout 和短事务。事务中不等待网络响应。

同一环境第一版运行一个 worker；数据库任务租约与条件更新防止意外重复进程同时领取任务。租约过期不代表外部请求没有成功，发布类任务必须先核对远端结果。

Postiz 由用户指定实例地址、服务端凭据和目标 integration ID。应用只对配置的目标账号创建任务，不让模型选择账号或 API 地址。

SQLite 保存账号、素材、草稿、审核、发布、任务和审计。正文、媒体、报告使用应用生成的相对路径保存到私有数据根目录。SQLite 在线备份与文件快照配套执行，不直接复制正在写入的数据库主文件。

## 3. 配置

配置分为运行配置和账号配置。

运行配置：数据目录、数据库路径、绑定地址、外部服务地址、凭据引用、请求超时和 worker 租约时间。

账号配置：技术主题、排除主题、读者、栏目、语气、允许语言、时区、可发布时间、每日发布上限、自动化模式。

语言使用标准语言标签，如 zh-CN、en、ja；每次生成必须明确 output_language。同一选题可以关联多个语言版本，但各版本单独审核、单独排期。新建账号不静默选择默认语言。

所有时间存 UTC；界面根据账号时区展示。北京时间仅作为可选配置，不能从开发电脑时区推断受众时区。

费用配置：调用次数限制、可计价服务的金额上限、不可计价服务的明确次数上限。未配置计费规则不能声称精确金额。外部调用前原子预留额度，完成后结算；结果不明的调用保留预留，待核对后释放。

## 4. 素材与证据

流程：搜索候选 → 事件合并 → 人工选择来源 → 正文读取 → 事实核验 → 可写作素材。

去重分三层：规范 URL/平台 ID 的来源去重；项目、版本、事件时间等辅助的事件合并；近期正文精确哈希与相似度提示。事件合并结果可人工拆分，不仅凭标题相近自动删除记录。

分别记录 occurred_at、published_at、retrieved_at，允许未知值。转述不能用采集时间补齐未知事件时间。

事实证据包含 claim_text、source_id、原文摘录或定位、检查状态和检查时间。已打开网页不等于事实已核验。数值保留测试条件，传闻和事实不能混为同一状态。

来源优先：官方公告、版本日志、项目仓库、原始论文和技术文档，其次原作者原帖与报道。不能自动把搜索摘要视为已核验正文。

来源中出现的提示词、命令、链接和指令均属于不可信资料，不参与执行策略。来源不能要求读取本机文件、改变凭据或直接发布。

## 5. 内容生成与版本

Generator 输入：账号写作配置、栏目、输出语言、已核验素材及选定来源。输出：segments、source_ids、claim_refs、editor_notes 和模型使用量。

模型只能提供结构化内容，不能批准或发布。响应校验来源引用、非空分段和输出语言；生成失败时保存可读错误，不用模拟正文冒充真实结果。

没有用户提供的实践资料时，不能编造“亲测”“我使用一个月”等经历。技术分析保存为观点，不能冒充来源结论。

draft 保存稳定 ID，draft_version 保存不可变内容、source_refs、content_hash、生成模型与时间。编辑、翻译或重新生成均创建新版本。批准绑定版本 ID 和内容哈希；改文、改媒体、改分段后重新审核。

长度校验根据已验证的目标账号与发布端能力配置；首版采用保守兼容规则并明确能力未知状态，不用 Python len() 代替 X 的加权长度规则。不能因为账号订阅未知就假设支持长帖。

## 6. 页面与动作

总览：待选来源、待审核、远端排期、结果待核对、失败、调用使用量。

选题/素材：录入公开链接和正文、查看来源时间、确认来源、查看事实与原文、合并/拆分事件。

草稿审核：选择栏目与语言、生成、编辑、查看版本差异、对照来源、批准、驳回、排期。

发布日历：显示本地和 Postiz 状态、查看真实帖子链接、取消排期、查看核对记录。M1 调整时间采用取消已确认后创建新排期，不假设 Postiz 提供任意原地更新接口。

设置与效果：账号配置、服务连接体检、暂停状态和后续周报。

M1 默认仅绑定本机回环地址。配置服务端管理员密码哈希，登录后使用 HttpOnly、SameSite 会话 Cookie，写操作校验 CSRF；配置 HTTPS 时设置 Secure。远程部署必须显式配置可信域名与 HTTPS，不能直接将无认证页面暴露公网。

## 7. 数据模型

| 表 | 关键字段与约束 |
| --- | --- |
| accounts | id、profile_json、timezone、mode、daily_limit、paused |
| sources | id、canonical_url、platform_id、body_path、时间字段、verification_status；来源键唯一 |
| topics | id、title、occurred_at、score、status |
| topic_sources | topic_id、source_id；组合唯一 |
| claims | id、source_id、text、locator、verification_status |
| drafts | id、account_id、topic_id、current_version_id |
| draft_versions | id、draft_id、version_no、language、segments_json、source_refs_json、content_hash；版本号组合唯一 |
| approvals | id、draft_version_id、content_hash、actor、approved_at；批准只对应此版本 |
| publications | id、account_id、draft_version_id、scheduled_at、status、remote_group_id、remote_post_ids、idempotency_key、last_error |
| jobs | id、kind、payload_json、status、attempts、run_after、lease_owner、lease_until、dedupe_key |
| metric_snapshots | publication_id、observed_at、metrics_json、unavailable_reason |
| events | id、entity_type、entity_id、event_type、actor、details_json、created_at |
| usage_records | id、job_id、provider、units_json、reserved_cost、settled_cost、status |

publications 的有效任务按 account_id + draft_version_id + target 去重。任务完成或取消后保留记录；重新发布必须显式创建新意图，不能自动绕过去重。

新增 schema 变更使用编号迁移，记录版本并在启动时检查兼容性；升级前备份，迁移失败不启动 worker。

## 8. 状态与外部副作用

草稿状态：generating → review_pending → approved；驳回为 rejected，修改产生新版本并回到 review_pending。

发布状态：ready → submitting → scheduled → published。明确拒绝进入 failed；网络中断、超时或 worker 崩溃后结果不明进入 reconciliation_required。

发送前在数据库事务中校验版本批准、账号暂停、数量上限并写入 submitting 与请求指纹；提交远端时不持有事务。成功后写入远端 ID；任务领取时遇到历史 submitting 必须先核对。

不承诺跨 SQLite 与 Postiz 的严格 exactly-once。若 Postiz 没有经过验证的幂等机制，远端成功、本地未保存的窗口无法仅靠本地唯一键消除。此时使用账号、排期时间、内容指纹与远端任务列表核对；无法唯一匹配时人工处理，绝不盲目重发。

远端 scheduled 不能视为 published。核对保存每段远端任务 ID、发布结果和平台帖子 ID。M1 支持单条文字发布；Thread 保留数据结构，真实发送在逐段查询/恢复能力验收后启用。

401/403 暂停相关发布连接并提示修复；429 按 Retry-After 延后；只对可确认未执行的暂时错误有限重试。创建请求的 5xx 也可能已产生副作用，必须先核对。具体连接错误类型由适配器映射。

账号暂停包含本地待提交任务与远端未来排期的处理。只有远端取消已确认才能显示“已取消”；已发出、取消失败或结果不明分别显示，暂停不承诺撤回已经发布的内容。

## 9. 后台任务

任务类型：generate_draft、submit_publication、reconcile_publication；M2 增加 discover_sources、read_sources、build_topics；M3 增加 collect_metrics、build_weekly_report。

领取采用条件更新并检查受影响行数，设置租约。任务每次执行只完成一个有界动作。大批量拆分成子任务，限制并发和超时。

重启恢复：过期生成任务可按重试上限恢复；发布任务先核对。错过时效窗口的资讯进入重新审核，不在重启后集中补发。

## 10. 指标与自动化

M3 在发布后配置的观察时间采样，比较栏目、主题、语言、时段。对比相同观察时长，样本不足时只报告事实，不做确定性结论。

metrics 的未知值为 null，并保存 unavailable_reason；零互动与无读取权限不能混淆。互动率标明实际分子分母，曝光缺失时不计算以曝光为分母的互动率。

自动化模式：辅助、审核后发布、有限自动发布。M1 只启用审核后发布；M3 有限自动发布只处理明确规则允许的已批准素材与栏目，新增新闻和异常仍进入审核。

周报建议不自动改写账号主题、预算、来源授权和发布权限。回复功能使用独立队列和开关，留至 M4。

## 11. 外部 Skill 与授权

外部 yichen-skills 通过配置路径和受限 subprocess 参数数组接入，不用 shell=True，不执行模型生成命令。

route_search.py 的 main 只调用 plan(request) 并打印计划，不能将计划 JSON 误当搜索结果。执行器逐个适配白名单后端；Grok 结果通过既有结果适配器验证原生搜索证据。

部分原生 CLI 路线没有统一候选输出，M2 仅启用实现并验证过的路线，其他路线显式标记 unsupported。缺少可选依赖不会自动安装或升级。

现有归档 Skill 要求候选当轮选择；系统不得自行写入 confirmed 标记。M1 人工录入/选择来源；M2 仍保留确认入口。无人值守跨阶段采集需独立定义来源范围与执行契约，不能通过包装绕过 Skill 的确认要求。

私人收藏、登录态回退、媒体下载和付费调用分别核对授权。自动发布配置不会扩大这些范围。

源仓库声明非商业使用限制；商业或公司用途需先核对授权。外部 Runtime 与私人配置不进入本仓库。

## 12. 接口契约

以下均为本应用拟实现的内部接口，不是对 Postiz 路径的假设。

| 方法与路径 | 行为 |
| --- | --- |
| POST /api/sources | 录入链接、正文与来源时间，返回 source_id |
| POST /api/drafts | 明确 source_ids、column、output_language，创建生成任务 |
| PUT /api/drafts/{id}/content | 校验 expected_version，新建内容版本 |
| POST /api/drafts/{id}/approve | 校验 version_id、content_hash，保存批准记录 |
| POST /api/publications | 校验批准版本和带时区排期，创建唯一发布意图 |
| GET /api/publications/{id} | 返回本地状态、远端状态及已核对链接 |
| POST /api/publications/{id}/reconcile | 入队核对，不直接重复创建 |
| POST /api/publications/{id}/cancel | 请求取消并跟踪远端确认 |
| POST /api/accounts/{id}/pause | 暂停本地提交并入队处理远端排期 |
| GET /api/health | 不泄露配置的本地健康状态 |

乐观并发冲突返回 409，未配置服务返回明确的连接未配置结果，不能伪造业务成功。业务动作与 events 在同一事务保存。

## 13. 运行与验收

开发阶段本机运行应用，Postiz 单独部署。持续运营部署到常开环境；网页进程与 worker 分开启动并由进程管理器重启。Postiz 按其官方文档维护自己的依赖，本项目数据库不与其共享。

凭据不进入日志、API 响应、Git 或生成提示词。读取公开 URL 时限制 HTTPS、重定向、大小和超时，拒绝本机、私网、链路本地地址并对每次重定向重新校验；M1 可通过人工正文录入先避免通用 URL 抓取器。

M1 必要验证：批准版本绑定、并发去重、提交超时转待核对、重启不盲目重发、暂停与远端取消区分、身份认证/CSRF。只运行相关关键测试与静态检查。

真实联调需要用户配置模型、Postiz、X 账号和发布权限，并指定测试内容。未取得这些条件时只能声明本地验证通过，不能声明真实发布完成。

## 14. 分阶段验收

M1：一条人工确认的技术素材能生成指定语言草稿，审核后排期，回收真实帖子 ID；失败可见且不会重复发送。

M2：定时执行有限搜索，来源可追溯，候选不会未经选择进入要求确认的归档流程；每次覆盖和失败均可见。

M3：真实指标有时间快照，预算限制生效，规则自动化可暂停，周报建议可审阅。

M4：媒体和 Thread 能独立核对恢复；回复建议与原帖队列分开。

## 15. 外部资料

- Postiz 源码：https://github.com/gitroomhq/postiz-app
- Postiz API：https://docs.postiz.com/public-api/introduction
- Postiz X 接入：https://docs.postiz.com/self-host/providers/x-twitter

外部接口按实际部署版本核对，部署时固定经过验证的版本，不跟随未经验证的 main 自动升级。

## 16. M1 实现说明

M1 来源正文保存在 SQLite，便于与版本引用、备份保持一致；媒体文件仍留给后续阶段。初版只发送单条文字，Thread 可编辑但不发送。固定依赖见 requirements.lock。核对任务按排期后 72 小时观察窗口运行，连续失败与正常轮询独立计数；取消确认后允许以新时间创建新意图。同一时间的已取消意图不会自动复活。

用户已授权当前 X 项目执行 commit 和 push，覆盖此前禁止提交的约定。真实发布与付费模型联调仍需服务配置和明确的测试内容。

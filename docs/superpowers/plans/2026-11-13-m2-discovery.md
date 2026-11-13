# M2 发现与候选审核实施计划

执行：使用 executing-plans，按以下任务顺序实施；已批准范围为系统设计 M2。用户允许当前 X 项目 commit 和 push。代码与测试仅在 D:/project/X 中修改。

- [x] 1. 新增迁移 003：发现设置、规则、运行、候选、分组、命中记录、额度和正文读取记录；验证 M1 数据升级保留。
- [x] 2. 在 app/discovery.py 实现规则、UTC 日原子额度、手动运行与定时入队、候选去重分组、人工确认及草稿交接；先写 tests/test_discovery.py。
- [x] 3. 在 app/adapters/discovery.py 实现固定路由/脚本白名单、子进程隔离、有界 JSON 契约及 AnySearch 有签名原文读取；先写 tests/test_discovery_adapter.py。
- [x] 4. 在 app/discovery_worker.py 实现任务执行、结果持久化和错误恢复；通过最小扩展接入现有 worker；先写 tests/test_discovery_worker.py。
- [x] 5. 在 app/discovery_routes.py 注册独立 HTML/API/表单路由，新增发现、候选和确认页面；复用页面与认证辅助函数；先写 tests/test_discovery_routes.py。
- [x] 6. 更新配置示例、README 与验证记录，静态检查、新增关键测试、一次 M1 回归、浏览器验证。
- [x] 7. 独立只读审查关键限额与任务路径，修复已复现问题；开发分支提交与推送结果以交付记录为准。

必要命令：.venv/Scripts/python.exe -m pytest tests/test_discovery*.py -q；.venv/Scripts/python.exe -m compileall -q app。测试临时目录明确位于 D:/project/X/.test-tmp，不使用 C 盘开发副本。

真实外部搜索/付费原文提取仅在用户配置运行时与调用上限后执行；本次验收不把替身协议验证视为真实服务联调。

入口修改授权：用户已明确允许 app/main.py、app/worker.py 接入，以及 base.html、dashboard.html 导航展示修改。既有发布状态机保持原样。

本地验收：58 项检查通过，浏览器验证完成。真实服务联调待配置；详细结果见 docs/verification-m2.md。

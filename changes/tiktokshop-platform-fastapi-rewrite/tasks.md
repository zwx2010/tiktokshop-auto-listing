# 实施任务

## Delivery / Proof Map

| 规格 | 交付物 | 证明 |
|---|---|---|
| MySQL 持久化、迁移 | MySQL 配置、Alembic schema、迁移 CLI | MySQL 集成测试与迁移报告 |
| 分层 FastAPI | application/domain/infrastructure/api 目录与依赖注入 | 单元测试、OpenAPI 检查 |
| 完整业务能力 | 业务服务和集成适配器迁移 | 核心流程测试与真实适配器验证 |
| 任务并发控制 | 任务租约、重试、幂等和日志 | 并发/故障恢复测试 |
| API 重设计 | 版本化资源 API、看板/CLI/回调适配 | OpenAPI 快照和端到端测试 |
| 可观测性与安全 | 健康检查、结构化日志、审计和验签 | 安全测试与启动检查 |

## Tasks

1. **建立基线与迁移前检查**（依赖：无；范围：`app/`、`docs/`、`data/`）
   - 盘点现有路由、脚本入口、状态机、表结构、外部集成和真实数据量；补充可重复的基线检查报告。
   - 证据：基线命令输出、路由清单、SQLite 表/行数清单、现有测试结果。

2. **建立 MySQL schema 与数据库基础设施**（依赖：1；范围：`app/infrastructure/db/`、`migrations/`、`config/`）
   - 落地 SQLAlchemy 2.x Repository/Unit of Work、连接池、事务、迁移版本、健康检查和生产 SQLite 拒绝逻辑；补齐目标业务表、索引、外键和定点金额字段。
   - 证据：空 MySQL 初始化、升级/降级检查、连接失败测试和 schema 校验。

3. **实现 SQLite → MySQL 全量迁移工具**（依赖：2；范围：`tools/migrate_sqlite_to_mysql.py`、`app/migration/`）
   - 提供预检、批量导入、检查点、幂等、冲突报告和行数/关联校验；保留原 SQLite 只读，不修改输入。
   - 证据：同一输入重复执行结果一致，迁移报告和关键表校验通过。

4. **重构领域模型和应用服务**（依赖：2；范围：`app/domain/`、`app/application/`）
   - 抽取账号、商品、定价、Listing、审批、任务状态机；所有状态变更通过服务和事务完成，外部依赖使用端口。
   - 证据：不启动 FastAPI/MySQL 的服务单元测试、状态转移测试、幂等测试。

5. **重写 API 与生命周期管理**（依赖：4；范围：`app/api/`、`app/main.py`、`app/schemas/`）
   - 设计版本化 REST API、统一错误/分页/任务响应、依赖注入和 lifespan；将 API、worker、迁移命令分成明确启动模式。
   - 证据：OpenAPI 快照、API 集成测试、API-only 启动不创建后台轮询线程。

6. **迁移采集、清洗、定价、文案、质检和导出**（依赖：4、5；范围：`app/pipeline.py`、`app/pricing.py`、`app/cleaning.py`、`app/rag/`、`app/excel_export.py`、`tools/`）
   - 将现有脚本直接 SessionLocal 调用改为应用服务/CLI；保持真实数据规则、文案缺失阻断和导出格式。
   - 证据：采集样本到 staging 的测试、RAG 规则测试、Excel/CSV 输出校验。

7. **迁移飞书、多维表、审批和 CDP 适配器**（依赖：4、6；范围：`app/integrations/feishu/`、`app/integrations/cdp/`、`app/agent/`）
   - 以可替换适配器重接回调验签、审批卡、多维表领取、单批次互斥、CDP 提交和结果回写；保留人工兜底。
   - 证据：回调验签测试、审批状态机测试、模拟适配器全链路测试、真实环境冒烟记录。

8. **迁移看板、启动器和运维文档**（依赖：5、6、7；范围：`app/templates/`、`scripts/launcher.py`、`README.md`、`docs/`）
   - 更新看板调用新 API，提供 API/worker/migrate/health 启动方式、MySQL 配置示例、备份和回滚说明。
   - 证据：新机器部署演练、健康检查、看板核心操作和启动器检查。

9. **全量验证与切换**（依赖：3、5、6、7、8；范围：测试与部署配置）
   - 执行单元、集成、迁移、并发、回归和真实适配器验证；生成切换清单和迁移报告，确认 MySQL 成为唯一运行时主库。
   - 证据：完整测试报告、迁移校验报告、OpenAPI/调用方清单、部署演练记录。

## Dependency Order

`1 → 2 → 3`；`2 → 4 → 5`；`4 → 6 → 7`；`5/6/7 → 8`；`3/5/6/7/8 → 9`。

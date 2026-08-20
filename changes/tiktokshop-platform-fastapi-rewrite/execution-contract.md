# 执行契约：TikTokShop Platform Lite FastAPI 分层重构

## Intent Lock

在保留完整 TikTok Shop 运营闭环的前提下，将现有 FastAPI 单体重构为分层模块化单体 + 独立 worker，并把 MySQL 作为唯一运行时业务数据库，完整迁移现有 SQLite 数据；API 可以重新设计，但看板、脚本和外部集成必须同步迁移并通过业务验证。

## Scope Fence

### In Scope

- API、应用服务、领域状态机、Repository/Unit of Work、任务 worker、迁移工具和测试体系。
- MySQL 8.0/InnoDB/utf8mb4 schema、迁移版本、连接池、事务、健康检查。
- SQLite 全量业务数据迁移、幂等重跑、检查点、关联/唯一键校验和报告。
- 商品、账号、SKU、Listing、采集入库、清洗定价、三语文案/RAG、图片质检、审核、飞书、多维表、CDP、导出和看板。
- 启动器、配置、部署文档和 API 调用方。

### Out of Scope

- 新增第三方平台或新的商品类目。
- 长期保留旧 API 兼容层。
- SQLite 作为生产业务读写后备库。
- 将 RAG 独立索引强行并入 MySQL，除非实现阶段验证其为业务一致性前提。

## Approved Behavior

1. 业务运行时必须连接 MySQL；生产配置指向 SQLite 时启动失败。
2. schema 变化通过迁移工具管理，不再依赖 FastAPI 启动时 `create_all`/补列。
3. HTTP 路由只做输入校验、鉴权/验签、服务调用和响应转换；不直接编排 SQL、线程或第三方请求。
4. API、CLI、飞书回调和 worker 共享应用服务、事务和幂等规则。
5. 长任务持久化到任务表，具备状态、租约/领取控制、重试、步骤日志、人工等待和失败恢复。
6. 采集到上架、审批拒绝、文案缺失、图片质检失败和 CDP 失败等现有业务分支必须保留。
7. SQLite 迁移只读输入，支持重复执行、检查点恢复和机器可读校验报告。
8. 飞书回调继续验签；敏感配置不写入源码；关键状态变化写审计记录。

## Architecture and Dependency Constraints

- 采用模块化单体，不拆成微服务。
- `api` 依赖 `application`；`application` 依赖 `domain` 和 ports；基础设施/外部集成实现 ports，不反向污染领域层。
- 数据访问统一经过 Repository/Unit of Work；禁止新代码直接导入全局 `SessionLocal`。
- 金额使用定点数；时间统一存储约定；表使用外键、唯一键和必要索引保证幂等。
- worker 与 API 可独立启动、停止和健康检查；API-only 模式不得隐式启动无限轮询线程。
- 真实 CDP/飞书适配器和测试替身分离；外部失败必须转换为可分类、可重试或需人工处理的错误。

## Execution Batches

### Batch 1 — Baseline and MySQL Foundation

**范围**：任务 1-3；`app/infrastructure/db/`、`migrations/`、`app/migration/`、`tools/migrate_sqlite_to_mysql.py`、配置和基线报告。

**完成定义**：MySQL 空库可初始化；目标 schema、索引、外键和金额类型通过检查；SQLite 迁移可预检、导入、重跑、恢复并生成行数/关联/冲突报告。

**验证证据**：MySQL 集成测试、迁移重复执行测试、异常恢复测试、迁移报告。

### Batch 2 — Domain and Application Services

**范围**：任务 4；`app/domain/`、`app/application/`、Repository/ports。

**完成定义**：账号、商品、定价、Listing、审批和任务状态机从路由/脚本中抽出；核心状态变更拥有事务和幂等规则；服务可脱离 FastAPI 与外部平台测试。

**验证证据**：领域状态机、服务单元测试、非法状态转换、重复请求和事务回滚测试。

### Batch 3 — API and Lifecycle

**范围**：任务 5；`app/api/`、`app/schemas/`、`app/main.py`、启动入口。

**完成定义**：版本化 API、统一分页/错误/任务响应、依赖注入和 lifespan 完成；API、worker、migration、health 启动边界清晰。

**验证证据**：OpenAPI 快照、API 集成测试、API-only 不启动轮询、MySQL 不可用启动预检。

### Batch 4 — Core Pipeline and Integrations

**范围**：任务 6-7；采集、清洗、定价、RAG、质检、导出、飞书、多维表、审批、CDP。

**完成定义**：所有旧业务能力通过应用服务和适配器恢复；文案缺失阻断、审图门、审批拒绝、单批次互斥和 CDP 结果回写均可验证。

**验证证据**：核心流程测试、模拟适配器全链路测试、飞书验签测试、真实环境冒烟记录、导出文件校验。

### Batch 5 — Consumers, Operations, and Cutover

**范围**：任务 8-9；看板、脚本、启动器、文档、部署和切换清单。

**完成定义**：看板和 CLI 使用新 API/服务；新机器可部署；迁移报告、备份/回滚方案、完整测试和 MySQL-only 运行检查通过。

**验证证据**：部署演练、健康检查、看板操作、全量回归、并发/恢复测试、最终迁移校验报告。

## Test Obligations

- 每个 SHALL/MUST 需求至少有自动化测试或真实适配器验证证据。
- MySQL 集成测试覆盖 schema、事务、唯一键、外键、定点金额、JSON 和时间字段。
- 迁移测试覆盖空库、完整库、重复执行、冲突、断点恢复和数据关联。
- worker 测试覆盖并发领取、租约过期、重试上限、人工等待和幂等。
- 外部集成测试覆盖飞书挑战/验签/审批、CDP 成功/失败、RAG 规则和导出格式。
- 切换前必须执行全量测试、健康检查、迁移报告和关键业务链路冒烟。

## Review Gates

- DP-3：本契约批准前不得实现。
- 每个批次完成后进行规格符合性、代码质量、测试证据和数据安全检查；未通过不得进入依赖批次。
- Batch 1 的迁移报告和 Batch 4 的真实链路证据是切换前强制证据。
- 任一需求、数据库范围、外部集成边界或批次顺序发生变化，必须退回规格/设计重新评审。

## Escalation and Rewind Rules

- MySQL schema 无法表达现有数据、迁移出现不可接受的数据丢失或外键冲突：暂停实现，回到设计与迁移规格。
- 发现未覆盖的业务分支、外部回调或调用方：暂停当前批次，补充 specs/tasks 后再继续。
- 任务重复执行、审批绕过、验签失效或 CDP 重复上架：进入调试路径，修复并重新验证相关批次。
- 真实环境依赖缺失时，可以完成替身测试，但不得把替身结果作为全链路完成证据。

## Handoff

DP-3 需要用户明确批准本执行契约。批准后进入执行模式选择和批次计划；未批准不得修改业务代码。

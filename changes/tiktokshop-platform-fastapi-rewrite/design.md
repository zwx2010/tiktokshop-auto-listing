# 设计：FastAPI 分层重构与 MySQL 迁移

## Relevant Facts and Constraints

- 当前入口为 `app.main:app`，启动事件同时初始化数据库、注册飞书处理器并启动多维表轮询线程。
- 业务模型已在 `app.models` 中定义，但现有数据库初始化依赖 `create_all` 和启动时补列。
- 路由、脚本和工具直接导入 `SessionLocal`；RAG 向量索引当前独立使用 SQLite 文件。
- `docs/02_database_schema.md` 已描述 MySQL 8.0、19 张业务表和 Alembic 方向，可作为目标 schema 基线。
- 用户已确认：分层重构、保留全部现有能力、MySQL 唯一运行时读写、完整迁移 SQLite、允许 API 重新设计。

## Goals

- 让 HTTP、任务、领域业务、数据库和第三方集成拥有可独立测试的边界。
- 在不缩水业务闭环的前提下，建立可迁移、可恢复、可审计的 MySQL 数据层。
- 让 API 和 worker 都通过同一组应用服务执行状态变更和幂等规则。

## Non-goals

- 不增加新的外部平台。
- 不保留旧 API 兼容层作为长期设计目标。
- 不把 RAG 语料索引强行并入业务 MySQL，除非验证后发现其数据一致性要求需要这样做。

## Decisions

### 1. 采用分层模块化单体

- **Choice**：一个 FastAPI 部署单元配合独立 worker 进程；内部拆分 API、application、domain、infrastructure、integrations 和 jobs。
- **Rationale**：现有能力高度共享数据库和流程状态，先拆成微服务会扩大迁移面和运维成本；模块化单体能完成边界治理并保留单库事务。
- **Alternatives**：保持现有单体入口会继续放大线程和导入耦合；直接微服务化需要跨服务一致性和更多部署组件。
- **Consequences**：需要重写导入路径和依赖注入，但可逐模块替换，测试可绕过 HTTP 和外部平台。

### 2. 采用 MySQL + Alembic 迁移

- **Choice**：MySQL 8.0/InnoDB/utf8mb4，SQLAlchemy 2.x，Alembic 管理 schema，环境变量提供连接串。
- **Rationale**：项目已有 MySQL Schema 文档；显式迁移可以替代 `create_all`/启动补列，支持回滚检查和部署审计。
- **Alternatives**：继续 `create_all` 无法安全演进；同时支持 SQLite/MySQL 作为运行时主库会增加方言和测试组合。
- **Consequences**：开发环境需要 MySQL 或容器化测试实例；迁移工具必须先完成数据备份、预检和校验。

### 3. 统一应用服务与任务命令模型

- **Choice**：API、CLI、飞书回调和 worker 均调用同一应用服务；任务以 MySQL 记录为事实来源，使用租约/版本号/幂等键控制领取。
- **Rationale**：当前脚本和路由各自操作 SessionLocal，容易产生行为分叉和重复执行。
- **Alternatives**：仅把路由改成异步而不统一服务边界，不能解决外部集成和 CLI 的重复逻辑。
- **Consequences**：需要迁移现有脚本调用点，并为长任务增加可观察的步骤和恢复语义。

### 4. API 重新设计而非长期兼容

- **Choice**：建立版本化资源 API、统一分页/错误/任务响应，同时同步修改看板、脚本和回调调用方。
- **Rationale**：用户明确允许 API 重新设计；借此去除当前扁平路由和隐式状态变更。
- **Alternatives**：保留旧路由可降低短期改动，但会固定旧模型和不清晰的副作用。
- **Consequences**：切换批次必须同时更新调用方，并提供迁移说明和端到端验收。

### 5. 外部集成采用适配器与可替换端口

- **Choice**：飞书、CDP、RAG、图片质检和导出分别通过 ports/adapters 接入应用服务；真实适配器与测试替身分离。
- **Rationale**：保证核心状态机可离线测试，同时保留真实运行能力。
- **Alternatives**：在领域代码中直接调用 requests、浏览器端口和文件系统，会让测试依赖外部环境。
- **Consequences**：需要定义稳定的输入/输出 DTO 和错误分类；真实环境测试仍需保留。

## Risks and Verification Evidence

| 风险 | 验证证据 |
|---|---|
| SQLite 与 MySQL 类型、默认值、JSON、时间字段差异 | 迁移前后行数/关联/唯一键报告，MySQL 集成测试 |
| 任务重复领取导致重复审批或上架 | 并发领取测试、幂等键测试、任务日志审计 |
| 重构丢失复杂审批和 CDP 分支 | 现有流程分支盘点、状态机测试、真实沙箱链路记录 |
| API 重设计遗漏看板或脚本调用方 | OpenAPI 快照、调用方清单、看板/CLI/回调端到端测试 |
| MySQL 不可用时服务误启动 | 启动预检、健康检查和故障演练 |

## Migration Boundary

业务库迁移包含 `accounts`、`keywords`、`products`、`product_skus`、`listings`、`tasks` 及 schema 文档中定义的扩展表。`data/rag_corpus.db` 作为独立索引资产单独校验，不与业务表混迁；如果重构后的 RAG 需要持久化语料，会通过独立适配器决定存储实现。
